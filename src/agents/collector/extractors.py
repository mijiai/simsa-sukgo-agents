"""업로드 파일을 종류별로 구조화 추출하는 모듈.

기존 collector 는 파일명만 raw.json 에 적었음. 이 모듈은 xlsx/pdf/image 를
다운로드해 시트별 표 / PDF 텍스트+표 / 이미지 메타데이터를 구조화해 반환.
financial agent 가 표 데이터를 직접 인용해 분석 bullet 을 작성할 수 있게 함.

LLM Vision 미사용 (PR4 에서 옵션 추가 예정) — 비용/지연 회피.
"""

from datetime import date, datetime
from io import BytesIO
from typing import Any

import openpyxl
import pdfplumber
import xlrd
from PIL import Image
from xlrd import xldate_as_datetime

from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore

logger = get_logger(__name__)

XLSX_MAX_ROWS_PER_SHEET = 200
PDF_MAX_TEXT_CHARS = 10000

_XLSX_SUFFIX = ".xlsx"
_XLS_SUFFIX = ".xls"
_PDF_SUFFIX = ".pdf"
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

_OWNERSHIP_KEYWORDS = ("구조도", "ownership", "chart", "조직")
_PRODUCT_KEYWORDS = ("상품", "product", "catalog")


def _suspect_image_role(filename: str) -> str:
    lower = filename.lower()
    if any(kw in lower for kw in _OWNERSHIP_KEYWORDS):
        return "ownership_chart"
    if any(kw in lower for kw in _PRODUCT_KEYWORDS):
        return "product_catalog"
    return "unknown"


def _normalize_xlsx_row(row: tuple) -> list[Any]:
    return [None if cell is None else cell for cell in row]


def _xls_cell_value(cell: xlrd.sheet.Cell, datemode: int) -> Any:
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xldate_as_datetime(cell.value, datemode)
    if cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
        return int(cell.value)
    return cell.value


def _stringify_header(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


async def extract_xls(blob: BlobStore, blob_path: str) -> list[dict[str, Any]]:
    """레거시 .xls (binary) 시트별 구조화 추출. extract_xlsx 와 같은 포맷.

    openpyxl 은 .xls 를 못 읽으므로 xlrd 사용 (financial/templates.py 와 동일 라이브러리).
    """
    data = await blob.download(blob_path)
    wb = xlrd.open_workbook(file_contents=data)
    sheets: list[dict[str, Any]] = []
    for sheet in wb.sheets():
        if sheet.nrows == 0:
            continue
        header = [_xls_cell_value(sheet.cell(0, c), wb.datemode) for c in range(sheet.ncols)]
        columns = [_stringify_header(c) for c in header]

        rows: list[list[Any]] = []
        truncated = False
        for r in range(1, sheet.nrows):
            if len(rows) >= XLSX_MAX_ROWS_PER_SHEET:
                truncated = True
                break
            rows.append(
                [_xls_cell_value(sheet.cell(r, c), wb.datemode) for c in range(sheet.ncols)]
            )

        if not columns and not rows:
            continue
        sheets.append(
            {
                "sheet_name": sheet.name,
                "columns": columns,
                "rows": rows,
                "truncated": truncated,
            }
        )
    return sheets


async def extract_xlsx(blob: BlobStore, blob_path: str) -> list[dict[str, Any]]:
    """엑셀 파일을 시트별로 구조화 추출.

    Returns:
        [{"sheet_name": str, "columns": list[str], "rows": list[list[Any]],
          "truncated": bool}, ...]

    - 1행 = columns, 2행+ = rows
    - 시트당 200행 초과 시 truncated=True
    - 병합 셀/스타일 무시 (값만)
    """
    data = await blob.download(blob_path)
    wb = openpyxl.load_workbook(BytesIO(data), data_only=True, read_only=True)
    sheets: list[dict[str, Any]] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter)
        except StopIteration:
            continue
        columns = [str(c) if c is not None else "" for c in header]

        rows: list[list[Any]] = []
        truncated = False
        for i, row in enumerate(rows_iter):
            if i >= XLSX_MAX_ROWS_PER_SHEET:
                truncated = True
                break
            rows.append(_normalize_xlsx_row(row))

        if not columns and not rows:
            continue
        sheets.append(
            {
                "sheet_name": sheet_name,
                "columns": columns,
                "rows": rows,
                "truncated": truncated,
            }
        )
    wb.close()
    return sheets


async def extract_pdf(blob: BlobStore, blob_path: str) -> dict[str, Any]:
    """PDF 텍스트 + 표 추출.

    Returns:
        {
            "text": str,            # 페이지별 \\n\\n 분리, 10000자 truncate
            "tables": list[dict],   # extract_xlsx 와 동일 포맷 (sheet_name = "page_N_table_M")
            "page_count": int,
            "truncated": bool,
        }
    """
    data = await blob.download(blob_path)
    text_parts: list[str] = []
    tables: list[dict[str, Any]] = []
    page_count = 0
    text_truncated = False

    with pdfplumber.open(BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        total_chars = 0
        for page_idx, page in enumerate(pdf.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                remaining = PDF_MAX_TEXT_CHARS - total_chars
                if remaining <= 0:
                    text_truncated = True
                    break
                if len(page_text) > remaining:
                    text_parts.append(page_text[:remaining])
                    total_chars += remaining
                    text_truncated = True
                    break
                text_parts.append(page_text)
                total_chars += len(page_text)

            for tbl_idx, raw_tbl in enumerate(page.extract_tables(), start=1):
                if not raw_tbl:
                    continue
                header_row = raw_tbl[0]
                columns = [(c or "").strip() for c in header_row]
                rows = [list(r) for r in raw_tbl[1:]]
                truncated = False
                if len(rows) > XLSX_MAX_ROWS_PER_SHEET:
                    rows = rows[:XLSX_MAX_ROWS_PER_SHEET]
                    truncated = True
                tables.append(
                    {
                        "sheet_name": f"page_{page_idx}_table_{tbl_idx}",
                        "columns": columns,
                        "rows": rows,
                        "truncated": truncated,
                    }
                )

    return {
        "text": "\n\n".join(text_parts),
        "tables": tables,
        "page_count": page_count,
        "truncated": text_truncated,
    }


VISION_CAPTION_SYSTEM_PROMPT = (
    "당신은 여신 심사 보고서에 첨부될 이미지를 한 문장의 한국어 캡션으로 설명하는 도우미입니다. "
    "객관적·간결한 사실만 기술하고 추측은 하지 않습니다. 1문장, 80자 이내."
)
VISION_CAPTION_USER_PROMPT = (
    "이 이미지가 무엇을 보여주는지 한 문장(80자 이내, 한국어)으로 캡션을 만드세요. "
    "예: '주요 자회사 3개를 포함한 지분 구조도', '상품 카탈로그 메인 페이지'."
)


async def caption_image_with_vision(
    blob: BlobStore,
    blob_path: str,
    original_filename: str,
    anthropic: AnthropicClient,
    *,
    model: str | None = None,
) -> str | None:
    """단일 이미지에 대해 Claude Vision 으로 짧은 caption 생성. 실패 시 None."""
    try:
        data = await blob.download(blob_path)
        return await anthropic.complete_with_image(
            system=VISION_CAPTION_SYSTEM_PROMPT,
            user_text=VISION_CAPTION_USER_PROMPT,
            image_bytes=data,
            image_filename=original_filename,
            model=model,
            max_tokens=200,
        )
    except Exception as exc:
        logger.warning(
            "collect.image.vision_caption_failed",
            blob_path=blob_path,
            error=str(exc),
        )
        return None


async def extract_image(blob: BlobStore, blob_path: str, original_filename: str) -> dict[str, Any]:
    """이미지 메타데이터 추출 (LLM Vision 미사용 — caption 은 별도 함수로 분리).

    Returns:
        {"blob_path": str, "original_filename": str,
         "suspected_role": str, "width": int, "height": int, "caption": None}

    caption 필드는 placeholder None — vision 활성화 시 collector service 가 채움.
    """
    data = await blob.download(blob_path)
    with Image.open(BytesIO(data)) as img:
        width, height = img.size
    return {
        "blob_path": blob_path,
        "original_filename": original_filename,
        "suspected_role": _suspect_image_role(original_filename),
        "width": width,
        "height": height,
        "caption": None,
    }


async def extract_uploaded_file(
    blob: BlobStore, blob_path: str, original_filename: str
) -> dict[str, Any] | None:
    """확장자 기반 dispatcher.

    Returns:
        {"kind": "xlsx"|"pdf"|"image", "filename": str, "content": ...} or None
    """
    lower = original_filename.lower()
    if lower.endswith(_XLSX_SUFFIX):
        content = await extract_xlsx(blob, blob_path)
        return {"kind": "xlsx", "filename": original_filename, "content": content}
    if lower.endswith(_XLS_SUFFIX):
        content = await extract_xls(blob, blob_path)
        return {"kind": "xlsx", "filename": original_filename, "content": content}
    if lower.endswith(_PDF_SUFFIX):
        content = await extract_pdf(blob, blob_path)
        return {"kind": "pdf", "filename": original_filename, "content": content}
    if lower.endswith(_IMAGE_SUFFIXES):
        content = await extract_image(blob, blob_path, original_filename)
        return {"kind": "image", "filename": original_filename, "content": content}
    return None
