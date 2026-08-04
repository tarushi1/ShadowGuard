"""
Rendering text into images, so OCR has ground truth to be scored against.
================================================================

The problem with evaluating OCR is normally the corpus: you need
images, and you need to know what they say. Collecting real screenshots
means either hand-transcribing them or trusting a second OCR engine to
grade the first.

Rendering sidesteps it entirely. We already have 200 labelled text
samples; drawing one into an image gives an image whose exact contents
are known by construction. The measurement is then honest and, more
importantly, REPRODUCIBLE — anyone can regenerate every image from the
corpus and get the same numbers.

WHAT THIS DOES NOT MEASURE, stated up front
-------------------------------------------
Rendered text is CLEANER than a real screenshot. There is no
compression noise, no subpixel antialiasing from a different renderer,
no window chrome, no cursor sitting on a character, no JPEG artefacts
from someone pasting a photo of their monitor.

So these numbers are an UPPER BOUND on OCR quality. Real screenshots
will do worse. A result of "OCR loses 15% of detections" should be read
as "loses at least 15%", and the report must say so rather than quoting
it as the expected field performance.
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

# Monospace, because the input this project actually receives is
# screenshots of terminals, editors and consoles. A proportional font
# would measure a different problem — and character confusion (0/O,
# 1/l) behaves differently between the two.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)


@dataclass(frozen=True)
class RenderStyle:
    """One way a screenshot might look.

    The three that matter for this input type: how small the text is,
    and whether it is dark-on-light (an editor) or light-on-dark (a
    terminal). Contrast polarity is included because Tesseract is
    trained on printed documents — dark ink on white paper — and light
    text on a dark background is the common case here.
    """

    name: str
    font_size: int
    foreground: tuple[int, int, int]
    background: tuple[int, int, int]


STYLES = (
    RenderStyle("editor-14px", 14, (30, 30, 30), (255, 255, 255)),
    RenderStyle("editor-11px", 11, (30, 30, 30), (255, 255, 255)),
    RenderStyle("terminal-14px", 14, (220, 220, 220), (20, 20, 24)),
    RenderStyle("terminal-11px", 11, (220, 220, 220), (20, 20, 24)),
)

DEFAULT_STYLE = STYLES[0]


def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue

    # PIL's built-in bitmap font. Usable, but it is ~11px and not
    # scalable, so results with it are not comparable across styles —
    # which is why the caller is told rather than left to wonder.
    raise RuntimeError(
        "no monospace TrueType font found; OCR evaluation needs one of: "
        + ", ".join(FONT_CANDIDATES)
    )


def render(text: str, style: RenderStyle = DEFAULT_STYLE, *, width_chars: int = 72) -> bytes:
    """Draw `text` as if it were a screenshot. Returns PNG bytes.

    PNG rather than JPEG, deliberately. JPEG's lossy compression
    introduces ringing around high-contrast edges — exactly where
    character strokes are — so JPEG would measure the codec as much as
    the recognizer. Testing JPEG degradation is a separate, later
    experiment; conflating the two would leave neither answered.

    :param text: the content to draw
    :param style: size and colour scheme
    :param width_chars: wrap width, in characters
    """
    from PIL import Image, ImageDraw

    font = _font(style.font_size)

    # Wrap by character count rather than by measured pixels: in a
    # monospace font they are the same thing, and it keeps the layout
    # predictable across font sizes.
    lines: list[str] = []
    for paragraph in text.split("\n"):
        while len(paragraph) > width_chars:
            lines.append(paragraph[:width_chars])
            paragraph = paragraph[width_chars:]
        lines.append(paragraph)

    # Measure with the real font rather than guessing from font_size:
    # a "14px" font is not 14 pixels wide per character, and getbbox
    # accounts for the actual advance width.
    probe = font.getbbox("M" * 10)
    char_width = (probe[2] - probe[0]) / 10
    line_height = style.font_size + 6
    padding = 16

    image = Image.new(
        "RGB",
        (
            int(char_width * width_chars) + padding * 2,
            line_height * len(lines) + padding * 2,
        ),
        style.background,
    )

    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text(
            (padding, padding + index * line_height),
            line,
            font=font,
            fill=style.foreground,
        )

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    return buffer.getvalue()
