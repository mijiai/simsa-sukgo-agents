from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from docx import Document
from pypdf import PdfWriter

from src.agents.financial.templates import (
    extract_docx_text,
    extract_pdf_text,
    get_cached_samples,
    load_financial_samples,
    reset_samples_for_tests,
)


def _make_docx(lines: list[str]) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf_with_text(text: str) -> bytes:
    """Build a minimal PDF whose first page contains `text` extractable by pypdf."""
    src = Document()
    src.add_paragraph(text)
    docx_buf = BytesIO()
    src.save(docx_buf)
    # docx → pdf is not trivial without LibreOffice; instead, write a hand-rolled
    # minimal PDF using PdfWriter + a text annotation. pypdf cannot synthesize text
    # streams either, so we use a known-good fixture: a manually constructed PDF
    # byte string containing one page with a Tj command.
    pdf_bytes = _hand_rolled_pdf(text)
    return pdf_bytes


def _hand_rolled_pdf(text: str) -> bytes:
    """Return bytes of a single-page PDF whose extracted text contains `text`."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode()
    objs: list[bytes] = []

    def add(obj: bytes) -> int:
        objs.append(obj)
        return len(objs)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    add(
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream"
    )
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    )
    return bytes(out)


def test_extract_docx_text_paragraphs() -> None:
    data = _make_docx(["재무 분석 샘플", "부채비율 200% 위험"])
    text = extract_docx_text(data)
    assert "재무 분석 샘플" in text
    assert "부채비율 200% 위험" in text


def test_extract_pdf_text_returns_visible_text() -> None:
    data = _hand_rolled_pdf("PDF financial sample text")
    text = extract_pdf_text(data)
    assert "PDF financial sample text" in text


def test_extract_pdf_text_handles_empty_pdf() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    text = extract_pdf_text(buf.getvalue())
    assert text == ""


async def test_load_financial_samples_caches_docx_and_pdf() -> None:
    reset_samples_for_tests()

    docx_data = _make_docx(["DOCX 샘플 본문"])
    pdf_data = _hand_rolled_pdf("PDF sample body")

    blob = MagicMock()
    blob.list_prefix = AsyncMock(
        return_value=[
            "templates/financial_samples/a.docx",
            "templates/financial_samples/b.pdf",
            "templates/financial_samples/notes.txt",
        ]
    )
    blob.download = AsyncMock(side_effect=[docx_data, pdf_data])

    samples = await load_financial_samples(blob, "templates/financial_samples/")

    assert len(samples) == 2
    assert "DOCX 샘플 본문" in samples[0]
    assert "PDF sample body" in samples[1]
    assert get_cached_samples() == samples
    blob.download.assert_any_await("templates/financial_samples/a.docx")
    blob.download.assert_any_await("templates/financial_samples/b.pdf")


async def test_load_financial_samples_skips_corrupt_file() -> None:
    reset_samples_for_tests()
    good = _make_docx(["좋은 샘플"])
    bad = b"not a real docx"

    blob = MagicMock()
    blob.list_prefix = AsyncMock(
        return_value=[
            "templates/financial_samples/good.docx",
            "templates/financial_samples/broken.docx",
        ]
    )
    blob.download = AsyncMock(side_effect=[good, bad])

    samples = await load_financial_samples(blob, "templates/financial_samples/")
    assert len(samples) == 1
    assert "좋은 샘플" in samples[0]


async def test_load_financial_samples_empty_prefix_resets_cache() -> None:
    reset_samples_for_tests(["initial"])

    blob = MagicMock()
    blob.list_prefix = AsyncMock(return_value=[])
    blob.download = AsyncMock()

    samples = await load_financial_samples(blob, "templates/financial_samples/")
    assert samples == []
    assert get_cached_samples() == []
    blob.download.assert_not_called()
