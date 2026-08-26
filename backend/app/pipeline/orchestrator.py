"""
Detection pipeline orchestrator.
================================================================

Owns the sequencing of the detection stages and the fusion of their
results into one verdict. Route handlers call `analyze()`; so does the
evaluation harness, directly, with no HTTP involved.

That second caller is the reason this module exists as a separate
layer rather than living inside the route. The Week 3 harness runs the
whole pipeline over a labelled corpus a few hundred times to produce
precision, recall and false-positive rates. Doing that through HTTP
would mean running a server, paying network overhead per sample, and
parsing JSON back into objects — slow enough that you stop rerunning
it, and the numbers go stale.

Sequence, as of Week 5:

    regex -> entropy -> semantic -> risk fusion -> redaction

Each stage is skippable and each is measured separately, which is what
lets the report say what every one of them is worth rather than
asserting that the pipeline as a whole works.
"""

import time
import uuid
from dataclasses import dataclass

from ..core import behaviour, conversation, redactor, risk
from ..core.policy import DEFAULT_POLICY, RiskPolicy
from ..detectors import entropy_detector, ocr, pdf, regex_detector, semantic_detector
from ..detectors.base import Finding, highest_severity, resolve_overlaps
from ..detectors.ocr import OcrResult
from ..detectors.pdf import PdfResult
from ..schemas.analyze import (
    Action,
    AnalyzeRequest,
    AnalyzeResponse,
    DetectedEntity,
    OcrInfo,
    PdfInfo,
    RiskLevel,
)

@dataclass(frozen=True)
class StageResult:
    """What the detection stages concluded, before HTTP gets involved."""

    action: Action
    level: RiskLevel
    score: int
    findings: list[Finding]
    stages_run: list[str]
    reasons: tuple[str, ...] = ()
    """Human-readable justifications from the risk engine.

    A verdict nobody can explain is a verdict nobody will trust.
    """


def run_stages(
    text: str,
    *,
    use_entropy: bool = True,
    use_semantic: bool = True,
    policy: RiskPolicy = DEFAULT_POLICY,
    behaviour=None,
    primed: frozenset[str] = frozenset(),
) -> StageResult:
    """Run the detection stages and fuse them into a verdict.

    Extracted from `analyze()` so the API route and the evaluation
    harness call THE SAME CODE. That is the point: a harness that
    reimplements the stage logic measures something that never runs in
    production, and the two drift apart without either failing.

    The `use_*` switches exist for the harness, which needs to compare
    configurations: regex only, Stage 1, Stage 1 + 2. The report's
    central claim is a DIFFERENCE between them, so producing those
    configurations has to be a first-class capability rather than a
    line someone comments out by hand.

    :param text: content to inspect
    :param use_entropy: run Stage 1b (entropy)
    :param use_semantic: run Stage 2 (Presidio NER)
    :param policy: risk-scoring opinions; the harness varies this to
        report what a threshold change would actually cost
    :param primed: context words carried from earlier messages in the
        same conversation. Empty on every path except a live request,
        so the corpus keeps measuring single-message detection — the
        same discipline D49 applies to the behavioural signal
    """
    stages_run: list[str] = ["regex"]

    # ---- Stage 1a: pattern detection ----------------------------
    # Sub-millisecond and near-perfect on structured secrets, so it
    # always runs first.
    findings: list[Finding] = regex_detector.scan(text, primed)

    # ---- Short-circuit ------------------------------------------
    # A critical regex hit is already certain and already blocking.
    # No later stage can change the verdict, so running them would buy
    # nothing and cost real latency — Stage 2's spaCy inference alone
    # is roughly 50ms, against ~0.04ms for everything so far.
    #
    # `stages_run` records what actually executed, which turns this
    # optimisation into evidence rather than a claim.
    critical_already = highest_severity(findings) == "critical"

    if use_entropy and not critical_already:
        # ---- Stage 1b: entropy ----------------------------------
        # Catches secrets whose format nobody has published. Weak on
        # its own — see the detector's docstring — so it is capped at
        # `medium` severity and can warn but never block.
        stages_run.append("entropy")
        findings = resolve_overlaps(findings + entropy_detector.scan(text))

    if use_semantic and not critical_already:
        # ---- Stage 2: semantic entities --------------------------
        # PERSON, ORGANIZATION, LOCATION — categories no pattern can
        # reach, because the answer depends on surrounding words
        # rather than the shape of the token.
        #
        # ~200x the cost of Stage 1 (2.8ms vs 0.014ms), which is why
        # it sits behind the same short-circuit.
        stages_run.append("semantic")
        findings = _merge_semantic(findings, semantic_detector.scan(text))

    # ---- Fusion --------------------------------------------------
    # Where "what did we find" becomes "what should happen". Kept in
    # its own module because it is the one part of this pipeline whose
    # correct answer is an organisational choice rather than a
    # technical fact — see core/policy.py.
    assessment = risk.assess(findings, text, policy, behaviour)

    return StageResult(
        action=assessment.action,
        level=assessment.level,
        score=assessment.score,
        findings=findings,
        stages_run=stages_run,
        reasons=assessment.reasons,
    )


def _merge_semantic(stage1: list[Finding], semantic: list[Finding]) -> list[Finding]:
    """Add Stage 2 findings without letting them displace Stage 1 ones.

    NOT `resolve_overlaps(stage1 + semantic)`, and the difference cost
    five false negatives before it was caught.

    `resolve_overlaps` ranks by confidence, which is correct when two
    detectors make COMPETING CLAIMS about what the same characters are
    — an Aadhaar number or a payment card, but not both. Confidence is
    the right arbiter there because the question is about truth.

    Stage 2 is not making a competing claim. spaCy labels
    `Kp3Vx9Nm2Qr7Ts5Yw1Bz8Ld4Hg6Jf0Ac` as a PERSON at 0.85 confidence
    because a random token in that sentence position looks like a name
    to it. That 0.85 is not comparable to our 0.85 for "this looks
    random" — the two numbers answer different questions — and letting
    the semantic label win meant a `low`-severity guess suppressed a
    `medium`-severity secret and the message was allowed through.

    The principle: **Stage 1 is authoritative on the spans it has
    already classified.** Semantic findings are additive context, and
    survive only where Stage 1 claimed nothing. If our detectors
    already know what a span is, spaCy's opinion about it is noise.

    :param stage1: findings from regex and entropy, already resolved
    :param semantic: findings from Presidio
    """
    kept = list(stage1)
    kept.extend(f for f in semantic if not any(f.overlaps(existing) for existing in stage1))

    return sorted(kept, key=lambda f: f.start)


def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """Run the detection pipeline over one submission.

    Deliberately synchronous. The detectors are CPU-bound — regex,
    entropy, spaCy inference — so `async def` would buy nothing and
    would actively mislead: an async function that never awaits blocks
    the event loop for its whole duration, which is worse than being
    honest about it. FastAPI runs plain `def` handlers in a thread
    pool, which is the correct treatment for CPU-bound work.

    :param request: validated payload from the extension
    :returns: the verdict, including timing and which stages ran
    """
    started = time.perf_counter()

    # A correlation id generated per request and threaded through the
    # log line, the response and (from Week 5) the database row. When
    # a user reports "it blocked something it shouldn't have", this is
    # what turns three separate systems into one traceable story.
    request_id = uuid.uuid4().hex[:12]

    # ---- Stage 4: behavioural baseline --------------------------
    # Computed here rather than inside run_stages, because the
    # evaluation harness calls run_stages directly with no user
    # history — the corpus is a set of isolated messages. Threading the
    # signal in at THIS level keeps the measured numbers about content
    # detection and lets the live path use both.
    #
    # A first pass without behaviour tells us what was found; the
    # signal then depends on those entity types, so the fusion runs
    # twice. Cheap, because no detector runs the second time.
    # ---- Conversation priming -----------------------------------
    # Context words carried from earlier messages in this conversation.
    # Read BEFORE analysis and recorded AFTER it, so a message can
    # never prime itself — the same rule the Stage 1 gates follow by
    # excluding a match from its own context window (D29).
    #
    # `take` rather than a getter: reading spends one of the three
    # messages a mention supports.
    primed = conversation.take(request.conversation_id)

    preliminary = run_stages(request.text, primed=primed)

    signal = behaviour.signal_for(
        request.user_id,
        char_count=len(request.text),
        entity_types=frozenset(f.type for f in preliminary.findings),
    )

    result = (
        preliminary
        if signal.is_neutral
        else run_stages(request.text, behaviour=signal, primed=primed)
    )

    # Recorded after the verdict is settled. Only words from the closed
    # vocabulary in `regex_detector` ever reach the store — never the
    # message, and never a fragment of it.
    conversation.note(
        request.conversation_id,
        regex_detector.context_vocabulary_in(request.text),
    )
    action, level, score = result.action, result.level, result.score
    findings, stages_run = result.findings, result.stages_run

    # ---- Convert internal findings to the API shape -------------
    # This is where `value` is dropped. Findings carry the matched
    # text because the entropy stage scores it and Week 5's redaction
    # replaces it; the response carries offsets only, because a reply
    # containing the secret would put a copy in every HTTP log and
    # proxy cache between here and the browser.
    entities = [
        DetectedEntity(
            type=f.type,
            start=f.start,
            end=f.end,
            score=f.score,
            detector=f.detector,
        )
        for f in findings
    ]

    message = _describe(findings, action)

    # ---- The one place text travels back ------------------------
    # Populated only for `redact`, and only because what goes back is
    # strictly SAFER than what the client already holds. Building it
    # unconditionally "in case the UI wants it" would put a lightly
    # transformed copy of every prompt into every HTTP log, which is
    # the exposure this project exists to prevent.
    # Strips exactly `risk.removable()` — the same set the decision to
    # offer redaction was based on. Passing every finding instead would
    # deliver a different message from the one that was approved, and
    # would remove the names that the residual-score check had already
    # judged safe to keep.
    redacted_text = (
        redactor.redact(request.text, risk.removable(findings))
        if action is Action.REDACT
        else None
    )

    # Measured around the detection work only, excluding FastAPI's
    # request parsing and response serialisation. That keeps the number
    # comparable across the regex-only baseline and the full pipeline —
    # which is precisely the comparison the report has to make.
    latency_ms = (time.perf_counter() - started) * 1000

    return AnalyzeResponse(
        request_id=request_id,
        action=action,
        level=level,
        score=score,
        entities=entities,
        message=message,
        redacted_text=redacted_text,
        latency_ms=round(latency_ms, 3),
        stages_run=stages_run,
        reasons=list(result.reasons),
    )


def analyze_image(
    image_bytes: bytes,
    *,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> tuple[AnalyzeResponse, OcrResult]:
    """Read an image, then run the ordinary pipeline over what it says.

    Stage 3 is a text SOURCE, not a detector (D41), so the only thing
    that differs from `analyze()` is where the text came from. Every
    rule, threshold and policy below this line is the same one that
    handles typed input — which means every improvement to Stage 1
    improves image handling for free.

    Returns the OCR result alongside the response so the caller can
    record its METADATA. Deliberately not folded into the response: the
    extracted text must reach the detectors and nothing else, and
    returning it separately keeps the route's access to it explicit
    rather than incidental.

    :param image_bytes: the raw upload
    :raises ValueError: on an unusable image
    :raises RuntimeError: when Tesseract is not installed
    """
    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]

    extracted = ocr.extract_text(image_bytes)

    # An image with no legible text is not a pass — it is an absence of
    # evidence. Returning `allow` here is correct, and the OCR
    # confidence travels back so the user can see the difference
    # between "we read this and it was fine" and "we could not read
    # this". Treating them as the same thing is how a blurry screenshot
    # becomes a bypass.
    result = run_stages(extracted.text, policy=policy) if extracted.text.strip() else None

    if result is None:
        action, level, score = Action.ALLOW, RiskLevel.LOW, 0
        findings, stages_run, reasons = [], ["ocr"], ()
    else:
        action, level, score = result.action, result.level, result.score
        findings, reasons = result.findings, result.reasons
        stages_run = ["ocr", *result.stages_run]

        # ---- Redaction is not available for images ---------------
        # The risk engine works on text and will happily return
        # `redact`, because from its point of view a secret sits inside
        # a sentence that survives its removal. It does — but the user
        # is holding a PNG, and we cannot hand back an edited image we
        # have any way to verify.
        #
        # Emitting `redact` anyway is the specific failure D35 warned
        # about: an action the client cannot perform. The content
        # script guards on `redacted_text` being present and would fall
        # back to a warning, so nothing would break — it would just be
        # the server telling a lie the client happens to catch.
        #
        # Downgrading to `warn` unconditionally would be worse than the
        # lie: a critical key that would BLOCK as text would merely warn
        # as a screenshot, making the image path the weaker one and
        # rewarding exactly the bypass B11 describes. So the score
        # decides, using the same threshold as everything else.
        if action is Action.REDACT:
            action = Action.BLOCK if score >= policy.block_threshold else Action.WARN

    entities = [
        DetectedEntity(
            type=f.type, start=f.start, end=f.end, score=f.score, detector=f.detector
        )
        for f in findings
    ]

    latency_ms = (time.perf_counter() - started) * 1000

    response = AnalyzeResponse(
        request_id=request_id,
        action=action,
        level=level,
        score=score,
        entities=entities,
        message=_describe(findings, action, from_image=True),
        # Redaction is not offered for images. Editing pixels is a
        # different problem from editing a string, and handing back a
        # "redacted" image we cannot verify would be worse than
        # refusing — see D43.
        redacted_text=None,
        ocr=OcrInfo(
            characters=len(extracted.text),
            words=extracted.word_count,
            confidence=round(extracted.confidence, 3),
            width=extracted.width,
            height=extracted.height,
        ),
        latency_ms=round(latency_ms, 3),
        stages_run=stages_run,
        reasons=list(reasons),
    )

    return response, extracted


def analyze_pdf(
    pdf_bytes: bytes,
    *,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> tuple[AnalyzeResponse, PdfResult]:
    """Read a PDF, then run the ordinary pipeline over what it says.

    Stage 3b is a text SOURCE, exactly as Stage 3 is (D41), so the only
    thing that differs from `analyze()` is where the text came from.

    Deliberately a sibling of `analyze_image` rather than a branch
    inside it. The two share a shape and almost nothing else: one loses
    information to recognition and reports a confidence, the other
    extracts exactly and reports which of five channels it came from.
    Folding them together would mean a function whose every second line
    asks which kind of file this is.

    :param pdf_bytes: the raw upload
    :raises ValueError: on an unreadable PDF
    :raises RuntimeError: when the PDF backend is not installed
    """
    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]

    extracted = pdf.extract_text(pdf_bytes)

    # An empty extraction is NOT a pass, and this is the whole reason
    # `has_text_layer` exists. A scanned document yields nothing here,
    # and mapping nothing to `allow` would build the bypass in
    # deliberately: photograph the secret, wrap it in a PDF, send it.
    #
    # The honest action is to allow — we have no evidence of anything —
    # while telling the client plainly that nothing was read, so the
    # modal can say "this looks like a scan and was not inspected"
    # instead of showing silence as a clean bill of health. The same
    # distinction Stage 3 draws with OCR confidence.
    result = run_stages(extracted.text, policy=policy) if extracted.text.strip() else None

    if result is None:
        action, level, score = Action.ALLOW, RiskLevel.LOW, 0
        findings, stages_run, reasons = [], ["pdf"], ()
    else:
        action, level, score = result.action, result.level, result.score
        findings, reasons = result.findings, result.reasons
        stages_run = ["pdf", *result.stages_run]

        # ---- Redaction is not available for documents ------------
        # Same reasoning as images, reached from the other direction.
        # The risk engine sees a secret inside prose that survives its
        # removal and offers `redact` — but the user is holding a PDF,
        # and handing back an edited document would mean rewriting a
        # content stream, re-laying out a page, and asking them to
        # trust the result. Worse, a "redaction" applied to a PDF is
        # the exact operation this module exists to catch people
        # getting wrong.
        #
        # Downgrading everything to `warn` would make documents the
        # weakest channel and reward routing secrets through them, so
        # the score decides on the same threshold as everything else.
        if action is Action.REDACT:
            action = Action.BLOCK if score >= policy.block_threshold else Action.WARN

    entities = [
        DetectedEntity(
            type=f.type, start=f.start, end=f.end, score=f.score, detector=f.detector
        )
        for f in findings
    ]

    latency_ms = (time.perf_counter() - started) * 1000

    response = AnalyzeResponse(
        request_id=request_id,
        action=action,
        level=level,
        score=score,
        entities=entities,
        message=_describe(findings, action, from_pdf=True, extracted=extracted),
        redacted_text=None,
        pdf=PdfInfo(
            characters=extracted.char_count,
            pages=extracted.page_count,
            pages_read=extracted.pages_read,
            has_text_layer=extracted.has_text_layer,
            truncated=extracted.truncated,
            encrypted=extracted.encrypted,
            sources=list(extracted.sources),
        ),
        latency_ms=round(latency_ms, 3),
        stages_run=stages_run,
        reasons=list(reasons),
    )

    return response, extracted


def _describe(
    findings: list[Finding],
    action: Action,
    *,
    from_image: bool = False,
    from_pdf: bool = False,
    extracted: "PdfResult | None" = None,
) -> str:
    """Write the sentence the user reads in the modal.

    Names the entity TYPES and never the matched values. A warning
    that quotes the secret back would put it on screen — often on a
    shared screen, in a recording, or in a screenshot pasted into a
    ticket — which is the same mistake as logging it.

    Wording matters more than it looks. "We found something" trains
    people to click through; naming what was found and why gives them
    enough to make an actual decision, which is the difference between
    a control people respect and one they learn to dismiss.
    """
    where = " in this image" if from_image else " in this document" if from_pdf else ""

    if not findings:
        # ---- The sentence that must not lie -----------------------
        # "Nothing was found" and "there was nothing to look at" are
        # different statements, and collapsing them is how a scanned
        # payslip gets sent under a reassuring message. A document with
        # no text layer is reported as UNREAD, not as clean.
        if from_pdf and extracted is not None and not extracted.has_text_layer:
            if extracted.encrypted:
                return (
                    "This PDF is password-protected, so ShadowGuard could not "
                    "read it. It has NOT been checked for sensitive content."
                )
            return (
                "This PDF has no text layer — it is most likely a scan or a "
                "photograph. ShadowGuard could not read it, so it has NOT been "
                "checked for sensitive content."
            )

        if from_pdf:
            return "No sensitive content was found in this document."

        return (
            "No sensitive text was found in this image."
            if from_image
            else "No sensitive content detected."
        )

    # Preserve first-appearance order rather than sorting: it matches
    # what the user sees in their own message.
    seen: list[str] = []
    for finding in findings:
        label = finding.type.replace("_", " ").lower()
        if label not in seen:
            seen.append(label)

    listed = seen[0] if len(seen) == 1 else f"{', '.join(seen[:-1])} and {seen[-1]}"

    if action is Action.BLOCK:
        return (
            f"This message contains {listed}{where}. Sending has been blocked — "
            f"remove it before trying again."
        )

    return (
        f"This message appears to contain {listed}{where}. "
        f"Review it before sending."
    )
