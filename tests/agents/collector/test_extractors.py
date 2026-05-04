from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import openpyxl
from PIL import Image

from src.agents.collector.extractors import (
    PDF_MAX_TEXT_CHARS,
    XLSX_MAX_ROWS_PER_SHEET,
    extract_image,
    extract_pdf,
    extract_uploaded_file,
    extract_xlsx,
)


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


def _make_png(width: int = 100, height: int = 50) -> bytes:
    img = Image.new("RGB", (width, height), color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hand_rolled_pdf(text: str) -> bytes:
    """단일 페이지 텍스트 PDF (pdfplumber 가 추출 가능한 형태)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode()
    objs: list[bytes] = []

    def add(o: bytes) -> None:
        objs.append(o)

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
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def _blob_with(data: bytes) -> MagicMock:
    blob = MagicMock()
    blob.download = AsyncMock(return_value=data)
    return blob


async def test_extract_xlsx_basic_separates_columns_and_rows() -> None:
    data = _make_xlsx(
        {
            "재무": [["구분", "2023", "2024"], ["자산총계", 1000, 1200]],
            "비율": [["지표", "값"], ["부채비율", 180.5]],
        }
    )
    sheets = await extract_xlsx(_blob_with(data), "any/path.xlsx")
    assert len(sheets) == 2

    by_name = {s["sheet_name"]: s for s in sheets}
    assert by_name["재무"]["columns"] == ["구분", "2023", "2024"]
    assert by_name["재무"]["rows"] == [["자산총계", 1000, 1200]]
    assert by_name["재무"]["truncated"] is False

    assert by_name["비율"]["columns"] == ["지표", "값"]
    assert by_name["비율"]["rows"] == [["부채비율", 180.5]]


async def test_extract_xlsx_truncates_rows_beyond_limit() -> None:
    rows = [["구분", "값"]] + [[f"row_{i}", i] for i in range(XLSX_MAX_ROWS_PER_SHEET + 50)]
    data = _make_xlsx({"S": rows})
    sheets = await extract_xlsx(_blob_with(data), "x.xlsx")
    assert len(sheets[0]["rows"]) == XLSX_MAX_ROWS_PER_SHEET
    assert sheets[0]["truncated"] is True


async def test_extract_xlsx_skips_empty_sheets() -> None:
    data = _make_xlsx({"empty": [], "real": [["A"], ["x"]]})
    sheets = await extract_xlsx(_blob_with(data), "x.xlsx")
    names = [s["sheet_name"] for s in sheets]
    assert "real" in names
    assert "empty" not in names


async def test_extract_pdf_returns_text_and_page_count() -> None:
    data = _hand_rolled_pdf("PDF body content")
    result = await extract_pdf(_blob_with(data), "x.pdf")
    assert "PDF body content" in result["text"]
    assert result["page_count"] == 1
    assert result["truncated"] is False
    assert isinstance(result["tables"], list)


async def test_extract_pdf_truncates_text_at_limit() -> None:
    huge_pdf = _hand_rolled_pdf("X" * (PDF_MAX_TEXT_CHARS + 5000))
    result = await extract_pdf(_blob_with(huge_pdf), "x.pdf")
    assert len(result["text"]) <= PDF_MAX_TEXT_CHARS
    assert result["truncated"] is True


async def test_extract_image_returns_dimensions_and_role() -> None:
    blob = _blob_with(_make_png(width=480, height=273))
    result = await extract_image(blob, "jobs/J/input/ownership_chart.png", "ownership_chart.png")
    assert result["width"] == 480
    assert result["height"] == 273
    assert result["suspected_role"] == "ownership_chart"
    assert result["blob_path"] == "jobs/J/input/ownership_chart.png"
    assert result["original_filename"] == "ownership_chart.png"


async def test_extract_image_role_unknown_for_arbitrary_filename() -> None:
    blob = _blob_with(_make_png())
    result = await extract_image(blob, "p", "random.png")
    assert result["suspected_role"] == "unknown"


async def test_extract_image_role_product_catalog_keyword() -> None:
    blob = _blob_with(_make_png())
    result = await extract_image(blob, "p", "product_catalog_main.png")
    assert result["suspected_role"] == "product_catalog"


async def test_extract_uploaded_file_dispatches_by_extension() -> None:
    xlsx_blob = _blob_with(_make_xlsx({"S": [["a"], ["b"]]}))
    pdf_blob = _blob_with(_hand_rolled_pdf("text"))
    img_blob = _blob_with(_make_png())

    assert (await extract_uploaded_file(xlsx_blob, "p", "report.xlsx"))["kind"] == "xlsx"
    assert (await extract_uploaded_file(pdf_blob, "p", "report.pdf"))["kind"] == "pdf"
    assert (await extract_uploaded_file(img_blob, "p", "img.PNG"))["kind"] == "image"


async def test_extract_uploaded_file_returns_none_for_unsupported() -> None:
    blob = MagicMock()
    blob.download = AsyncMock()
    assert await extract_uploaded_file(blob, "p", "data.csv") is None
    assert await extract_uploaded_file(blob, "p", "notes.txt") is None
    blob.download.assert_not_called()
