import json
from datetime import UTC, datetime

from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorDeregisterResponse,
    MonitorGetLatestSnapshotRequest,
    MonitorGetLatestSnapshotResponse,
    MonitorListResponse,
    MonitorRegisterRequest,
    MonitorRegisterResponse,
    MonitorTargetListItem,
)
from src.common.exceptions import EntityNotFoundError
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import MonitoringTarget
from src.storage.table_store import TableStore

logger = get_logger(__name__)


async def monitor_register_service(
    request: MonitorRegisterRequest,
    tables: TableStore,
) -> MonitorRegisterResponse:
    try:
        await tables.companies.get(request.company_id)
    except EntityNotFoundError as exc:
        logger.warning(
            "monitor.register.company_not_found",
            company_id=request.company_id,
        )
        raise EntityNotFoundError("Companies", "company", request.company_id) from exc

    target = MonitoringTarget(
        company_id=request.company_id,
        company_name=request.company_name,
        recipient_email=request.recipient_email,
        origin_job_id=request.origin_job_id,
        registered_at=datetime.now(UTC),
        is_active=True,
    )
    await tables.monitoring_targets.upsert(target)
    logger.info(
        "monitor.register.done",
        company_id=request.company_id,
        recipient=request.recipient_email,
    )
    return MonitorRegisterResponse(
        company_id=request.company_id,
        company_name=request.company_name,
        recipient_email=request.recipient_email,
    )


async def monitor_deregister_service(
    request: MonitorDeregisterRequest,
    tables: TableStore,
) -> MonitorDeregisterResponse:
    try:
        await tables.monitoring_targets.get(request.company_id)
    except EntityNotFoundError as exc:
        logger.warning(
            "monitor.deregister.target_not_found",
            company_id=request.company_id,
        )
        raise EntityNotFoundError("MonitoringTargets", "company", request.company_id) from exc

    await tables.monitoring_targets.deactivate(request.company_id)
    logger.info("monitor.deregister.done", company_id=request.company_id)
    return MonitorDeregisterResponse(company_id=request.company_id)


async def monitor_list_service(tables: TableStore) -> MonitorListResponse:
    targets = await tables.monitoring_targets.list_active()
    items = [
        MonitorTargetListItem(
            company_id=t.company_id,
            company_name=t.company_name,
            recipient_email=t.recipient_email,
            origin_job_id=t.origin_job_id,
            registered_at=t.registered_at,
            last_run_at=t.last_run_at,
            last_risk_level=t.last_risk_level,
        )
        for t in targets
    ]
    logger.info("monitor.list.done", count=len(items))
    return MonitorListResponse(count=len(items), targets=items)


async def monitor_get_latest_snapshot_service(
    request: MonitorGetLatestSnapshotRequest,
    tables: TableStore,
    blob: BlobStore,
) -> MonitorGetLatestSnapshotResponse:
    """가장 최근 monitor_run_now 결과를 풀로 반환 (상세 페이지 mount 시 1회 호출용).

    동작:
    1. MonitoringTargets 에서 company 검증 (없으면 EntityNotFoundError)
    2. MonitoringSnapshots 에서 latest row 조회 — 없으면 available=False 로 반환
    3. snapshot.snapshot_blob_path 의 풀 payload (json) 다운로드 → 응답에 매핑
    4. blob 다운로드 실패 시 Table 메타만으로 best-effort 응답 (graceful)
    """
    try:
        target = await tables.monitoring_targets.get(request.company_id)
    except EntityNotFoundError as exc:
        logger.warning("monitor.get_latest.target_not_found", company_id=request.company_id)
        raise EntityNotFoundError("MonitoringTargets", "company", request.company_id) from exc

    snapshot_row = await tables.monitoring_snapshots.get_latest(request.company_id)
    if snapshot_row is None:
        logger.info("monitor.get_latest.no_snapshot_yet", company_id=request.company_id)
        return MonitorGetLatestSnapshotResponse(
            company_id=request.company_id,
            company_name=target.company_name,
            available=False,
        )

    blob_payload: dict = {}
    if snapshot_row.snapshot_blob_path:
        try:
            data = await blob.download(snapshot_row.snapshot_blob_path)
            blob_payload = json.loads(data.decode("utf-8"))
        except Exception as exc:
            logger.warning(
                "monitor.get_latest.blob_download_failed",
                company_id=request.company_id,
                blob_path=snapshot_row.snapshot_blob_path,
                error=str(exc),
            )
            # blob 실패해도 Table 메타로 best-effort 응답 — frontend 가 빈 evidence 와
            # available=True 보고 "snapshot 은 있지만 상세 데이터 로드 실패" UX 표시 가능.

    # blob payload 우선, 없으면 Table 메타 fallback (Table 은 summary/key_signals 만 있음)
    run_at_str = blob_payload.get("run_at")
    run_at = datetime.fromisoformat(run_at_str) if isinstance(run_at_str, str) else None

    return MonitorGetLatestSnapshotResponse(
        company_id=request.company_id,
        company_name=target.company_name,
        available=True,
        run_at=run_at,
        run_date=snapshot_row.run_date,
        analysis_job_id=snapshot_row.analysis_job_id,
        previous_risk_level=blob_payload.get("previous_risk_level"),
        risk_level=snapshot_row.risk_level,
        risk_score=snapshot_row.risk_score,
        risk_changed=blob_payload.get("risk_changed"),
        summary=blob_payload.get("summary") or snapshot_row.summary or None,
        key_risk_factors=blob_payload.get("key_risk_factors") or [],
        positive_signals=blob_payload.get("positive_signals") or [],
        data_gaps=blob_payload.get("data_gaps") or [],
        news_count=blob_payload.get("news_count") or snapshot_row.news_count,
        lawsuit_count=blob_payload.get("lawsuit_count") or snapshot_row.lawsuit_count,
        news_top_titles=blob_payload.get("news_top_titles") or [],
        model=blob_payload.get("model"),
        snapshot_blob_path=snapshot_row.snapshot_blob_path,
    )
