from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import UpdateMode
from azure.data.tables.aio import TableClient, TableServiceClient
from pydantic import BaseModel

from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError, TableStorageError
from src.config.logging import get_logger
from src.storage.schemas import (
    AgentName,
    AgentStatus,
    AgentStatusValue,
    AlertHistory,
    AnalysisJob,
    AnalysisJobRef,
    Company,
    FinancialMetrics,
    FinancialRaw,
    JobStatus,
    MonitoringSnapshot,
    MonitoringTarget,
    SchedulerState,
)

logger = get_logger(__name__)

TABLE_ANALYSIS_JOBS = "AnalysisJobs"
TABLE_AGENT_STATUS = "AgentStatus"
TABLE_COMPANIES = "Companies"
TABLE_FINANCIAL_RAW = "FinancialRaw"
TABLE_FINANCIAL_METRICS = "FinancialMetrics"
TABLE_ANALYSIS_JOBS_REF = "AnalysisJobsRef"
TABLE_MONITORING_TARGETS = "MonitoringTargets"
TABLE_MONITORING_SNAPSHOTS = "MonitoringSnapshots"
TABLE_ALERT_HISTORY = "AlertHistory"
TABLE_SCHEDULER_STATE = "SchedulerState"

ALL_TABLES: tuple[str, ...] = (
    TABLE_ANALYSIS_JOBS,
    TABLE_AGENT_STATUS,
    TABLE_COMPANIES,
    TABLE_FINANCIAL_RAW,
    TABLE_FINANCIAL_METRICS,
    TABLE_ANALYSIS_JOBS_REF,
    TABLE_MONITORING_TARGETS,
    TABLE_MONITORING_SNAPSHOTS,
    TABLE_ALERT_HISTORY,
    TABLE_SCHEDULER_STATE,
)

_RESERVED_KEYS = {"PartitionKey", "RowKey", "Timestamp", "etag"}


def _to_storage_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    return value


def _model_to_entity(
    model: BaseModel, partition_key: str, row_key: str, *, exclude: set[str] | None = None
) -> dict[str, Any]:
    exclude_set = exclude or set()
    data = model.model_dump(mode="python")
    entity: dict[str, Any] = {"PartitionKey": partition_key, "RowKey": row_key}
    for key, value in data.items():
        if key in exclude_set or value is None:
            continue
        entity[key] = _to_storage_value(value)
    return entity


def _entity_to_model[T: BaseModel](
    entity: dict[str, Any],
    model_cls: type[T],
    *,
    extra: dict[str, Any] | None = None,
) -> T:
    payload = {k: v for k, v in entity.items() if k not in _RESERVED_KEYS}
    if extra:
        payload.update(extra)
    return model_cls.model_validate(payload)


def _now() -> datetime:
    return datetime.now(UTC)


class _RepoBase:
    def __init__(self, client: TableClient, table_name: str) -> None:
        self._client = client
        self._table = table_name

    async def _upsert(self, entity: dict[str, Any]) -> None:
        try:
            await self._client.upsert_entity(entity, mode=UpdateMode.REPLACE)
        except Exception as exc:
            pk = entity.get("PartitionKey")
            rk = entity.get("RowKey")
            raise TableStorageError(f"upsert failed: table={self._table} pk={pk} rk={rk}") from exc

    async def _merge(self, entity: dict[str, Any]) -> None:
        pk = entity["PartitionKey"]
        rk = entity["RowKey"]
        try:
            await self._client.update_entity(entity, mode=UpdateMode.MERGE)
        except ResourceNotFoundError as exc:
            raise EntityNotFoundError(self._table, pk, rk) from exc
        except Exception as exc:
            raise TableStorageError(f"merge failed: table={self._table} pk={pk} rk={rk}") from exc

    async def _get(self, partition_key: str, row_key: str) -> dict[str, Any]:
        try:
            return await self._client.get_entity(partition_key, row_key)
        except ResourceNotFoundError as exc:
            raise EntityNotFoundError(self._table, partition_key, row_key) from exc

    async def _query(self, query_filter: str) -> list[dict[str, Any]]:
        try:
            return [entity async for entity in self._client.query_entities(query_filter)]
        except Exception as exc:
            raise TableStorageError(
                f"query failed: table={self._table} filter={query_filter}"
            ) from exc

    async def _delete(self, partition_key: str, row_key: str) -> None:
        try:
            await self._client.delete_entity(partition_key, row_key)
        except ResourceNotFoundError:
            pass


class AnalysisJobsRepo(_RepoBase):
    PARTITION = "job"

    async def upsert(self, job: AnalysisJob) -> None:
        await self._upsert(_model_to_entity(job, self.PARTITION, job.job_id))

    async def get(self, job_id: str) -> AnalysisJob:
        return _entity_to_model(await self._get(self.PARTITION, job_id), AnalysisJob)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        current_agent: AgentName | None = None,
        report_blob_path: str | None = None,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        entity: dict[str, Any] = {
            "PartitionKey": self.PARTITION,
            "RowKey": job_id,
            "status": status.value,
            "updated_at": _now(),
        }
        if current_agent is not None:
            entity["current_agent"] = current_agent.value
        if report_blob_path is not None:
            entity["report_blob_path"] = report_blob_path
        if error_message is not None:
            entity["error_message"] = error_message
        if finished_at is not None:
            entity["finished_at"] = finished_at
        await self._merge(entity)

    async def list_running(self) -> list[AnalysisJob]:
        results = await self._query(
            "PartitionKey eq 'job' and "
            "(status eq 'collecting' or status eq 'analyzing' or status eq 'reporting')"
        )
        return [_entity_to_model(e, AnalysisJob) for e in results]


class AgentStatusRepo(_RepoBase):
    async def upsert(self, status: AgentStatus) -> None:
        await self._upsert(_model_to_entity(status, status.job_id, status.agent_name.value))

    async def get(self, job_id: str, agent_name: AgentName) -> AgentStatus:
        return _entity_to_model(await self._get(job_id, agent_name.value), AgentStatus)

    async def list_for_job(self, job_id: str) -> list[AgentStatus]:
        results = await self._query(f"PartitionKey eq '{job_id}'")
        return [_entity_to_model(e, AgentStatus) for e in results]

    async def update_running(self, job_id: str, agent_name: AgentName) -> None:
        await self._merge(
            {
                "PartitionKey": job_id,
                "RowKey": agent_name.value,
                "status": AgentStatusValue.RUNNING.value,
                "started_at": _now(),
            }
        )

    async def update_done(
        self, job_id: str, agent_name: AgentName, output_blob_path: str | None = None
    ) -> None:
        finished = _now()
        existing = await self._get(job_id, agent_name.value)
        started_at = existing.get("started_at")
        duration_sec = None
        if isinstance(started_at, datetime):
            duration_sec = int((finished - started_at).total_seconds())

        entity: dict[str, Any] = {
            "PartitionKey": job_id,
            "RowKey": agent_name.value,
            "status": AgentStatusValue.DONE.value,
            "finished_at": finished,
        }
        if duration_sec is not None:
            entity["duration_sec"] = duration_sec
        if output_blob_path is not None:
            entity["output_blob_path"] = output_blob_path
        await self._merge(entity)

    async def update_failed(self, job_id: str, agent_name: AgentName, error_detail: str) -> None:
        await self._merge(
            {
                "PartitionKey": job_id,
                "RowKey": agent_name.value,
                "status": AgentStatusValue.FAILED.value,
                "finished_at": _now(),
                "error_detail": error_detail,
            }
        )


class CompaniesRepo(_RepoBase):
    PARTITION = "company"

    async def upsert(self, company: Company) -> None:
        await self._upsert(_model_to_entity(company, self.PARTITION, company.company_id))

    async def get(self, company_id: str) -> Company:
        return _entity_to_model(await self._get(self.PARTITION, company_id), Company)


class FinancialRawRepo(_RepoBase):
    @staticmethod
    def _row_key(fiscal_year: int, fiscal_type: str) -> str:
        return f"{fiscal_year}-{fiscal_type}"

    async def insert(self, raw: FinancialRaw) -> None:
        rk = self._row_key(raw.fiscal_year, raw.fiscal_type)
        await self._upsert(_model_to_entity(raw, raw.job_id, rk))

    async def query_by_job(self, job_id: str) -> list[FinancialRaw]:
        results = await self._query(f"PartitionKey eq '{job_id}'")
        return [_entity_to_model(e, FinancialRaw) for e in results]


class FinancialMetricsRepo(_RepoBase):
    async def insert(self, metrics: FinancialMetrics) -> None:
        await self._upsert(_model_to_entity(metrics, metrics.job_id, str(metrics.base_year)))

    async def query_by_job(self, job_id: str) -> list[FinancialMetrics]:
        results = await self._query(f"PartitionKey eq '{job_id}'")
        return [_entity_to_model(e, FinancialMetrics) for e in results]


class AnalysisJobsRefRepo(_RepoBase):
    async def upsert(self, ref: AnalysisJobRef) -> None:
        await self._upsert(_model_to_entity(ref, ref.company_id, ref.job_id))

    async def update_after_done(
        self, company_id: str, job_id: str, risk_level: RiskLevel, finished_at: datetime
    ) -> None:
        await self._merge(
            {
                "PartitionKey": company_id,
                "RowKey": job_id,
                "status": JobStatus.DONE.value,
                "risk_level": risk_level.value,
                "finished_at": finished_at,
            }
        )

    async def list_for_company(self, company_id: str) -> list[AnalysisJobRef]:
        results = await self._query(f"PartitionKey eq '{company_id}'")
        return [_entity_to_model(e, AnalysisJobRef) for e in results]


class MonitoringTargetsRepo(_RepoBase):
    PARTITION = "company"

    async def upsert(self, target: MonitoringTarget) -> None:
        await self._upsert(_model_to_entity(target, self.PARTITION, target.company_id))

    async def get(self, company_id: str) -> MonitoringTarget:
        return _entity_to_model(await self._get(self.PARTITION, company_id), MonitoringTarget)

    async def deactivate(self, company_id: str) -> None:
        await self._merge(
            {"PartitionKey": self.PARTITION, "RowKey": company_id, "is_active": False}
        )

    async def update_after_run(
        self, company_id: str, last_run_at: datetime, last_risk_level: RiskLevel
    ) -> None:
        await self._merge(
            {
                "PartitionKey": self.PARTITION,
                "RowKey": company_id,
                "last_run_at": last_run_at,
                "last_risk_level": last_risk_level.value,
            }
        )

    async def list_active(self) -> list[MonitoringTarget]:
        results = await self._query(f"PartitionKey eq '{self.PARTITION}' and is_active eq true")
        return [_entity_to_model(e, MonitoringTarget) for e in results]


class MonitoringSnapshotsRepo(_RepoBase):
    @staticmethod
    def _row_key(run_date: date) -> str:
        return run_date.strftime("%Y%m%d")

    async def insert(self, snapshot: MonitoringSnapshot) -> None:
        rk = self._row_key(snapshot.run_date)
        entity = _model_to_entity(snapshot, snapshot.company_id, rk, exclude={"run_date"})
        await self._upsert(entity)

    async def list_for_company(self, company_id: str) -> list[MonitoringSnapshot]:
        results = await self._query(f"PartitionKey eq '{company_id}'")
        return [
            _entity_to_model(
                e,
                MonitoringSnapshot,
                extra={"run_date": datetime.strptime(e["RowKey"], "%Y%m%d").date()},
            )
            for e in results
        ]

    async def get_latest(self, company_id: str) -> MonitoringSnapshot | None:
        snapshots = await self.list_for_company(company_id)
        if not snapshots:
            return None
        return max(snapshots, key=lambda s: s.run_date)


class AlertHistoryRepo(_RepoBase):
    @staticmethod
    def _row_key(sent_at: datetime) -> str:
        return sent_at.strftime("%Y%m%d-%H%M%S")

    async def insert(self, alert: AlertHistory) -> None:
        rk = self._row_key(alert.sent_at)
        await self._upsert(_model_to_entity(alert, alert.company_id, rk))

    async def list_recent(self, company_id: str, since: datetime) -> list[AlertHistory]:
        rk_since = self._row_key(since)
        results = await self._query(f"PartitionKey eq '{company_id}' and RowKey ge '{rk_since}'")
        return [_entity_to_model(e, AlertHistory) for e in results]


class SchedulerStateRepo(_RepoBase):
    PARTITION = "scheduler"
    ROW = "monitoring_batch"

    async def get(self) -> SchedulerState | None:
        try:
            entity = await self._get(self.PARTITION, self.ROW)
        except EntityNotFoundError:
            return None
        return _entity_to_model(entity, SchedulerState)

    async def upsert(self, state: SchedulerState) -> None:
        await self._upsert(_model_to_entity(state, self.PARTITION, self.ROW))


class TableStore:
    def __init__(self, connection_string: str) -> None:
        if not connection_string:
            raise TableStorageError("connection_string is empty")

        self._service_client = TableServiceClient.from_connection_string(connection_string)

        self.jobs = AnalysisJobsRepo(
            self._service_client.get_table_client(TABLE_ANALYSIS_JOBS), TABLE_ANALYSIS_JOBS
        )
        self.agent_status = AgentStatusRepo(
            self._service_client.get_table_client(TABLE_AGENT_STATUS), TABLE_AGENT_STATUS
        )
        self.companies = CompaniesRepo(
            self._service_client.get_table_client(TABLE_COMPANIES), TABLE_COMPANIES
        )
        self.financial_raw = FinancialRawRepo(
            self._service_client.get_table_client(TABLE_FINANCIAL_RAW), TABLE_FINANCIAL_RAW
        )
        self.financial_metrics = FinancialMetricsRepo(
            self._service_client.get_table_client(TABLE_FINANCIAL_METRICS),
            TABLE_FINANCIAL_METRICS,
        )
        self.jobs_ref = AnalysisJobsRefRepo(
            self._service_client.get_table_client(TABLE_ANALYSIS_JOBS_REF), TABLE_ANALYSIS_JOBS_REF
        )
        self.monitoring_targets = MonitoringTargetsRepo(
            self._service_client.get_table_client(TABLE_MONITORING_TARGETS),
            TABLE_MONITORING_TARGETS,
        )
        self.monitoring_snapshots = MonitoringSnapshotsRepo(
            self._service_client.get_table_client(TABLE_MONITORING_SNAPSHOTS),
            TABLE_MONITORING_SNAPSHOTS,
        )
        self.alert_history = AlertHistoryRepo(
            self._service_client.get_table_client(TABLE_ALERT_HISTORY), TABLE_ALERT_HISTORY
        )
        self.scheduler_state = SchedulerStateRepo(
            self._service_client.get_table_client(TABLE_SCHEDULER_STATE), TABLE_SCHEDULER_STATE
        )

    async def __aenter__(self) -> "TableStore":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._service_client.close()

    async def initialize_tables(self) -> None:
        for name in ALL_TABLES:
            try:
                await self._service_client.create_table(name)
                logger.info("table.created", table=name)
            except ResourceExistsError:
                logger.debug("table.exists", table=name)
