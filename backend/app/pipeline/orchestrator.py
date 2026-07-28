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

Today the body is a stub. Tasks 3.4 onward replace the marked section
with real detectors; nothing above or below it needs to change, and
neither does the extension.
"""

import time
import uuid
from dataclasses import dataclass

from ..detectors import entropy_detector, regex_detector
from ..detectors.base import Finding, highest_severity, resolve_overlaps
from ..schemas.analyze import (
    Action,
    AnalyzeRequest,
    AnalyzeResponse,
    DetectedEntity,
    RiskLevel,
)

# ---------------------------------------------------------------
# Severity → verdict
# ---------------------------------------------------------------
# PLACEHOLDER, replaced by the real risk engine in Week 5.
#
# Today the verdict is simply the worst severity found. That is a
# reasonable Stage-1 policy — a private key is a private key — but it
# cannot weigh evidence: two medium signals plus unusual behaviour
# should outrank one medium signal, and this cannot express that.
# Week 5 turns it into weighted fusion over all stages.
#
# Kept as data rather than an if-chain so the eventual replacement has
# something concrete to compare against in the report.
_SEVERITY_VERDICT: dict[str, tuple[Action, RiskLevel, int]] = {
    "critical": (Action.BLOCK, RiskLevel.CRITICAL, 95),
    "high": (Action.WARN, RiskLevel.HIGH, 75),
    "medium": (Action.WARN, RiskLevel.MEDIUM, 50),
    "low": (Action.ALLOW, RiskLevel.LOW, 20),
}

_NOTHING_FOUND = (Action.ALLOW, RiskLevel.LOW, 0)


@dataclass(frozen=True)
class StageResult:
    """What the detection stages concluded, before HTTP gets involved."""

    action: Action
    level: RiskLevel
    score: int
    findings: list[Finding]
    stages_run: list[str]


def run_stages(text: str, *, use_entropy: bool = True) -> StageResult:
    """Run the detection stages and fuse them into a verdict.

    Extracted from `analyze()` so the API route and the evaluation
    harness call THE SAME CODE. That is the point: a harness that
    reimplements the stage logic measures something that never runs in
    production, and the two drift apart without either failing.

    `use_entropy` exists for the harness, which needs a regex-only
    baseline to compare against. The report's central claim is a
    DIFFERENCE between configurations, so producing those
    configurations has to be a first-class capability rather than a
    commented-out line someone toggles by hand.

    :param text: content to inspect
    :param use_entropy: run Stage 1b as well as 1a
    """
    stages_run: list[str] = ["regex"]

    # ---- Stage 1a: pattern detection ----------------------------
    # Sub-millisecond and near-perfect on structured secrets, so it
    # always runs first.
    findings: list[Finding] = regex_detector.scan(text)

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

    action, level, score = _SEVERITY_VERDICT.get(
        highest_severity(findings), _NOTHING_FOUND
    )

    return StageResult(
        action=action,
        level=level,
        score=score,
        findings=findings,
        stages_run=stages_run,
    )


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

    result = run_stages(request.text)
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
        redacted_text=None,
        latency_ms=round(latency_ms, 3),
        stages_run=stages_run,
    )


def _describe(findings: list[Finding], action: Action) -> str:
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
    if not findings:
        return "No sensitive content detected."

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
            f"This message contains {listed}. Sending has been blocked — "
            f"remove it before trying again."
        )

    return (
        f"This message appears to contain {listed}. "
        f"Review it before sending."
    )
