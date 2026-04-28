import json
from datetime import UTC, datetime
from uuid import uuid4

from src.agents.collector.clients import NaverNewsClient
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service
from src.agents.financial.schemas import AnalyzeRequest
from src.agents.financial.service import analyze_financials_service
from src.agents.monitoring.schemas import (
    MonitorRunNowRequest,
    MonitorRunNowResponse,
)
from src.common.anthropic_client import AnthropicClient
from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError, SimsaSukgoError
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import (
    AgentName,
    AgentStatus,
    AgentStatusValue,
    AnalysisJob,
    AnalysisJobRef,
    JobStatus,
    MonitoringSnapshot,
)
from src.storage.table_store import TableStore

logger = get_logger(__name__)

_MONITORING_USER_ID = "system:monitoring"
_MAX_KEY_SIGNALS = 5
_MAX_SUMMARY_CHARS = 1000


class MonitoringTargetInactiveError(SimsaSukgoError):
    def __init__(self, company_id: str) -> None:
        super().__init__(
            f"monitoring target is inactive: company_id={company_id}. "
            "call monitor_register again to reactivate."
        )
        self.company_id = company_id


def _snapshot_blob_path(company_id: str, run_at: datetime) -> str:
    return f"monitoring/{company_id}/{run_at.strftime('%Y%m%d')}/snapshot.json"


async def monitor_run_now_service(
    request: MonitorRunNowRequest,
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
    anthropic: AnthropicClient,
) -> MonitorRunNowResponse:
    """
    Re-run collect + analyze for a registered monitoring target, persist a
    snapshot. Skips report generation (monitoring is lightweight check, not
    full report). Alert sending is done in PR 3.
    """
    # 1. Validate target exists + active
    try:
        target = await tables.monitoring_targets.get(request.company_id)
    except EntityNotFoundError as exc:
        logger.warning("monitor.run_now.target_not_found", company_id=request.company_id)
        raise EntityNotFoundError("MonitoringTargets", "company", request.company_id) from exc

    if not target.is_active:
        logger.warning("monitor.run_now.target_inactive", company_id=request.company_id)
        raise MonitoringTargetInactiveError(request.company_id)

    # 2. Create new AnalysisJob (mimics create_analysis_job, no files/prompt)
    job_id = str(uuid4())
    started_at = datetime.now(UTC)
    logger.info(
        "monitor.run_now.start",
        company_id=request.company_id,
        company_name=target.company_name,
        analysis_job_id=job_id,
    )

    await tables.jobs.upsert(
        AnalysisJob(
            job_id=job_id,
            company_name=target.company_name,
            user_id=_MONITORING_USER_ID,
            status=JobStatus.PENDING,
            input_blob_prefix=f"jobs/{job_id}/input/",
            created_at=started_at,
            updated_at=started_at,
        )
    )
    await tables.jobs_ref.upsert(
        AnalysisJobRef(
            job_id=job_id,
            company_id=request.company_id,
            company_name=target.company_name,
            status=JobStatus.PENDING,
            created_at=started_at,
        )
    )
    # Only collect + analyze are needed for monitoring runs (no report).
    for agent_name in (AgentName.COLLECT, AgentName.ANALYZE):
        await tables.agent_status.upsert(
            AgentStatus(
                job_id=job_id,
                agent_name=agent_name,
                status=AgentStatusValue.PENDING,
            )
        )

    # 3. Run collect + analyze (each service handles its own status updates).
    # Failures inside services already mark AnalysisJobs.status=FAILED — we just
    # propagate so the caller knows.
    await collect_company_data_service(
        CollectRequest(job_id=job_id, company_name=target.company_name),
        blob,
        tables,
        naver,
    )
    await analyze_financials_service(
        AnalyzeRequest(job_id=job_id),
        blob,
        tables,
        anthropic,
    )

    # 4. Mark AnalysisJob done (we skip report intentionally for monitoring).
    finished_at = datetime.now(UTC)
    await tables.jobs.update_status(
        job_id,
        JobStatus.DONE,
        finished_at=finished_at,
    )

    # 5. Read collect/raw + analyze/result for snapshot details.
    raw_bytes = await blob.download(f"jobs/{job_id}/collect/raw.json")
    raw = json.loads(raw_bytes.decode("utf-8"))
    result_bytes = await blob.download(f"jobs/{job_id}/analyze/result.json")
    result = json.loads(result_bytes.decode("utf-8"))

    risk_level = RiskLevel(result["risk_level"])
    risk_score = float(result["risk_score"])
    summary = (result.get("summary") or "")[:_MAX_SUMMARY_CHARS]
    key_risk_factors = result.get("key_risk_factors") or []
    news_count = len(raw.get("news") or [])
    lawsuit_count = len(raw.get("lawsuits") or [])

    previous_risk_level = target.last_risk_level
    risk_changed = previous_risk_level != risk_level

    # 6. Save full snapshot.json to monitoring/{company_id}/{YYYYMMDD}/snapshot.json
    snapshot_path = _snapshot_blob_path(request.company_id, finished_at)
    snapshot_payload = {
        "company_id": request.company_id,
        "company_name": target.company_name,
        "run_at": finished_at.isoformat(),
        "run_date": finished_at.date().isoformat(),
        "analysis_job_id": job_id,
        "previous_risk_level": previous_risk_level.value if previous_risk_level else None,
        "risk_level": risk_level.value,
        "risk_score": risk_score,
        "risk_changed": risk_changed,
        "summary": result.get("summary"),
        "key_risk_factors": key_risk_factors,
        "positive_signals": result.get("positive_signals") or [],
        "data_gaps": result.get("data_gaps") or [],
        "news_count": news_count,
        "lawsuit_count": lawsuit_count,
        "news_top_titles": [n.get("title") for n in (raw.get("news") or [])[:10]],
        "model": result.get("model"),
    }
    await blob.upload(
        snapshot_path,
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )

    # 7. Insert MonitoringSnapshots row (UI list-view metadata)
    key_signals = " / ".join(key_risk_factors[:_MAX_KEY_SIGNALS])
    await tables.monitoring_snapshots.insert(
        MonitoringSnapshot(
            company_id=request.company_id,
            run_date=finished_at.date(),
            risk_level=risk_level,
            risk_score=risk_score,
            analysis_job_id=job_id,
            news_count=news_count,
            lawsuit_count=lawsuit_count,
            summary=summary,
            key_signals=key_signals,
            snapshot_blob_path=snapshot_path,
        )
    )

    # 8. Update MonitoringTarget last_run_at + last_risk_level
    await tables.monitoring_targets.update_after_run(request.company_id, finished_at, risk_level)

    logger.info(
        "monitor.run_now.done",
        company_id=request.company_id,
        analysis_job_id=job_id,
        risk_level=risk_level.value,
        risk_score=risk_score,
        risk_changed=risk_changed,
        previous_risk_level=previous_risk_level.value if previous_risk_level else None,
    )

    return MonitorRunNowResponse(
        company_id=request.company_id,
        company_name=target.company_name,
        analysis_job_id=job_id,
        run_date=finished_at.date(),
        risk_level=risk_level,
        risk_score=risk_score,
        previous_risk_level=previous_risk_level,
        risk_changed=risk_changed,
        snapshot_blob_path=snapshot_path,
    )
