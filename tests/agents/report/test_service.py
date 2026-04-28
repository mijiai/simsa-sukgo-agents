import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.report.schemas import ReportRequest
from src.agents.report.service import report_generate_service
from src.agents.report.templates import reset_templates_for_tests
from src.common.constants import RiskLevel
from src.common.exceptions import AnthropicApiError
from src.storage.schemas import AgentName, JobStatus


def _raw_payload() -> dict:
    return {
        "company_name": "ACME",
        "company_id": "c-1",
        "news": [{"title": "ACME 신사업"}],
        "uploaded_files": [],
    }


def _result_payload() -> dict:
    return {
        "job_id": "job-1",
        "company_name": "ACME",
        "company_id": "c-1",
        "risk_level": "MEDIUM",
        "risk_score": 55.0,
        "summary": "분석 요약",
        "key_risk_factors": ["요인1"],
        "positive_signals": ["신호1"],
        "data_gaps": ["재무제표 미확보"],
    }


def _make_deps(
    *,
    raw: dict | None = None,
    result: dict | None = None,
    markdown: str = "# 심사 보고서\n## 기업 개요\nACME...",
    templates: list[str] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    raw_bytes = json.dumps(raw or _raw_payload(), ensure_ascii=False).encode("utf-8")
    result_bytes = json.dumps(result or _result_payload(), ensure_ascii=False).encode("utf-8")

    blob = MagicMock()
    blob.download = AsyncMock(side_effect=[raw_bytes, result_bytes])
    blob.upload = AsyncMock()
    blob.generate_sas_url = MagicMock(return_value="https://sas-url.example.com/report.md?sig=xxx")

    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.update_status = AsyncMock()
    tables.agent_status = MagicMock()
    tables.agent_status.update_running = AsyncMock()
    tables.agent_status.update_done = AsyncMock()
    tables.agent_status.update_failed = AsyncMock()
    tables.jobs_ref = MagicMock()
    tables.jobs_ref.update_after_done = AsyncMock()

    anthropic = MagicMock()
    anthropic.model = "claude-sonnet-4-6"
    anthropic.complete_text = AsyncMock(return_value=markdown)

    reset_templates_for_tests(templates if templates is not None else ["샘플 본문 1"])
    return blob, tables, anthropic


async def test_report_success_uploads_md_updates_all_tables_returns_sas() -> None:
    blob, tables, anthropic = _make_deps()
    request = ReportRequest(job_id="job-1")

    response = await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    first_status = tables.jobs.update_status.call_args_list[0]
    assert first_status.args == ("job-1", JobStatus.REPORTING)
    assert first_status.kwargs["current_agent"] is AgentName.REPORT

    tables.agent_status.update_running.assert_awaited_once_with("job-1", AgentName.REPORT)

    assert blob.download.await_count == 2
    blob.download.assert_any_await("jobs/job-1/collect/raw.json")
    blob.download.assert_any_await("jobs/job-1/analyze/result.json")

    anthropic.complete_text.assert_awaited_once()
    user_prompt = anthropic.complete_text.call_args.kwargs["user"]
    assert "ACME" in user_prompt
    assert "샘플 본문 1" in user_prompt

    blob.upload.assert_awaited_once()
    upload_args = blob.upload.call_args
    assert upload_args.args[0] == "jobs/job-1/report/report.md"
    assert upload_args.args[1] == "# 심사 보고서\n## 기업 개요\nACME...".encode()
    assert upload_args.kwargs["content_type"] == "text/markdown; charset=utf-8"

    blob.generate_sas_url.assert_called_once()
    sas_args = blob.generate_sas_url.call_args
    assert sas_args.args[0] == "jobs/job-1/report/report.md"
    assert sas_args.args[1].total_seconds() == 168 * 3600

    tables.agent_status.update_done.assert_awaited_once_with(
        "job-1", AgentName.REPORT, output_blob_path="jobs/job-1/report/report.md"
    )

    done_status = tables.jobs.update_status.call_args_list[1]
    assert done_status.args == ("job-1", JobStatus.DONE)
    assert done_status.kwargs["report_blob_path"] == "jobs/job-1/report/report.md"
    assert done_status.kwargs["finished_at"] is not None

    tables.jobs_ref.update_after_done.assert_awaited_once()
    ref_kwargs = tables.jobs_ref.update_after_done.call_args.kwargs
    assert ref_kwargs["company_id"] == "c-1"
    assert ref_kwargs["job_id"] == "job-1"
    assert ref_kwargs["risk_level"] is RiskLevel.MEDIUM

    assert response.status == "done"
    assert response.risk_level is RiskLevel.MEDIUM
    assert response.risk_score == 55.0
    assert response.report_url == "https://sas-url.example.com/report.md?sig=xxx"
    assert response.report_blob_path == "jobs/job-1/report/report.md"


async def test_report_skips_jobs_ref_when_no_company_id() -> None:
    raw = _raw_payload()
    raw["company_id"] = None
    result = _result_payload()
    result["company_id"] = None

    blob, tables, anthropic = _make_deps(raw=raw, result=result)
    request = ReportRequest(job_id="job-2")

    await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    tables.jobs_ref.update_after_done.assert_not_called()


async def test_report_anthropic_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    anthropic.complete_text = AsyncMock(side_effect=AnthropicApiError("boom"))

    request = ReportRequest(job_id="job-3")
    with pytest.raises(AnthropicApiError):
        await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    tables.agent_status.update_failed.assert_awaited_once()
    failed_args = tables.agent_status.update_failed.call_args.args
    assert failed_args[1] is AgentName.REPORT

    assert tables.jobs.update_status.await_count == 2
    failed_status = tables.jobs.update_status.call_args_list[1]
    assert failed_status.args[1] is JobStatus.FAILED

    blob.upload.assert_not_called()
    tables.jobs_ref.update_after_done.assert_not_called()


async def test_report_blob_download_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    blob.download = AsyncMock(side_effect=RuntimeError("blob read boom"))

    request = ReportRequest(job_id="job-4")
    with pytest.raises(RuntimeError):
        await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    tables.agent_status.update_failed.assert_awaited_once()
    anthropic.complete_text.assert_not_called()
    blob.upload.assert_not_called()


async def test_report_blob_upload_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    blob.upload = AsyncMock(side_effect=RuntimeError("blob write boom"))

    request = ReportRequest(job_id="job-5")
    with pytest.raises(RuntimeError):
        await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    tables.agent_status.update_failed.assert_awaited_once()
    tables.jobs_ref.update_after_done.assert_not_called()


async def test_report_works_when_templates_cache_is_empty() -> None:
    blob, tables, anthropic = _make_deps(templates=[])
    request = ReportRequest(job_id="job-6")

    response = await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    user_prompt = anthropic.complete_text.call_args.kwargs["user"]
    assert "샘플이 로드되지 않았습니다" in user_prompt
    assert response.status == "done"


async def test_report_invalid_risk_level_in_result_marks_failed() -> None:
    bad_result = _result_payload()
    bad_result["risk_level"] = "INVALID"

    blob, tables, anthropic = _make_deps(result=bad_result)
    request = ReportRequest(job_id="job-7")

    with pytest.raises(ValueError):
        await report_generate_service(request, blob, tables, anthropic, sas_expiry_hours=168)

    tables.agent_status.update_failed.assert_awaited_once()
    blob.upload.assert_not_called()
