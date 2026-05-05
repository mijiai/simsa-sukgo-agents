import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.monitoring.run_service import (
    MonitoringTargetInactiveError,
    monitor_run_now_service,
)
from src.agents.monitoring.schemas import MonitorRunNowRequest
from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError
from src.storage.schemas import (
    AgentName,
    AnalysisJob,
    AnalysisJobRef,
    JobStatus,
    MonitoringSnapshot,
    MonitoringTarget,
)


def _now() -> datetime:
    return datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _target(
    *,
    company_id: str = "c-1",
    is_active: bool = True,
    last_risk_level: RiskLevel | None = None,
) -> MonitoringTarget:
    return MonitoringTarget(
        company_id=company_id,
        company_name="ACME",
        recipient_email="ops@example.com",
        origin_job_id="job-origin",
        registered_at=_now(),
        is_active=is_active,
        last_risk_level=last_risk_level,
    )


def _result_payload(
    *,
    risk_level: str = "MEDIUM",
    risk_score: float = 55.0,
) -> dict:
    return {
        "job_id": "<replaced>",
        "company_name": "ACME",
        "company_id": "c-1",
        "risk_level": risk_level,
        "risk_score": risk_score,
        "summary": "전반적으로 주의 필요",
        "key_risk_factors": ["요인1", "요인2", "요인3", "요인4", "요인5", "요인6"],
        "positive_signals": ["신호1"],
        "data_gaps": ["재무제표 미확보"],
        "model": "claude-haiku-4-5-20251001",
    }


def _raw_payload(news_count: int = 3) -> dict:
    return {
        "company_name": "ACME",
        "company_id": "c-1",
        "news": [
            {"title": f"뉴스{i + 1}", "description": "요약", "url": "https://x.com"}
            for i in range(news_count)
        ],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
    }


def _make_deps(
    *,
    target: MonitoringTarget | None = None,
    target_get_side_effect=None,
    result: dict | None = None,
    raw: dict | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    raw_bytes = json.dumps(raw or _raw_payload(), ensure_ascii=False).encode("utf-8")
    result_bytes = json.dumps(result or _result_payload(), ensure_ascii=False).encode("utf-8")

    blob = MagicMock()
    # download is called twice: raw.json then result.json (in order)
    blob.download = AsyncMock(side_effect=[raw_bytes, result_bytes])
    blob.upload = AsyncMock()

    tables = MagicMock()
    tables.monitoring_targets = MagicMock()
    tables.monitoring_targets.get = AsyncMock(
        side_effect=target_get_side_effect,
        return_value=target if target_get_side_effect is None else None,
    )
    tables.monitoring_targets.update_after_run = AsyncMock()
    tables.jobs = MagicMock()
    tables.jobs.upsert = AsyncMock()
    tables.jobs.update_status = AsyncMock()
    tables.jobs_ref = MagicMock()
    tables.jobs_ref.upsert = AsyncMock()
    tables.agent_status = MagicMock()
    tables.agent_status.upsert = AsyncMock()
    tables.monitoring_snapshots = MagicMock()
    tables.monitoring_snapshots.insert = AsyncMock()

    naver = MagicMock()
    anthropic = MagicMock()

    return blob, tables, naver, anthropic


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_full_happy_path(mock_collect, mock_analyze) -> None:
    blob, tables, naver, anthropic = _make_deps(
        target=_target(last_risk_level=RiskLevel.LOW),
    )
    request = MonitorRunNowRequest(company_id="c-1")

    response = await monitor_run_now_service(request, blob, tables, naver, anthropic)

    # AnalysisJob + Ref + AgentStatus rows seeded
    tables.jobs.upsert.assert_awaited_once()
    job_arg: AnalysisJob = tables.jobs.upsert.call_args.args[0]
    assert job_arg.user_id == "system:monitoring"
    assert job_arg.company_name == "ACME"
    assert job_arg.status is JobStatus.PENDING

    tables.jobs_ref.upsert.assert_awaited_once()
    ref_arg: AnalysisJobRef = tables.jobs_ref.upsert.call_args.args[0]
    assert ref_arg.company_id == "c-1"

    # Only collect + analyze AgentStatus rows (no report)
    assert tables.agent_status.upsert.await_count == 2
    seeded = {call.args[0].agent_name for call in tables.agent_status.upsert.call_args_list}
    assert seeded == {AgentName.COLLECT, AgentName.ANALYZE}

    # collect + analyze invoked with the new job_id
    mock_collect.assert_awaited_once()
    collect_req = mock_collect.call_args.args[0]
    assert collect_req.job_id == job_arg.job_id
    assert collect_req.company_name == "ACME"

    mock_analyze.assert_awaited_once()
    analyze_req = mock_analyze.call_args.args[0]
    assert analyze_req.job_id == job_arg.job_id

    # AnalysisJob marked DONE (skips report)
    tables.jobs.update_status.assert_awaited_once()
    done_call = tables.jobs.update_status.call_args
    assert done_call.args == (job_arg.job_id, JobStatus.DONE)
    assert done_call.kwargs["finished_at"] is not None

    # Snapshot blob saved
    blob.upload.assert_awaited_once()
    upload_args = blob.upload.call_args
    assert upload_args.args[0].startswith("monitoring/c-1/")
    assert upload_args.args[0].endswith("/snapshot.json")
    payload = json.loads(upload_args.args[1].decode("utf-8"))
    assert payload["company_id"] == "c-1"
    assert payload["analysis_job_id"] == job_arg.job_id
    assert payload["risk_level"] == "MEDIUM"
    assert payload["risk_score"] == 55.0
    assert payload["risk_changed"] is True
    assert payload["previous_risk_level"] == "LOW"
    assert payload["news_count"] == 3
    assert "뉴스1" in payload["news_top_titles"]
    assert payload["data_gaps"] == ["재무제표 미확보"]

    # Snapshot Table row inserted
    tables.monitoring_snapshots.insert.assert_awaited_once()
    snap_row: MonitoringSnapshot = tables.monitoring_snapshots.insert.call_args.args[0]
    assert snap_row.analysis_job_id == job_arg.job_id
    assert snap_row.risk_level is RiskLevel.MEDIUM
    assert snap_row.risk_score == 55.0
    assert snap_row.news_count == 3
    # key_signals joins top 5 risk factors
    assert snap_row.key_signals == "요인1 / 요인2 / 요인3 / 요인4 / 요인5"
    assert snap_row.summary.startswith("전반적으로")

    # MonitoringTarget last_run_at + last_risk_level updated
    tables.monitoring_targets.update_after_run.assert_awaited_once()
    update_args = tables.monitoring_targets.update_after_run.call_args.args
    assert update_args[0] == "c-1"
    assert update_args[2] is RiskLevel.MEDIUM

    # Response
    assert response.status == "snapshot_done"
    assert response.risk_level is RiskLevel.MEDIUM
    assert response.risk_score == 55.0
    assert response.previous_risk_level is RiskLevel.LOW
    assert response.risk_changed is True
    assert response.snapshot_blob_path.startswith("monitoring/c-1/")


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_response_includes_risk_evidence_fields(mock_collect, mock_analyze) -> None:
    """frontend 가 snapshot.json fetch 없이 위험 판단 근거를 바로 쓸 수 있도록.

    summary / key_risk_factors / positive_signals / data_gaps 가 MonitorRunNowResponse 에
    그대로 노출되는지 검증. snapshot.json 에 들어가는 값과 동일.
    """
    blob, tables, naver, anthropic = _make_deps(target=_target())
    request = MonitorRunNowRequest(company_id="c-1")

    response = await monitor_run_now_service(request, blob, tables, naver, anthropic)

    assert response.summary == "전반적으로 주의 필요"
    assert response.key_risk_factors == ["요인1", "요인2", "요인3", "요인4", "요인5", "요인6"]
    assert response.positive_signals == ["신호1"]
    assert response.data_gaps == ["재무제표 미확보"]


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_response_evidence_defaults_when_analysis_missing_fields(
    mock_collect, mock_analyze
) -> None:
    """analyze 결과에 일부 필드 누락이어도 응답은 빈 list / None 으로 안전 fallback."""
    minimal_result = {
        "risk_level": "LOW",
        "risk_score": 10.0,
        # summary / key_risk_factors / positive_signals / data_gaps 없음
    }
    blob, tables, naver, anthropic = _make_deps(target=_target(), result=minimal_result)
    request = MonitorRunNowRequest(company_id="c-1")

    response = await monitor_run_now_service(request, blob, tables, naver, anthropic)

    assert response.summary is None
    assert response.key_risk_factors == []
    assert response.positive_signals == []
    assert response.data_gaps == []


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_first_run_no_previous_risk_level(mock_collect, mock_analyze) -> None:
    blob, tables, naver, anthropic = _make_deps(
        target=_target(last_risk_level=None),
    )
    request = MonitorRunNowRequest(company_id="c-1")

    response = await monitor_run_now_service(request, blob, tables, naver, anthropic)

    assert response.previous_risk_level is None
    assert response.risk_changed is True  # null → MEDIUM = changed


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_same_risk_level_no_change(mock_collect, mock_analyze) -> None:
    blob, tables, naver, anthropic = _make_deps(
        target=_target(last_risk_level=RiskLevel.MEDIUM),
        result=_result_payload(risk_level="MEDIUM"),
    )
    request = MonitorRunNowRequest(company_id="c-1")

    response = await monitor_run_now_service(request, blob, tables, naver, anthropic)

    assert response.risk_changed is False


async def test_run_now_target_not_found() -> None:
    blob, tables, naver, anthropic = _make_deps(
        target_get_side_effect=EntityNotFoundError("MonitoringTargets", "company", "c-99"),
    )
    request = MonitorRunNowRequest(company_id="c-99")

    with pytest.raises(EntityNotFoundError):
        await monitor_run_now_service(request, blob, tables, naver, anthropic)

    tables.jobs.upsert.assert_not_called()
    blob.upload.assert_not_called()


async def test_run_now_target_inactive_raises() -> None:
    blob, tables, naver, anthropic = _make_deps(target=_target(is_active=False))
    request = MonitorRunNowRequest(company_id="c-1")

    with pytest.raises(MonitoringTargetInactiveError):
        await monitor_run_now_service(request, blob, tables, naver, anthropic)

    tables.jobs.upsert.assert_not_called()
    tables.monitoring_snapshots.insert.assert_not_called()


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch(
    "src.agents.monitoring.run_service.collect_company_data_service",
    new_callable=AsyncMock,
    side_effect=RuntimeError("collect boom"),
)
async def test_run_now_collect_failure_propagates_no_snapshot(mock_collect, mock_analyze) -> None:
    blob, tables, naver, anthropic = _make_deps(target=_target())
    request = MonitorRunNowRequest(company_id="c-1")

    with pytest.raises(RuntimeError):
        await monitor_run_now_service(request, blob, tables, naver, anthropic)

    mock_analyze.assert_not_called()
    blob.upload.assert_not_called()
    tables.monitoring_snapshots.insert.assert_not_called()
    tables.monitoring_targets.update_after_run.assert_not_called()


@patch(
    "src.agents.monitoring.run_service.analyze_financials_service",
    new_callable=AsyncMock,
    side_effect=RuntimeError("analyze boom"),
)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_analyze_failure_propagates_no_snapshot(mock_collect, mock_analyze) -> None:
    blob, tables, naver, anthropic = _make_deps(target=_target())
    request = MonitorRunNowRequest(company_id="c-1")

    with pytest.raises(RuntimeError):
        await monitor_run_now_service(request, blob, tables, naver, anthropic)

    blob.upload.assert_not_called()
    tables.monitoring_snapshots.insert.assert_not_called()


@patch("src.agents.monitoring.run_service.analyze_financials_service", new_callable=AsyncMock)
@patch("src.agents.monitoring.run_service.collect_company_data_service", new_callable=AsyncMock)
async def test_run_now_truncates_long_summary(mock_collect, mock_analyze) -> None:
    long_summary = "가" * 5000
    blob, tables, naver, anthropic = _make_deps(
        target=_target(),
        result={**_result_payload(), "summary": long_summary},
    )
    request = MonitorRunNowRequest(company_id="c-1")

    await monitor_run_now_service(request, blob, tables, naver, anthropic)

    snap_row: MonitoringSnapshot = tables.monitoring_snapshots.insert.call_args.args[0]
    assert len(snap_row.summary) == 1000
    # full summary preserved in blob payload
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["summary"] == long_summary
