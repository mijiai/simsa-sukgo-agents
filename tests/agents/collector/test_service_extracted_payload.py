"""PR1 신규 동작 — collector 가 업로드 파일을 구조화 추출해 raw.json 에 박는지."""

import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import openpyxl
from PIL import Image

from src.agents.collector.internal_db import reset_for_tests as reset_internal_db
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service


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
    img = Image.new("RGB", (width, height), color="blue")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_deps() -> tuple[MagicMock, MagicMock, MagicMock]:
    reset_internal_db()
    blob = MagicMock()
    blob.upload = AsyncMock()
    blob.list_prefix = AsyncMock(return_value=[])
    blob.download = AsyncMock(return_value=b"")

    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.update_status = AsyncMock()
    tables.companies = MagicMock()
    tables.companies.find_by_name = AsyncMock(return_value=None)
    tables.agent_status = MagicMock()
    tables.agent_status.update_running = AsyncMock()
    tables.agent_status.update_done = AsyncMock()
    tables.agent_status.update_failed = AsyncMock()

    naver = MagicMock()
    naver.search = AsyncMock(return_value=[])
    return blob, tables, naver


async def test_xlsx_upload_populates_extracted_tables_and_financial_years() -> None:
    blob, tables, naver = _make_deps()
    xlsx_bytes = _make_xlsx(
        {
            "재무제표": [
                ["구분", "2022", "2023", "2024"],
                ["자산총계", 100, 120, 150],
                ["부채총계", 50, 60, 70],
            ]
        }
    )
    blob.list_prefix = AsyncMock(return_value=["jobs/job-1/input/financials.xlsx"])
    blob.download = AsyncMock(return_value=xlsx_bytes)

    request = CollectRequest(job_id="job-1", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["uploaded_files"] == ["financials.xlsx"]
    assert len(payload["extracted_tables"]) == 1
    table = payload["extracted_tables"][0]
    assert table["source_file"] == "financials.xlsx"
    assert table["sheet_name"] == "재무제표"
    assert table["columns"] == ["구분", "2022", "2023", "2024"]
    assert table["rows"][0] == ["자산총계", 100, 120, 150]

    # 자동 추론된 financial_years
    assert payload["financial_years"] == [2022, 2023, 2024]
    assert response.financial_years == [2022, 2023, 2024]
    assert response.extracted_table_count == 1
    assert response.extracted_image_count == 0
    assert response.extracted_doc_count == 0


async def test_image_upload_populates_extracted_images_with_role_heuristic() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(return_value=["jobs/job-2/input/ownership_chart.png"])
    blob.download = AsyncMock(return_value=_make_png(width=300, height=200))

    request = CollectRequest(job_id="job-2", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert len(payload["extracted_images"]) == 1
    img = payload["extracted_images"][0]
    assert img["source_file"] == "ownership_chart.png"
    assert img["blob_path"] == "jobs/job-2/input/ownership_chart.png"
    assert img["suspected_role"] == "ownership_chart"
    assert img["width"] == 300
    assert img["height"] == 200
    assert response.extracted_image_count == 1


async def test_corrupt_file_does_not_fail_overall_collect() -> None:
    """xlsx 1개는 정상, 1개는 corrupt → corrupt 만 skip 되고 collect 는 성공해야 함."""
    blob, tables, naver = _make_deps()
    good_xlsx = _make_xlsx({"S": [["A", "B"], [1, 2]]})

    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-3/input/good.xlsx",
            "jobs/job-3/input/broken.xlsx",
        ]
    )
    # list_prefix 결과는 정렬 순서대로 다운로드됨 — service 의 순회 순서 그대로
    blob.download = AsyncMock(side_effect=[good_xlsx, b"not a real xlsx"])

    request = CollectRequest(job_id="job-3", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    # good.xlsx 만 추출 성공
    assert response.extracted_table_count == 1
    assert payload["extracted_tables"][0]["source_file"] == "good.xlsx"
    # 둘 다 uploaded_files 에는 표시
    assert payload["uploaded_files"] == ["good.xlsx", "broken.xlsx"]


async def test_xlsx_sparse_falls_back_to_extracted_docs() -> None:
    """헤더가 빈 PDF 인쇄형 .xlsx → tables=[] + extracted_docs 에 텍스트로 추가."""
    blob, tables, naver = _make_deps()

    # 첫 행이 모두 빈 문자열인 sparse 시트
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Page 1")
    ws.append([""] * 20)  # 빈 헤더
    ws.append(["상호", None, None, "테스트회사", None, "자산총계", 1000])
    buf = BytesIO()
    wb.save(buf)
    sparse_bytes = buf.getvalue()

    blob.list_prefix = AsyncMock(return_value=["jobs/job-sparse/input/credit.xlsx"])
    blob.download = AsyncMock(return_value=sparse_bytes)

    request = CollectRequest(job_id="job-sparse", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["extracted_tables"] == []  # 표 추출 포기
    assert len(payload["extracted_docs"]) == 1
    doc = payload["extracted_docs"][0]
    assert doc["source_file"] == "credit.xlsx"
    assert "상호" in doc["text"] and "테스트회사" in doc["text"]
    assert doc["page_count"] == 0  # xls/xlsx text fallback 표시
    assert response.extracted_doc_count == 1
    assert response.extracted_table_count == 0


async def test_unsupported_extension_skipped_silently() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(return_value=["jobs/job-4/input/data.csv"])

    request = CollectRequest(job_id="job-4", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["uploaded_files"] == ["data.csv"]
    assert payload["extracted_tables"] == []
    assert payload["extracted_images"] == []
    assert payload["extracted_docs"] == []
    assert response.extracted_table_count == 0
    blob.download.assert_not_called()


async def test_collected_at_iso_format_preserved() -> None:
    """raw.json 의 collected_at 이 ISO-8601 형태로 직렬화되는지 (JSON dumps 호환성)."""
    blob, tables, naver = _make_deps()
    request = CollectRequest(job_id="job-5", company_name="ACME")
    await collect_company_data_service(request, blob, tables, naver)
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    # ISO parse 가능해야
    parsed = datetime.fromisoformat(payload["collected_at"])
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed) == UTC.utcoffset(parsed)
