import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.collector.internal_db import reset_for_tests as reset_internal_db
from src.agents.collector.schemas import CollectRequest, NewsArticle
from src.agents.collector.service import collect_company_data_service
from src.common.exceptions import NaverApiError
from src.storage.schemas import AgentName, Company, JobStatus


def _now() -> datetime:
    return datetime(2026, 4, 27, 9, 0, tzinfo=UTC)


def _make_deps() -> tuple[MagicMock, MagicMock, MagicMock]:
    reset_internal_db()
    blob = MagicMock()
    blob.upload = AsyncMock()
    blob.list_prefix = AsyncMock(return_value=[])

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


def _article(title: str = "ACME 신사업") -> NewsArticle:
    return NewsArticle(
        title=title,
        description="요약",
        url="https://example.com/a",
        naver_link="https://n.news.naver.com/a",
        published_at=_now(),
    )


async def test_collect_success_writes_raw_and_updates_status() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-1/input/사업계획서.pdf",
            "jobs/job-1/input/재무제표.xlsx",
            "jobs/job-1/input/prompt.txt",
        ]
    )
    tables.companies.find_by_name = AsyncMock(
        return_value=Company(
            company_id="c-1",
            company_name="ACME",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    naver.search = AsyncMock(return_value=[_article("뉴스1"), _article("뉴스2")])

    request = CollectRequest(job_id="job-1", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    tables.jobs.update_status.assert_awaited_once()
    args = tables.jobs.update_status.call_args
    assert args.args[0] == "job-1"
    assert args.args[1] is JobStatus.COLLECTING
    assert args.kwargs["current_agent"] is AgentName.COLLECT

    tables.agent_status.update_running.assert_awaited_once_with("job-1", AgentName.COLLECT)

    naver.search.assert_awaited_once()
    assert naver.search.call_args.args[0] == "ACME"

    blob.upload.assert_awaited_once()
    upload_args = blob.upload.call_args
    assert upload_args.args[0] == "jobs/job-1/collect/raw.json"
    payload = json.loads(upload_args.args[1].decode("utf-8"))
    assert payload["company_name"] == "ACME"
    assert payload["company_id"] == "c-1"
    assert len(payload["news"]) == 2
    assert payload["news"][0]["title"] == "뉴스1"
    assert payload["uploaded_files"] == ["사업계획서.pdf", "재무제표.xlsx"]
    assert payload["lawsuits"] == []
    assert payload["financial_years"] == []
    assert upload_args.kwargs["content_type"] == "application/json"

    tables.agent_status.update_done.assert_awaited_once_with(
        "job-1", AgentName.COLLECT, output_blob_path="jobs/job-1/collect/raw.json"
    )

    assert response.job_id == "job-1"
    assert response.status == "collect_done"
    assert response.company_id == "c-1"
    assert response.news_count == 2
    assert response.lawsuit_count == 0
    assert response.financial_years == []
    assert response.uploaded_files == ["사업계획서.pdf", "재무제표.xlsx"]
    assert response.output_blob_path == "jobs/job-1/collect/raw.json"


async def test_collect_no_files_no_news_returns_zero_counts() -> None:
    blob, tables, naver = _make_deps()
    request = CollectRequest(job_id="job-2", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    assert response.news_count == 0
    assert response.uploaded_files == []
    assert response.company_id is None
    blob.upload.assert_awaited_once()
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["news"] == []
    assert payload["uploaded_files"] == []


async def test_collect_skips_prompt_txt_in_uploaded_files() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-3/input/prompt.txt",
            "jobs/job-3/input/data.csv",
        ]
    )
    request = CollectRequest(job_id="job-3", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)
    assert response.uploaded_files == ["data.csv"]


async def test_collect_naver_failure_marks_agent_status_failed() -> None:
    blob, tables, naver = _make_deps()
    naver.search = AsyncMock(side_effect=NaverApiError("rate limited", status_code=429))

    request = CollectRequest(job_id="job-4", company_name="ACME")
    with pytest.raises(NaverApiError):
        await collect_company_data_service(request, blob, tables, naver)

    tables.agent_status.update_failed.assert_awaited_once()
    failed_args = tables.agent_status.update_failed.call_args.args
    assert failed_args[0] == "job-4"
    assert failed_args[1] is AgentName.COLLECT
    assert "rate limited" in failed_args[2]

    # AnalysisJobs.status set to FAILED (second update_status call after the initial COLLECTING)
    assert tables.jobs.update_status.await_count == 2
    failed_status_call = tables.jobs.update_status.call_args_list[1]
    assert failed_status_call.args[1] is JobStatus.FAILED

    blob.upload.assert_not_called()


async def test_collect_blob_upload_failure_marks_failed() -> None:
    blob, tables, naver = _make_deps()
    blob.upload = AsyncMock(side_effect=RuntimeError("blob write boom"))

    request = CollectRequest(job_id="job-5", company_name="ACME")
    with pytest.raises(RuntimeError):
        await collect_company_data_service(request, blob, tables, naver)

    tables.agent_status.update_failed.assert_awaited_once()
    assert tables.jobs.update_status.await_count == 2


async def test_collect_company_not_found_continues_with_none_id() -> None:
    blob, tables, naver = _make_deps()
    tables.companies.find_by_name = AsyncMock(return_value=None)
    naver.search = AsyncMock(return_value=[_article()])

    request = CollectRequest(job_id="job-6", company_name="Unknown Co")
    response = await collect_company_data_service(request, blob, tables, naver)

    assert response.company_id is None
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["company_id"] is None
    assert payload["internal_credit_data"] is None
    assert response.has_internal_credit_data is False


async def test_collect_internal_db_hit_injects_data_into_raw_payload() -> None:
    blob, tables, naver = _make_deps()
    reset_internal_db({"1248100998": "부채비율 | 180\n유동비율 | 120"})
    tables.companies.find_by_name = AsyncMock(
        return_value=Company(
            company_id="1248100998",
            company_name="ACME",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    request = CollectRequest(job_id="job-7", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["company_id"] == "1248100998"
    assert "부채비율 | 180" in payload["internal_credit_data"]
    assert "유동비율 | 120" in payload["internal_credit_data"]
    assert response.has_internal_credit_data is True


async def test_collect_internal_db_miss_for_known_company_keeps_field_none() -> None:
    """Companies match hits but internal_db has no entry for that company_id."""
    blob, tables, naver = _make_deps()
    reset_internal_db({"OTHER-ID": "다른 회사 데이터"})
    tables.companies.find_by_name = AsyncMock(
        return_value=Company(
            company_id="1248100998",
            company_name="ACME",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    request = CollectRequest(job_id="job-8", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["company_id"] == "1248100998"
    assert payload["internal_credit_data"] is None
    assert response.has_internal_credit_data is False


async def test_collect_unknown_company_skips_internal_db_lookup() -> None:
    """If Companies match misses, internal_db isn't consulted (no company_id)."""
    blob, tables, naver = _make_deps()
    reset_internal_db({"1248100998": "이 회사 데이터는 매칭 안 돼야 함"})
    tables.companies.find_by_name = AsyncMock(return_value=None)

    request = CollectRequest(job_id="job-9", company_name="Unknown Co")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["company_id"] is None
    assert payload["internal_credit_data"] is None
    assert response.has_internal_credit_data is False


async def test_collect_attached_documents_extracts_text_into_raw_payload() -> None:
    """첨부된 docx 1개의 본문 텍스트가 raw.json 의 attached_documents 에 들어가야 함."""
    from io import BytesIO

    from docx import Document

    blob, tables, naver = _make_deps()

    # 첨부 .docx 1개 + prompt.txt
    doc = Document()
    doc.add_paragraph("사업 개요: ACME 신사업 진출 계획")
    buf = BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-10/input/business_plan.docx",
            "jobs/job-10/input/prompt.txt",
        ]
    )
    blob.download = AsyncMock(return_value=docx_bytes)

    request = CollectRequest(job_id="job-10", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["uploaded_files"] == ["business_plan.docx"]
    assert len(payload["attached_documents"]) == 1
    assert payload["attached_documents"][0]["filename"] == "business_plan.docx"
    assert "사업 개요: ACME 신사업 진출 계획" in payload["attached_documents"][0]["text"]
    assert response.attached_documents_count == 1


async def test_collect_unsupported_attachment_in_uploaded_files_but_not_in_documents() -> None:
    """지원 안 되는 확장자(.png)는 uploaded_files 엔 나오지만 attached_documents 엔 X."""
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-11/input/photo.png",
            "jobs/job-11/input/prompt.txt",
        ]
    )

    request = CollectRequest(job_id="job-11", company_name="ACME")
    response = await collect_company_data_service(request, blob, tables, naver)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["uploaded_files"] == ["photo.png"]
    assert payload["attached_documents"] == []
    assert response.attached_documents_count == 0
    blob.download.assert_not_called()
