from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.monitoring.scheduler import (
    needs_catchup,
    run_monitoring_batch,
)
from src.storage.schemas import MonitoringTarget, SchedulerState


def _now() -> datetime:
    return datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _target(company_id: str) -> MonitoringTarget:
    return MonitoringTarget(
        company_id=company_id,
        company_name=f"Co-{company_id}",
        recipient_email="ops@example.com",
        origin_job_id="job-origin",
        registered_at=_now(),
        is_active=True,
    )


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.monitoring_batch_cron = "0 9 1 */3 *"
    s.monitoring_catchup_threshold_days = 90
    return s


def _make_tables(
    *,
    targets: list[MonitoringTarget] | None = None,
    state: SchedulerState | None = None,
) -> MagicMock:
    tables = MagicMock()
    tables.monitoring_targets = MagicMock()
    tables.monitoring_targets.list_active = AsyncMock(return_value=targets or [])
    tables.scheduler_state = MagicMock()
    tables.scheduler_state.get = AsyncMock(return_value=state)
    tables.scheduler_state.upsert = AsyncMock()
    return tables


# ===== run_monitoring_batch =====


@patch("src.agents.monitoring.scheduler.monitor_run_now_service", new_callable=AsyncMock)
async def test_batch_iterates_all_active_targets(mock_run) -> None:
    targets = [_target("c-1"), _target("c-2"), _target("c-3")]
    tables = _make_tables(targets=targets)
    settings = _make_settings()

    result = await run_monitoring_batch(
        blob=MagicMock(),
        tables=tables,
        naver=MagicMock(),
        anthropic=MagicMock(),
        gmail=MagicMock(),
        settings=settings,
    )

    assert mock_run.await_count == 3
    company_ids = {call.args[0].company_id for call in mock_run.call_args_list}
    assert company_ids == {"c-1", "c-2", "c-3"}
    assert result == {"target_count": 3, "success": 3, "failed": 0}


@patch("src.agents.monitoring.scheduler.monitor_run_now_service", new_callable=AsyncMock)
async def test_batch_continues_on_per_target_failure(mock_run) -> None:
    mock_run.side_effect = [None, RuntimeError("c-2 boom"), None]
    targets = [_target("c-1"), _target("c-2"), _target("c-3")]
    tables = _make_tables(targets=targets)

    result = await run_monitoring_batch(
        blob=MagicMock(),
        tables=tables,
        naver=MagicMock(),
        anthropic=MagicMock(),
        gmail=MagicMock(),
        settings=_make_settings(),
    )

    assert mock_run.await_count == 3
    assert result == {"target_count": 3, "success": 2, "failed": 1}


@patch("src.agents.monitoring.scheduler.monitor_run_now_service", new_callable=AsyncMock)
async def test_batch_empty_targets_still_updates_state(mock_run) -> None:
    tables = _make_tables(targets=[])

    result = await run_monitoring_batch(
        blob=MagicMock(),
        tables=tables,
        naver=MagicMock(),
        anthropic=MagicMock(),
        gmail=MagicMock(),
        settings=_make_settings(),
    )

    mock_run.assert_not_called()
    tables.scheduler_state.upsert.assert_awaited_once()
    upserted = tables.scheduler_state.upsert.call_args.args[0]
    assert upserted.run_count == 1
    assert upserted.last_run_at is not None
    assert result == {"target_count": 0, "success": 0, "failed": 0}


@patch("src.agents.monitoring.scheduler.monitor_run_now_service", new_callable=AsyncMock)
async def test_batch_increments_run_count(mock_run) -> None:
    tables = _make_tables(
        targets=[_target("c-1")],
        state=SchedulerState(last_run_at=_now() - timedelta(days=90), run_count=5),
    )

    await run_monitoring_batch(
        blob=MagicMock(),
        tables=tables,
        naver=MagicMock(),
        anthropic=MagicMock(),
        gmail=MagicMock(),
        settings=_make_settings(),
    )

    upserted = tables.scheduler_state.upsert.call_args.args[0]
    assert upserted.run_count == 6


# ===== needs_catchup =====


async def test_needs_catchup_false_when_no_state() -> None:
    tables = _make_tables(state=None)
    assert await needs_catchup(tables, threshold_days=90) is False


async def test_needs_catchup_false_when_state_has_no_last_run() -> None:
    tables = _make_tables(state=SchedulerState(last_run_at=None, run_count=0))
    assert await needs_catchup(tables, threshold_days=90) is False


async def test_needs_catchup_false_when_recent() -> None:
    tables = _make_tables(
        state=SchedulerState(last_run_at=datetime.now(UTC) - timedelta(days=30), run_count=1),
    )
    assert await needs_catchup(tables, threshold_days=90) is False


async def test_needs_catchup_true_when_stale() -> None:
    tables = _make_tables(
        state=SchedulerState(last_run_at=datetime.now(UTC) - timedelta(days=120), run_count=1),
    )
    assert await needs_catchup(tables, threshold_days=90) is True


async def test_needs_catchup_true_at_exact_threshold() -> None:
    tables = _make_tables(
        state=SchedulerState(last_run_at=datetime.now(UTC) - timedelta(days=90), run_count=1),
    )
    assert await needs_catchup(tables, threshold_days=90) is True
