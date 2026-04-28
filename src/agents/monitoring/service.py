from datetime import UTC, datetime

from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorDeregisterResponse,
    MonitorListResponse,
    MonitorRegisterRequest,
    MonitorRegisterResponse,
    MonitorTargetListItem,
)
from src.common.exceptions import EntityNotFoundError
from src.config.logging import get_logger
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
