from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agents.collector.clients import NaverNewsClient
from src.agents.monitoring.gmail_client import GmailClient
from src.agents.monitoring.run_service import monitor_run_now_service
from src.agents.monitoring.schemas import MonitorRunNowRequest
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger
from src.config.settings import Settings
from src.storage.blob_store import BlobStore
from src.storage.schemas import SchedulerState
from src.storage.table_store import TableStore

logger = get_logger(__name__)

_BATCH_JOB_ID = "monitoring_batch"


async def run_monitoring_batch(
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
    anthropic: AnthropicClient,
    gmail: GmailClient,
    settings: Settings,
) -> dict[str, int]:
    """Iterate all active MonitoringTargets, run monitor_run_now_service for each.

    Per-target failures are logged and don't abort the batch. SchedulerState is
    upserted with last_run_at/run_count regardless of per-target outcomes.
    """
    started_at = datetime.now(UTC)
    targets = await tables.monitoring_targets.list_active()
    logger.info("monitor.batch.start", target_count=len(targets))

    success = 0
    failed = 0
    for target in targets:
        try:
            await monitor_run_now_service(
                MonitorRunNowRequest(company_id=target.company_id),
                blob,
                tables,
                naver,
                anthropic,
                gmail=gmail,
                settings=settings,
            )
            success += 1
        except Exception as exc:
            failed += 1
            logger.error(
                "monitor.batch.target_failed",
                company_id=target.company_id,
                error=str(exc),
            )

    state = await tables.scheduler_state.get()
    await tables.scheduler_state.upsert(
        SchedulerState(
            last_run_at=started_at,
            next_run_at=None,
            run_count=(state.run_count if state else 0) + 1,
        )
    )

    logger.info(
        "monitor.batch.done",
        target_count=len(targets),
        success=success,
        failed=failed,
    )
    return {"target_count": len(targets), "success": success, "failed": failed}


async def needs_catchup(tables: TableStore, *, threshold_days: int) -> bool:
    """True if last batch run is older than threshold (or never ran)."""
    state = await tables.scheduler_state.get()
    if state is None or state.last_run_at is None:
        # Never ran. Don't catchup on first ever startup — let cron handle it
        # so demo / fresh deploy doesn't fire batch immediately.
        return False
    elapsed = datetime.now(UTC) - state.last_run_at
    return elapsed >= timedelta(days=threshold_days)


def setup_scheduler(
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
    anthropic: AnthropicClient,
    gmail: GmailClient,
    settings: Settings,
) -> AsyncIOScheduler:
    """Create + start an AsyncIOScheduler with the monitoring batch cron job."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(settings.monitoring_batch_cron, timezone="UTC")
    scheduler.add_job(
        run_monitoring_batch,
        trigger=trigger,
        kwargs={
            "blob": blob,
            "tables": tables,
            "naver": naver,
            "anthropic": anthropic,
            "gmail": gmail,
            "settings": settings,
        },
        id=_BATCH_JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    next_fire = scheduler.get_job(_BATCH_JOB_ID).next_run_time
    logger.info(
        "monitor.scheduler.started",
        cron=settings.monitoring_batch_cron,
        next_run_at=next_fire.isoformat() if next_fire else None,
    )
    return scheduler


async def schedule_catchup(scheduler: AsyncIOScheduler, **kwargs: Any) -> None:
    """Fire a one-off compensating batch ASAP (next event-loop tick)."""
    scheduler.add_job(
        run_monitoring_batch,
        trigger="date",
        run_date=datetime.now(UTC),
        kwargs=kwargs,
        id=f"{_BATCH_JOB_ID}_catchup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info("monitor.scheduler.catchup_scheduled")


def shutdown_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    if scheduler is None or not scheduler.running:
        return
    scheduler.shutdown(wait=False)
    logger.info("monitor.scheduler.stopped")
