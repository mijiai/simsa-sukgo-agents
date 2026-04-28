from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorRegisterRequest,
)
from src.agents.monitoring.service import (
    monitor_deregister_service,
    monitor_list_service,
    monitor_register_service,
)
from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError
from src.storage.schemas import Company, MonitoringTarget


def _now() -> datetime:
    return datetime(2026, 4, 28, 10, 0, tzinfo=UTC)


def _company() -> Company:
    return Company(
        company_id="c-1",
        company_name="ACME",
        created_at=_now(),
        updated_at=_now(),
    )


def _make_tables(
    *,
    company_get_side_effect=None,
    target_get_side_effect=None,
    list_active_return: list[MonitoringTarget] | None = None,
) -> MagicMock:
    tables = MagicMock()
    tables.companies = MagicMock()
    tables.companies.get = AsyncMock(
        side_effect=company_get_side_effect if company_get_side_effect else None,
        return_value=_company() if company_get_side_effect is None else None,
    )

    tables.monitoring_targets = MagicMock()
    tables.monitoring_targets.upsert = AsyncMock()
    tables.monitoring_targets.deactivate = AsyncMock()
    tables.monitoring_targets.get = AsyncMock(
        side_effect=target_get_side_effect if target_get_side_effect else None,
        return_value=None if target_get_side_effect else MagicMock(),
    )
    tables.monitoring_targets.list_active = AsyncMock(
        return_value=list_active_return if list_active_return is not None else [],
    )
    return tables


# ===== monitor_register =====


async def test_register_validates_company_exists_then_upserts() -> None:
    tables = _make_tables()
    request = MonitorRegisterRequest(
        company_id="c-1",
        company_name="ACME",
        recipient_email="ops@example.com",
        origin_job_id="job-1",
    )

    response = await monitor_register_service(request, tables)

    tables.companies.get.assert_awaited_once_with("c-1")
    tables.monitoring_targets.upsert.assert_awaited_once()
    target = tables.monitoring_targets.upsert.call_args.args[0]
    assert isinstance(target, MonitoringTarget)
    assert target.company_id == "c-1"
    assert target.recipient_email == "ops@example.com"
    assert target.origin_job_id == "job-1"
    assert target.is_active is True
    assert target.registered_at is not None

    assert response.status == "registered"
    assert response.company_id == "c-1"
    assert response.recipient_email == "ops@example.com"


async def test_register_raises_when_company_not_found() -> None:
    tables = _make_tables(
        company_get_side_effect=EntityNotFoundError("Companies", "company", "c-99"),
    )
    request = MonitorRegisterRequest(
        company_id="c-99",
        company_name="UnknownCo",
        recipient_email="ops@example.com",
        origin_job_id="job-1",
    )
    with pytest.raises(EntityNotFoundError):
        await monitor_register_service(request, tables)

    tables.monitoring_targets.upsert.assert_not_called()


async def test_register_upsert_overrides_email_for_existing_target() -> None:
    tables = _make_tables()
    request = MonitorRegisterRequest(
        company_id="c-1",
        company_name="ACME",
        recipient_email="newaddress@example.com",
        origin_job_id="job-2",
    )
    await monitor_register_service(request, tables)

    target = tables.monitoring_targets.upsert.call_args.args[0]
    assert target.recipient_email == "newaddress@example.com"
    assert target.origin_job_id == "job-2"


# ===== monitor_deregister =====


async def test_deregister_marks_inactive_when_target_exists() -> None:
    tables = _make_tables()
    request = MonitorDeregisterRequest(company_id="c-1")

    response = await monitor_deregister_service(request, tables)

    tables.monitoring_targets.get.assert_awaited_once_with("c-1")
    tables.monitoring_targets.deactivate.assert_awaited_once_with("c-1")
    assert response.status == "deregistered"
    assert response.company_id == "c-1"


async def test_deregister_raises_when_target_not_found() -> None:
    tables = _make_tables(
        target_get_side_effect=EntityNotFoundError("MonitoringTargets", "company", "c-99"),
    )
    request = MonitorDeregisterRequest(company_id="c-99")

    with pytest.raises(EntityNotFoundError):
        await monitor_deregister_service(request, tables)

    tables.monitoring_targets.deactivate.assert_not_called()


# ===== monitor_list =====


async def test_list_empty_returns_zero_count() -> None:
    tables = _make_tables(list_active_return=[])
    response = await monitor_list_service(tables)
    assert response.count == 0
    assert response.targets == []


async def test_list_returns_active_targets_with_metadata() -> None:
    targets = [
        MonitoringTarget(
            company_id="c-1",
            company_name="ACME",
            recipient_email="a@example.com",
            origin_job_id="job-1",
            registered_at=_now(),
            is_active=True,
            last_run_at=_now(),
            last_risk_level=RiskLevel.MEDIUM,
        ),
        MonitoringTarget(
            company_id="c-2",
            company_name="BetaCo",
            recipient_email="b@example.com",
            origin_job_id="job-2",
            registered_at=_now(),
            is_active=True,
        ),
    ]
    tables = _make_tables(list_active_return=targets)

    response = await monitor_list_service(tables)

    assert response.count == 2
    assert response.targets[0].company_id == "c-1"
    assert response.targets[0].last_risk_level is RiskLevel.MEDIUM
    assert response.targets[0].last_run_at is not None
    assert response.targets[1].company_id == "c-2"
    assert response.targets[1].last_risk_level is None
    assert response.targets[1].last_run_at is None
