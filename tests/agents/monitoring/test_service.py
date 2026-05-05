from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorGetLatestSnapshotRequest,
    MonitorRegisterRequest,
)
from src.agents.monitoring.service import (
    monitor_deregister_service,
    monitor_get_latest_snapshot_service,
    monitor_list_service,
    monitor_register_service,
)
from src.common.constants import RiskLevel
from src.common.exceptions import EntityNotFoundError
from src.storage.schemas import Company, MonitoringSnapshot, MonitoringTarget


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


# ===== monitor_get_latest_snapshot =====


import json  # noqa: E402  (block-internal import to keep above tests untouched)
from datetime import date  # noqa: E402


def _target_obj() -> MonitoringTarget:
    return MonitoringTarget(
        company_id="c-1",
        company_name="ACME",
        recipient_email="ops@example.com",
        origin_job_id="job-origin",
        registered_at=_now(),
        is_active=True,
        last_run_at=_now(),
        last_risk_level=RiskLevel.MEDIUM,
    )


def _snapshot_row(company_id: str = "c-1") -> MonitoringSnapshot:
    return MonitoringSnapshot(
        company_id=company_id,
        run_date=date(2026, 4, 28),
        risk_level=RiskLevel.HIGH,
        risk_score=72.1,
        analysis_job_id="aj-42",
        news_count=15,
        lawsuit_count=2,
        summary="Table 메타 summary (truncated)",
        key_signals="요인1 / 요인2",
        snapshot_blob_path="monitoring/c-1/20260428/snapshot.json",
    )


def _full_snapshot_payload() -> dict:
    return {
        "company_id": "c-1",
        "company_name": "ACME",
        "run_at": "2026-04-28T10:00:00+00:00",
        "run_date": "2026-04-28",
        "analysis_job_id": "aj-42",
        "previous_risk_level": "MEDIUM",
        "risk_level": "HIGH",
        "risk_score": 72.1,
        "risk_changed": True,
        "summary": "전반적으로 위험 신호 다수 — 주의 필요",
        "key_risk_factors": ["부채비율 급등", "연체율 상승", "감사의견 변경"],
        "positive_signals": ["순이익 흑자 유지"],
        "data_gaps": ["담보 평가서 부족"],
        "news_count": 15,
        "lawsuit_count": 2,
        "news_top_titles": ["뉴스1", "뉴스2"],
        "model": "claude-haiku-4-5-20251001",
    }


def _make_tables_for_get_latest(
    *,
    target: MonitoringTarget | None = None,
    target_side_effect=None,
    latest: MonitoringSnapshot | None = None,
) -> MagicMock:
    tables = MagicMock()
    tables.monitoring_targets = MagicMock()
    tables.monitoring_targets.get = AsyncMock(
        side_effect=target_side_effect,
        return_value=target if target_side_effect is None else None,
    )
    tables.monitoring_snapshots = MagicMock()
    tables.monitoring_snapshots.get_latest = AsyncMock(return_value=latest)
    return tables


def _make_blob(payload: dict | None = None, *, raises: bool = False) -> MagicMock:
    blob = MagicMock()
    if raises:
        blob.download = AsyncMock(side_effect=RuntimeError("blob down"))
    else:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        blob.download = AsyncMock(return_value=body)
    return blob


async def test_get_latest_snapshot_returns_full_payload_from_blob() -> None:
    tables = _make_tables_for_get_latest(target=_target_obj(), latest=_snapshot_row())
    blob = _make_blob(_full_snapshot_payload())

    request = MonitorGetLatestSnapshotRequest(company_id="c-1")
    response = await monitor_get_latest_snapshot_service(request, tables, blob)

    assert response.available is True
    assert response.company_name == "ACME"
    assert response.risk_level is RiskLevel.HIGH
    assert response.previous_risk_level is RiskLevel.MEDIUM
    assert response.risk_changed is True
    assert response.summary == "전반적으로 위험 신호 다수 — 주의 필요"
    assert response.key_risk_factors == ["부채비율 급등", "연체율 상승", "감사의견 변경"]
    assert response.positive_signals == ["순이익 흑자 유지"]
    assert response.data_gaps == ["담보 평가서 부족"]
    assert response.news_count == 15
    assert response.lawsuit_count == 2
    assert response.news_top_titles == ["뉴스1", "뉴스2"]
    assert response.model == "claude-haiku-4-5-20251001"
    assert response.snapshot_blob_path == "monitoring/c-1/20260428/snapshot.json"


async def test_get_latest_snapshot_available_false_when_no_snapshot_yet() -> None:
    """Target 은 등록됐지만 monitor_run_now 가 한 번도 안 돈 경우."""
    tables = _make_tables_for_get_latest(target=_target_obj(), latest=None)
    blob = _make_blob()  # 호출되지 않아야 함

    request = MonitorGetLatestSnapshotRequest(company_id="c-1")
    response = await monitor_get_latest_snapshot_service(request, tables, blob)

    assert response.available is False
    assert response.company_id == "c-1"
    assert response.company_name == "ACME"
    assert response.risk_level is None
    assert response.summary is None
    assert response.key_risk_factors == []
    blob.download.assert_not_called()


async def test_get_latest_snapshot_raises_when_target_not_found() -> None:
    tables = _make_tables_for_get_latest(
        target_side_effect=EntityNotFoundError("MonitoringTargets", "company", "c-x")
    )
    blob = _make_blob()
    request = MonitorGetLatestSnapshotRequest(company_id="c-x")

    with pytest.raises(EntityNotFoundError):
        await monitor_get_latest_snapshot_service(request, tables, blob)
    blob.download.assert_not_called()


async def test_get_latest_snapshot_falls_back_to_table_meta_when_blob_fails() -> None:
    """Blob 다운로드 실패 시 Table 메타로 best-effort 응답 (available=True 유지)."""
    tables = _make_tables_for_get_latest(target=_target_obj(), latest=_snapshot_row())
    blob = _make_blob(raises=True)

    request = MonitorGetLatestSnapshotRequest(company_id="c-1")
    response = await monitor_get_latest_snapshot_service(request, tables, blob)

    # available=True — snapshot row 가 있음을 알림
    assert response.available is True
    # Table 메타는 살아있어야 함
    assert response.risk_level is RiskLevel.HIGH
    assert response.risk_score == 72.1
    assert response.analysis_job_id == "aj-42"
    assert response.news_count == 15
    assert response.summary == "Table 메타 summary (truncated)"  # Table 의 summary
    # blob 이 비어 있으니 evidence list 는 빈 채로
    assert response.key_risk_factors == []
    assert response.positive_signals == []
    assert response.data_gaps == []
    assert response.snapshot_blob_path == "monitoring/c-1/20260428/snapshot.json"
