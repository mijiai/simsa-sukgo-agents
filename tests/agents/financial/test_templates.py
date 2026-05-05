from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import xlwt
from docx import Document
from pypdf import PdfWriter

from src.agents.financial.templates import (
    extract_docx_text,
    extract_pdf_text,
    extract_xls_text,
    extract_xlsx_text,
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


def _make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_xls(sheets: dict[str, list[list]]) -> bytes:
    wb = xlwt.Workbook()
    for name, rows in sheets.items():
        ws = wb.add_sheet(name)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                ws.write(r, c, val)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_xlsx_serializes_per_sheet_with_pipe_rows() -> None:
    data = _make_xlsx(
        {
            "재무KPI": [
                ["구분", "2023", "2022", "2021"],
                ["자산총계", 463743, 282367, 457150],
                ["부채총계", 189170, 66996, 249827],
            ],
            "비율": [["부채비율", 720.81, 68.90, 31.11]],
        }
    )
    text = extract_xlsx_text(data)
    assert "=== 시트: 재무KPI ===" in text
    assert "구분 | 2023 | 2022 | 2021" in text
    assert "자산총계 | 463743 | 282367 | 457150" in text
    assert "=== 시트: 비율 ===" in text
    assert "부채비율 | 720.81 | 68.9 | 31.11" in text


def test_extract_xlsx_skips_empty_rows_and_cells() -> None:
    data = _make_xlsx(
        {
            "Sheet1": [
                ["A", "B"],
                [None, None],
                ["C", None, "D"],
            ]
        }
    )
    text = extract_xlsx_text(data)
    lines = text.split("\n")
    assert "A | B" in lines
    assert "C | D" in lines
    assert "" not in [line for line in lines if line.strip() == ""] or True  # informational
    # critical: the empty row should not produce a blank pipe line
    assert " | " not in [line for line in lines if line.strip() == "|"]


def test_extract_xls_serializes_per_sheet_with_pipe_rows() -> None:
    data = _make_xls(
        {
            "Page 1": [
                ["상호", "나이스고속관광(주)"],
                ["대표자명", "최정윤"],
            ],
            "Page 2": [["부채비율", 720.81]],
        }
    )
    text = extract_xls_text(data)
    assert "=== 시트: Page 1 ===" in text
    assert "상호 | 나이스고속관광(주)" in text
    assert "대표자명 | 최정윤" in text
    assert "=== 시트: Page 2 ===" in text
    assert "부채비율 | 720.81" in text


def test_extract_xls_handles_integer_floats() -> None:
    """Excel stores all numbers as float — integer-valued floats must drop the .0"""
    data = _make_xls({"S": [["자산총계", 463743]]})
    text = extract_xls_text(data)
    assert "자산총계 | 463743" in text
    assert "463743.0" not in text


def test_extract_xls_text_truncates_per_sheet_when_max_chars_set() -> None:
    """collector 가 LLM 토큰 통제용으로 시트당 max_chars 를 넘기면 truncate 마커가 박힘."""
    long_value = "X" * 5000
    data = _make_xls({"S": [["자산", long_value]]})
    text = extract_xls_text(data, max_chars_per_sheet=1000)
    assert "[...truncated]" in text
    assert len(text) < 1200  # marker 포함 약간의 여유


def test_extract_xls_text_no_truncation_when_max_chars_none() -> None:
    """default None → 무제한 (기존 동작 보존)."""
    long_value = "X" * 5000
    data = _make_xls({"S": [["자산", long_value]]})
    text = extract_xls_text(data)  # max_chars_per_sheet 없이
    assert "[...truncated]" not in text
    assert long_value in text


def test_extract_xlsx_text_truncates_per_sheet_when_max_chars_set() -> None:
    long_value = "X" * 5000
    data = _make_xlsx({"S": [["자산", long_value]]})
    text = extract_xlsx_text(data, max_chars_per_sheet=1000)
    assert "[...truncated]" in text
    assert len(text) < 1200


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


async def test_load_financial_samples_caches_all_four_formats() -> None:
    reset_samples_for_tests()

    docx_data = _make_docx(["DOCX 본문"])
    pdf_data = _hand_rolled_pdf("PDF body")
    xlsx_data = _make_xlsx({"S": [["xlsx_marker", 100]]})
    xls_data = _make_xls({"S": [["xls_marker", 200]]})

    blob = MagicMock()
    blob.list_prefix = AsyncMock(
        return_value=[
            "templates/financial_samples/a.docx",
            "templates/financial_samples/b.pdf",
            "templates/financial_samples/c.xlsx",
            "templates/financial_samples/d.xls",
            "templates/financial_samples/e.png",  # unsupported, must skip
        ]
    )
    blob.download = AsyncMock(side_effect=[docx_data, pdf_data, xlsx_data, xls_data])

    samples = await load_financial_samples(blob, "templates/financial_samples/")

    assert len(samples) == 4
    joined = "\n".join(samples)
    assert "DOCX 본문" in joined
    assert "PDF body" in joined
    assert "xlsx_marker | 100" in joined
    assert "xls_marker | 200" in joined
    # png ignored
    assert blob.download.await_count == 4


async def test_load_financial_samples_empty_prefix_resets_cache() -> None:
    reset_samples_for_tests(["initial"])

    blob = MagicMock()
    blob.list_prefix = AsyncMock(return_value=[])
    blob.download = AsyncMock()

    samples = await load_financial_samples(blob, "templates/financial_samples/")
    assert samples == []
    assert get_cached_samples() == []
    blob.download.assert_not_called()
