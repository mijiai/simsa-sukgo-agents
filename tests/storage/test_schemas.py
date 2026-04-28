from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.common.constants import RiskLevel
from src.storage.schemas import (
    AgentName,
    AgentStatus,
    AlertHistory,
    AlertStatus,
    AnalysisJob,
    Company,
    FinancialMetrics,
    FinancialRaw,
    JobStatus,
    MonitoringSnapshot,
    SchedulerState,
)


def test_analysis_job_defaults() -> None:
    now = datetime.now(UTC)
    job = AnalysisJob(job_id="j1", company_name="ACME", created_at=now, updated_at=now)
    assert job.status is JobStatus.PENDING
    assert job.current_agent is None
    assert job.finished_at is None


def test_agent_status_validates_agent_name() -> None:
    status = AgentStatus(job_id="j1", agent_name=AgentName.COLLECT)
    assert status.agent_name is AgentName.COLLECT
    with pytest.raises(ValidationError):
        AgentStatus(job_id="j1", agent_name="invalid")  # type: ignore[arg-type]


def test_financial_raw_year_bounds() -> None:
    now = datetime.now(UTC)
    FinancialRaw(job_id="j", company_id="c", fiscal_year=2023, fiscal_type="annual", created_at=now)
    with pytest.raises(ValidationError):
        FinancialRaw(
            job_id="j",
            company_id="c",
            fiscal_year=1800,
            fiscal_type="annual",
            created_at=now,
        )


def test_financial_metrics_risk_score_bounds() -> None:
    now = datetime.now(UTC)
    FinancialMetrics(
        job_id="j",
        company_id="c",
        base_year=2023,
        risk_level=RiskLevel.MEDIUM,
        risk_score=50.0,
        created_at=now,
    )
    with pytest.raises(ValidationError):
        FinancialMetrics(
            job_id="j",
            company_id="c",
            base_year=2023,
            risk_level=RiskLevel.MEDIUM,
            risk_score=120.0,
            created_at=now,
        )


def test_company_required_fields() -> None:
    now = datetime.now(UTC)
    Company(company_id="c", company_name="ACME", created_at=now, updated_at=now)
    with pytest.raises(ValidationError):
        Company(company_name="ACME", created_at=now, updated_at=now)  # type: ignore[call-arg]


def test_monitoring_snapshot_run_date_is_date() -> None:
    snap = MonitoringSnapshot(
        company_id="c",
        run_date=date(2026, 4, 26),
        risk_level=RiskLevel.HIGH,
        risk_score=72.5,
        analysis_job_id="job-1",
    )
    assert snap.run_date == date(2026, 4, 26)
    assert snap.risk_score == 72.5
    assert snap.analysis_job_id == "job-1"
    assert snap.news_count == 0
    assert snap.lawsuit_count == 0
    assert snap.summary == ""


def test_alert_history_status_enum() -> None:
    alert = AlertHistory(
        company_id="c",
        sent_at=datetime.now(UTC),
        risk_level=RiskLevel.CRITICAL,
        sent_to="ops@example.com",
        status=AlertStatus.SENT,
    )
    assert alert.status is AlertStatus.SENT


def test_scheduler_state_defaults() -> None:
    state = SchedulerState()
    assert state.last_run_at is None
    assert state.run_count == 0
