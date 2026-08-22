"""
Minimal PDFs, built byte by byte.
================================================================

No authoring library. Partly to avoid a test-only dependency, but
mainly because a document produced by the same ecosystem that parses
it is a weak test: it exercises the happy path both libraries agree
on. These files are assembled from raw PDF syntax with the cross
reference table computed by hand, which is what a PDF from an
arbitrary piece of software actually looks like.
"""

from __future__ import annotations


def _assemble(objects: list[bytes], root: int, info: int | None = None) -> bytes:
    """Glue numbered objects into a file with a correct xref table.

    The offsets have to be exact byte positions from the start of the
    file, which is the entire reason this is a function rather than a
    string constant: edit any object by one character and every offset
    after it moves.
    """
    out = bytearray(b"%PDF-1.7\n")
    offsets = [0]

    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    count = len(objects) + 1

    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()

    trailer = f"<< /Size {count} /Root {root} 0 R"
    if info is not None:
        trailer += f" /Info {info} 0 R"
    trailer += " >>"

    out += b"trailer\n" + trailer.encode() + b"\n"
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()

    return bytes(out)


def _stream(content: bytes) -> bytes:
    return (
        f"<< /Length {len(content)} >>\nstream\n".encode()
        + content
        + b"\nendstream"
    )


def _escape(text: str) -> bytes:
    """PDF string escaping. Backslash and both parens are structural."""
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def text_pdf(*lines: str, metadata: dict[str, str] | None = None) -> bytes:
    """A born-digital PDF: one page, a real text layer."""
    drawn = b"BT\n/F1 12 Tf\n"
    for index, line in enumerate(lines):
        drawn += b"1 0 0 1 72 " + str(720 - index * 16).encode() + b" Tm\n"
        drawn += b"(" + _escape(line) + b") Tj\n"
    drawn += b"ET\n"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        _stream(drawn),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    info_number = None
    if metadata:
        entries = b" ".join(
            b"/" + key.encode() + b" (" + _escape(value) + b")"
            for key, value in metadata.items()
        )
        objects.append(b"<< " + entries + b" >>")
        info_number = len(objects)

    return _assemble(objects, root=1, info=info_number)


def redacted_pdf(hidden: str, visible: str = "This section has been redacted.") -> bytes:
    """The classic leak: a filled rectangle painted OVER live text.

    Visually identical to a redacted document. The characters are still
    in the content stream, in the same order, fully selectable — the
    black box is drawn afterwards and simply covers them up.
    """
    drawn = (
        b"BT\n/F1 12 Tf\n1 0 0 1 72 720 Tm\n(" + _escape(visible) + b") Tj\nET\n"
        b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(" + _escape(hidden) + b") Tj\nET\n"
        # Now paint over it. `re` builds the rectangle, `f` fills it.
        b"0 0 0 rg\n70 694 400 20 re\nf\n"
    )

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        _stream(drawn),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble(objects, root=1)


def annotated_pdf(note: str, author: str = "Reviewer", body: str = "Quarterly report.") -> bytes:
    """A page carrying a comment. Never printed, rarely reread."""
    drawn = b"BT\n/F1 12 Tf\n1 0 0 1 72 720 Tm\n(" + _escape(body) + b") Tj\nET\n"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> /Annots [6 0 R] >>",
        _stream(drawn),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Annot /Subtype /Text /Rect [100 100 120 120] /Contents ("
        + _escape(note) + b") /T (" + _escape(author) + b") >>",
    ]
    return _assemble(objects, root=1)


def scanned_pdf() -> bytes:
    """A page with no text layer at all — a photograph in a wrapper.

    The content stream draws a grey rectangle and nothing else, which
    is structurally what a scan is: marks on a page with no characters
    behind them.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        _stream(b"0.5 0.5 0.5 rg\n72 600 400 120 re\nf\n"),
    ]
    return _assemble(objects, root=1)


def many_pages_pdf(count: int, text: str = "page") -> bytes:
    """A document longer than the page cap, to prove truncation is honest."""
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(count))

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    for index in range(count):
        content_number = 5 + index * 2
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + f"/Contents {content_number} 0 R".encode()
            + b" /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        drawn = (
            b"BT\n/F1 12 Tf\n1 0 0 1 72 720 Tm\n("
            + _escape(f"{text} {index}") + b") Tj\nET\n"
        )
        objects.append(_stream(drawn))

    return _assemble(objects, root=1)


def form_pdf(**values: str) -> bytes:
    """A filled-in AcroForm.

    Worth having as its own fixture because a completed form is the
    case where page text and document content diverge most sharply: the
    page draws the QUESTIONS, and the field dictionary holds every
    ANSWER. Extracting only page text on an insurance claim reads the
    labels and misses the claimant.
    """
    field_numbers = list(range(5, 5 + len(values)))
    refs = " ".join(f"{n} 0 R" for n in field_numbers)

    objects = [
        f"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [{refs}] >> >>".encode(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        f"/Annots [{refs}] >>".encode(),
        _stream(b""),
    ]

    for name, value in values.items():
        objects.append(
            b"<< /Type /Annot /Subtype /Widget /FT /Tx /Rect [0 0 0 0] /T ("
            + _escape(name) + b") /V (" + _escape(value) + b") >>"
        )

    return _assemble(objects, root=1)


def encrypted_pdf(password: str = "letmein") -> bytes:
    """A password-protected document.

    Built with pypdf rather than by hand: the encryption dictionary
    involves real key derivation, and a hand-rolled one would test
    nothing except my ability to reimplement RC4 incorrectly.
    """
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(text_pdf("secret contents"))))
    writer.encrypt(password)

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
