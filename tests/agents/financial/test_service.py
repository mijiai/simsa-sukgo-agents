import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.agents.financial.schemas import AnalyzeRequest
from src.agents.financial.service import analyze_financials_service
from src.common.constants import RiskLevel
from src.common.exceptions import AnthropicApiError
from src.storage.schemas import AgentName, JobStatus


def _now() -> datetime:
    return datetime(2026, 4, 27, 10, 0, tzinfo=UTC)


def _raw_payload(*, news_count: int = 2, files: list[str] | None = None) -> dict:
    return {
        "company_name": "ACME",
        "company_id": "c-1",
        "collected_at": _now().isoformat(),
        "news": [
            {
                "title": f"뉴스{i + 1}",
                "description": "요약",
                "url": "https://example.com/a",
                "naver_link": "https://n.news.naver.com/a",
                "published_at": _now().isoformat(),
            }
            for i in range(news_count)
        ],
        "lawsuits": [],
        "uploaded_files": files or [],
        "financial_years": [],
    }


def _judgment(
    *,
    risk_level: str = "MEDIUM",
    risk_score: float = 55.0,
    summary: str = "종합 분석",
    key_risk_factors: list[str] | None = None,
    positive_signals: list[str] | None = None,
    data_gaps: list[str] | None = None,
) -> dict:
    return {
        "risk_level": risk_level,
        "risk_score": risk_score,
        "summary": summary,
        "key_risk_factors": key_risk_factors or [],
        "positive_signals": positive_signals or [],
        "data_gaps": data_gaps or [],
    }


def _make_deps(
    *,
    raw: dict | None = None,
    judgment: dict | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    raw = raw or _raw_payload()
    blob = MagicMock()
    blob.download = AsyncMock(return_value=json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    blob.upload = AsyncMock()

    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.update_status = AsyncMock()
    tables.agent_status = MagicMock()
    tables.agent_status.update_running = AsyncMock()
    tables.agent_status.update_done = AsyncMock()
    tables.agent_status.update_failed = AsyncMock()

    anthropic = MagicMock()
    anthropic.model = "claude-haiku-4-5-20251001"
    anthropic.complete_json = AsyncMock(return_value=judgment or _judgment())

    return blob, tables, anthropic


async def test_analyze_success_writes_result_and_updates_status() -> None:
    blob, tables, anthropic = _make_deps(
        judgment=_judgment(
            risk_level="MEDIUM",
            risk_score=55.0,
            summary="뉴스 일부에 부정 신호 감지",
            key_risk_factors=["경쟁사 신제품 출시"],
            positive_signals=["인지도 상승"],
            data_gaps=["재무제표 미확보"],
        ),
    )

    request = AnalyzeRequest(job_id="job-1")
    response = await analyze_financials_service(request, blob, tables, anthropic)

    first_status = tables.jobs.update_status.call_args_list[0]
    assert first_status.args[0] == "job-1"
    assert first_status.args[1] is JobStatus.ANALYZING
    assert first_status.kwargs["current_agent"] is AgentName.ANALYZE

    tables.agent_status.update_running.assert_awaited_once_with("job-1", AgentName.ANALYZE)

    blob.download.assert_awaited_once_with("jobs/job-1/collect/raw.json")
    anthropic.complete_json.assert_awaited_once()
    call_kwargs = anthropic.complete_json.call_args.kwargs
    assert "당신은 한국 시중은행" in call_kwargs["system"]
    assert "ACME" in call_kwargs["user"]

    blob.upload.assert_awaited_once()
    upload_args = blob.upload.call_args
    assert upload_args.args[0] == "jobs/job-1/analyze/result.json"
    payload = json.loads(upload_args.args[1].decode("utf-8"))
    assert payload["job_id"] == "job-1"
    assert payload["company_name"] == "ACME"
    assert payload["company_id"] == "c-1"
    assert payload["risk_level"] == "MEDIUM"
    assert payload["risk_score"] == 55.0
    assert payload["model"] == "claude-haiku-4-5-20251001"
    assert payload["summary"] == "뉴스 일부에 부정 신호 감지"
    assert payload["key_risk_factors"] == ["경쟁사 신제품 출시"]
    assert payload["positive_signals"] == ["인지도 상승"]
    assert payload["data_gaps"] == ["재무제표 미확보"]
    assert payload["input_summary"] == {
        "news_count": 2,
        "lawsuit_count": 0,
        "uploaded_file_count": 0,
        "financial_years": [],
        "extracted_table_count": 0,
        "extracted_image_count": 0,
    }
    # section_insights 가 judgment 에 없으면 빈 리스트로 result 에 들어감 (하위 호환)
    assert payload["section_insights"] == []
    assert upload_args.kwargs["content_type"] == "application/json"

    tables.agent_status.update_done.assert_awaited_once_with(
        "job-1", AgentName.ANALYZE, output_blob_path="jobs/job-1/analyze/result.json"
    )

    assert response.status == "analyze_done"
    assert response.risk_level is RiskLevel.MEDIUM
    assert response.risk_score == 55.0
    assert response.key_risk_factors == ["경쟁사 신제품 출시"]
    assert response.data_gaps == ["재무제표 미확보"]
    assert response.output_blob_path == "jobs/job-1/analyze/result.json"


async def test_analyze_anthropic_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    anthropic.complete_json = AsyncMock(
        side_effect=AnthropicApiError("rate limited", status_code=429)
    )

    request = AnalyzeRequest(job_id="job-2")
    with pytest.raises(AnthropicApiError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()
    failed_args = tables.agent_status.update_failed.call_args.args
    assert failed_args[0] == "job-2"
    assert failed_args[1] is AgentName.ANALYZE
    assert "rate limited" in failed_args[2]

    assert tables.jobs.update_status.await_count == 2
    failed_status_call = tables.jobs.update_status.call_args_list[1]
    assert failed_status_call.args[1] is JobStatus.FAILED

    blob.upload.assert_not_called()


async def test_analyze_invalid_judgment_schema_marks_failed() -> None:
    blob, tables, anthropic = _make_deps(
        judgment={
            "risk_level": "INVALID_LEVEL",
            "risk_score": 50.0,
            "summary": "요약",
        },
    )

    request = AnalyzeRequest(job_id="job-3")
    with pytest.raises(ValidationError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()
    assert tables.jobs.update_status.await_count == 2
    blob.upload.assert_not_called()


async def test_analyze_risk_score_out_of_range_marks_failed() -> None:
    blob, tables, anthropic = _make_deps(
        judgment=_judgment(risk_score=150.0),
    )

    request = AnalyzeRequest(job_id="job-4")
    with pytest.raises(ValidationError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()
    blob.upload.assert_not_called()


async def test_analyze_blob_download_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    blob.download = AsyncMock(side_effect=RuntimeError("blob read boom"))

    request = AnalyzeRequest(job_id="job-5")
    with pytest.raises(RuntimeError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()
    anthropic.complete_json.assert_not_called()
    blob.upload.assert_not_called()


async def test_analyze_blob_upload_failure_marks_failed() -> None:
    blob, tables, anthropic = _make_deps()
    blob.upload = AsyncMock(side_effect=RuntimeError("blob write boom"))

    request = AnalyzeRequest(job_id="job-6")
    with pytest.raises(RuntimeError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()
    assert tables.jobs.update_status.await_count == 2


async def test_analyze_propagates_data_gaps_when_inputs_empty() -> None:
    raw = _raw_payload(news_count=0, files=[])
    blob, tables, anthropic = _make_deps(
        raw=raw,
        judgment=_judgment(
            risk_level="LOW",
            risk_score=10.0,
            summary="자료 부족으로 보수적 평가",
            data_gaps=["뉴스 0건", "재무제표 미확보", "내부 신용데이터 미확보"],
        ),
    )

    request = AnalyzeRequest(job_id="job-7")
    response = await analyze_financials_service(request, blob, tables, anthropic)

    assert response.data_gaps == ["뉴스 0건", "재무제표 미확보", "내부 신용데이터 미확보"]
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["input_summary"]["news_count"] == 0
    assert payload["input_summary"]["uploaded_file_count"] == 0


async def test_analyze_uses_company_name_from_raw_not_request() -> None:
    raw = _raw_payload()
    raw["company_name"] = "다른기업"
    blob, tables, anthropic = _make_deps(raw=raw)

    request = AnalyzeRequest(job_id="job-8")
    await analyze_financials_service(request, blob, tables, anthropic)

    user_prompt = anthropic.complete_json.call_args.kwargs["user"]
    assert "다른기업" in user_prompt
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["company_name"] == "다른기업"
