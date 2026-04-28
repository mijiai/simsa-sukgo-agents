from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError

from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError, TableStorageError
from src.storage.schemas import (
    AgentName,
    AgentStatus,
    AnalysisJob,
    Company,
    FinancialMetrics,
    FinancialRaw,
    JobStatus,
    MonitoringSnapshot,
    SchedulerState,
)
from src.storage.table_store import (
    ALL_TABLES,
    AgentStatusRepo,
    AnalysisJobsRepo,
    CompaniesRepo,
    FinancialMetricsRepo,
    FinancialRawRepo,
    MonitoringSnapshotsRepo,
    SchedulerStateRepo,
    TableStore,
    _entity_to_model,
    _model_to_entity,
    _to_storage_value,
)

CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=testacc;"
    "AccountKey=dGVzdGtleQ==;"
    "EndpointSuffix=core.windows.net"
)


def _now() -> datetime:
    return datetime(2026, 4, 26, 9, 0, tzinfo=UTC)


def test_to_storage_value_unwraps_strenum() -> None:
    assert _to_storage_value(RiskLevel.HIGH) == "HIGH"


def test_to_storage_value_promotes_date_to_datetime() -> None:
    out = _to_storage_value(date(2026, 4, 26))
    assert isinstance(out, datetime)
    assert out.year == 2026
    assert out.tzinfo is not None


def test_to_storage_value_passes_through_primitives() -> None:
    assert _to_storage_value(42) == 42
    assert _to_storage_value("x") == "x"
    assert _to_storage_value(None) is None


def test_model_to_entity_skips_none_and_inserts_keys() -> None:
    job = AnalysisJob(job_id="j1", company_name="ACME", created_at=_now(), updated_at=_now())
    entity = _model_to_entity(job, "job", "j1")
    assert entity["PartitionKey"] == "job"
    assert entity["RowKey"] == "j1"
    assert entity["status"] == "pending"
    assert "finished_at" not in entity  # None skipped
    assert "user_id" not in entity


def test_model_to_entity_respects_exclude() -> None:
    snap = MonitoringSnapshot(
        company_id="c1",
        run_date=date(2026, 4, 26),
        risk_level=RiskLevel.HIGH,
        risk_score=80.0,
        analysis_job_id="job-1",
    )
    entity = _model_to_entity(snap, "c1", "20260426", exclude={"run_date"})
    assert "run_date" not in entity
    assert entity["risk_level"] == "HIGH"
    assert entity["risk_score"] == 80.0
    assert entity["analysis_job_id"] == "job-1"


def test_entity_to_model_strips_reserved_keys_and_validates() -> None:
    entity = {
        "PartitionKey": "job",
        "RowKey": "j1",
        "Timestamp": "ignored",
        "etag": "ignored",
        "job_id": "j1",
        "company_name": "ACME",
        "status": "collecting",
        "created_at": _now(),
        "updated_at": _now(),
    }
    job = _entity_to_model(entity, AnalysisJob)
    assert job.status is JobStatus.COLLECTING
    assert job.company_name == "ACME"


def _mock_table_client() -> MagicMock:
    client = MagicMock()
    client.upsert_entity = AsyncMock()
    client.update_entity = AsyncMock()
    client.get_entity = AsyncMock()
    client.delete_entity = AsyncMock()
    client.query_entities = MagicMock()
    return client


def _async_iter(items: list[dict]) -> object:
    async def gen() -> object:
        for item in items:
            yield item

    return gen()


async def test_analysis_jobs_repo_upsert_and_get_round_trip() -> None:
    client = _mock_table_client()
    repo = AnalysisJobsRepo(client, "AnalysisJobs")

    job = AnalysisJob(job_id="j1", company_name="ACME", created_at=_now(), updated_at=_now())
    await repo.upsert(job)
    client.upsert_entity.assert_awaited_once()
    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "job"
    assert sent["RowKey"] == "j1"
    assert sent["status"] == "pending"

    client.get_entity = AsyncMock(return_value=sent)
    fetched = await repo.get("j1")
    assert fetched.job_id == "j1"
    assert fetched.status is JobStatus.PENDING


async def test_analysis_jobs_repo_get_raises_entity_not_found() -> None:
    client = _mock_table_client()
    client.get_entity = AsyncMock(side_effect=ResourceNotFoundError("missing"))
    repo = AnalysisJobsRepo(client, "AnalysisJobs")

    with pytest.raises(EntityNotFoundError) as ei:
        await repo.get("missing-id")
    assert ei.value.partition_key == "job"
    assert ei.value.row_key == "missing-id"


async def test_analysis_jobs_repo_update_status_only_sets_provided_fields() -> None:
    client = _mock_table_client()
    repo = AnalysisJobsRepo(client, "AnalysisJobs")

    await repo.update_status("j1", JobStatus.COLLECTING, current_agent=AgentName.COLLECT)

    client.update_entity.assert_awaited_once()
    sent = client.update_entity.call_args.args[0]
    assert sent["status"] == "collecting"
    assert sent["current_agent"] == "collect"
    assert "report_blob_path" not in sent
    assert "error_message" not in sent
    assert "updated_at" in sent


async def test_analysis_jobs_repo_upsert_wraps_failure() -> None:
    client = _mock_table_client()
    client.upsert_entity = AsyncMock(side_effect=RuntimeError("boom"))
    repo = AnalysisJobsRepo(client, "AnalysisJobs")

    job = AnalysisJob(job_id="j1", company_name="ACME", created_at=_now(), updated_at=_now())
    with pytest.raises(TableStorageError, match="upsert failed"):
        await repo.upsert(job)


async def test_agent_status_repo_uses_job_id_partition() -> None:
    client = _mock_table_client()
    repo = AgentStatusRepo(client, "AgentStatus")

    status = AgentStatus(job_id="j1", agent_name=AgentName.ANALYZE)
    await repo.upsert(status)

    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "j1"
    assert sent["RowKey"] == "analyze"
    assert sent["status"] == "pending"


async def test_agent_status_repo_update_done_computes_duration() -> None:
    client = _mock_table_client()
    started = datetime(2026, 4, 26, 9, 0, 0, tzinfo=UTC)
    client.get_entity = AsyncMock(return_value={"started_at": started, "status": "running"})
    repo = AgentStatusRepo(client, "AgentStatus")

    with patch("src.storage.table_store._now") as now_fn:
        now_fn.return_value = datetime(2026, 4, 26, 9, 0, 30, tzinfo=UTC)
        await repo.update_done("j1", AgentName.COLLECT, output_blob_path="path")

    sent = client.update_entity.call_args.args[0]
    assert sent["status"] == "done"
    assert sent["duration_sec"] == 30
    assert sent["output_blob_path"] == "path"


async def test_companies_repo_upsert_uses_constant_partition() -> None:
    client = _mock_table_client()
    repo = CompaniesRepo(client, "Companies")

    company = Company(company_id="c1", company_name="ACME", created_at=_now(), updated_at=_now())
    await repo.upsert(company)

    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "company"
    assert sent["RowKey"] == "c1"


async def test_companies_repo_find_by_name_returns_match() -> None:
    client = _mock_table_client()
    entity = {
        "PartitionKey": "company",
        "RowKey": "c1",
        "company_id": "c1",
        "company_name": "ACME",
        "created_at": _now(),
        "updated_at": _now(),
    }
    client.query_entities = MagicMock(side_effect=lambda *a, **k: _async_iter([entity]))
    repo = CompaniesRepo(client, "Companies")

    found = await repo.find_by_name("ACME")
    assert found is not None
    assert found.company_id == "c1"
    sent_filter = client.query_entities.call_args.args[0]
    assert "company_name eq 'ACME'" in sent_filter


async def test_companies_repo_find_by_name_returns_none_when_missing() -> None:
    client = _mock_table_client()
    client.query_entities = MagicMock(side_effect=lambda *a, **k: _async_iter([]))
    repo = CompaniesRepo(client, "Companies")

    assert await repo.find_by_name("Unknown") is None


async def test_companies_repo_find_by_name_escapes_single_quote() -> None:
    client = _mock_table_client()
    client.query_entities = MagicMock(side_effect=lambda *a, **k: _async_iter([]))
    repo = CompaniesRepo(client, "Companies")

    await repo.find_by_name("O'Hara")
    sent_filter = client.query_entities.call_args.args[0]
    assert "O''Hara" in sent_filter


async def test_financial_raw_repo_row_key_format() -> None:
    client = _mock_table_client()
    repo = FinancialRawRepo(client, "FinancialRaw")

    raw = FinancialRaw(
        job_id="j1",
        company_id="c1",
        fiscal_year=2023,
        fiscal_type="annual",
        revenue=1_000_000_000_000,
        created_at=_now(),
    )
    await repo.insert(raw)

    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "j1"
    assert sent["RowKey"] == "2023-annual"
    assert sent["revenue"] == 1_000_000_000_000


async def test_financial_metrics_repo_row_key_is_year() -> None:
    client = _mock_table_client()
    repo = FinancialMetricsRepo(client, "FinancialMetrics")

    metrics = FinancialMetrics(
        job_id="j1",
        company_id="c1",
        base_year=2023,
        risk_level=RiskLevel.HIGH,
        risk_score=72.5,
        created_at=_now(),
    )
    await repo.insert(metrics)

    sent = client.upsert_entity.call_args.args[0]
    assert sent["RowKey"] == "2023"
    assert sent["risk_level"] == "HIGH"


async def test_monitoring_snapshots_repo_writes_yyyymmdd_row_key() -> None:
    client = _mock_table_client()
    repo = MonitoringSnapshotsRepo(client, "MonitoringSnapshots")

    snap = MonitoringSnapshot(
        company_id="c1",
        run_date=date(2026, 4, 26),
        risk_level=RiskLevel.MEDIUM,
        risk_score=55.0,
        analysis_job_id="job-1",
        news_count=3,
        lawsuit_count=1,
    )
    await repo.insert(snap)

    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "c1"
    assert sent["RowKey"] == "20260426"
    assert "run_date" not in sent
    assert sent["risk_score"] == 55.0
    assert sent["analysis_job_id"] == "job-1"


async def test_monitoring_snapshots_list_round_trips_run_date() -> None:
    client = _mock_table_client()

    entities = [
        {
            "PartitionKey": "c1",
            "RowKey": "20260101",
            "company_id": "c1",
            "risk_level": "LOW",
            "risk_score": 10.0,
            "analysis_job_id": "job-a",
            "news_count": 0,
            "lawsuit_count": 0,
            "summary": "",
            "key_signals": "",
        },
        {
            "PartitionKey": "c1",
            "RowKey": "20260426",
            "company_id": "c1",
            "risk_level": "HIGH",
            "risk_score": 75.0,
            "analysis_job_id": "job-b",
            "news_count": 5,
            "lawsuit_count": 2,
            "summary": "",
            "key_signals": "",
        },
    ]

    client.query_entities = MagicMock(side_effect=lambda *a, **k: _async_iter(entities))
    repo = MonitoringSnapshotsRepo(client, "MonitoringSnapshots")

    snaps = await repo.list_for_company("c1")
    assert {s.run_date for s in snaps} == {date(2026, 1, 1), date(2026, 4, 26)}

    latest = await repo.get_latest("c1")
    assert latest is not None
    assert latest.run_date == date(2026, 4, 26)
    assert latest.risk_level is RiskLevel.HIGH


async def test_scheduler_state_repo_get_returns_none_when_missing() -> None:
    client = _mock_table_client()
    client.get_entity = AsyncMock(side_effect=ResourceNotFoundError("missing"))
    repo = SchedulerStateRepo(client, "SchedulerState")

    state = await repo.get()
    assert state is None


async def test_scheduler_state_repo_upsert_uses_constant_keys() -> None:
    client = _mock_table_client()
    repo = SchedulerStateRepo(client, "SchedulerState")

    await repo.upsert(SchedulerState(run_count=3))

    sent = client.upsert_entity.call_args.args[0]
    assert sent["PartitionKey"] == "scheduler"
    assert sent["RowKey"] == "monitoring_batch"
    assert sent["run_count"] == 3


def test_table_store_constructor_rejects_empty_connection_string() -> None:
    with pytest.raises(TableStorageError):
        TableStore("")


def test_table_store_exposes_all_repos() -> None:
    with patch("src.storage.table_store.TableServiceClient") as svc_cls:
        svc = MagicMock()
        svc.get_table_client = MagicMock()
        svc.close = AsyncMock()
        svc_cls.from_connection_string.return_value = svc
        store = TableStore(CONNECTION_STRING)

    assert isinstance(store.jobs, AnalysisJobsRepo)
    assert isinstance(store.agent_status, AgentStatusRepo)
    assert isinstance(store.companies, CompaniesRepo)
    assert isinstance(store.financial_raw, FinancialRawRepo)
    assert isinstance(store.financial_metrics, FinancialMetricsRepo)
    assert isinstance(store.monitoring_snapshots, MonitoringSnapshotsRepo)
    assert isinstance(store.scheduler_state, SchedulerStateRepo)
    assert len(ALL_TABLES) == 10
    assert svc.get_table_client.call_count == 10


async def test_table_store_initialize_tables_swallows_exists() -> None:
    from azure.core.exceptions import ResourceExistsError

    with patch("src.storage.table_store.TableServiceClient") as svc_cls:
        svc = MagicMock()
        svc.get_table_client = MagicMock()
        svc.close = AsyncMock()
        svc.create_table = AsyncMock(side_effect=ResourceExistsError("exists"))
        svc_cls.from_connection_string.return_value = svc
        store = TableStore(CONNECTION_STRING)
        await store.initialize_tables()

    assert svc.create_table.await_count == 10
