from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.monitoring.alerter import (
    is_duplicate_alert,
    maybe_send_alert,
    render_alert_html,
    render_alert_subject,
    render_alert_text,
    should_alert,
)
from src.common.constants import RiskLevel
from src.common.exceptions import GmailApiError
from src.storage.schemas import AlertHistory, AlertStatus, MonitoringSnapshot


def _now() -> datetime:
    return datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _snapshot(
    *,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    risk_score: float = 55.0,
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        company_id="c-1",
        run_date=_now().date(),
        risk_level=risk_level,
        risk_score=risk_score,
        analysis_job_id="job-1",
        news_count=10,
        summary="요약",
        key_signals="요인1 / 요인2",
        snapshot_blob_path="monitoring/c-1/20260428/snapshot.json",
    )


# ===== should_alert =====


@pytest.mark.parametrize(
    "previous,new,min_level,first_run,expected",
    [
        # 이전 None + first_run_send=True
        (None, RiskLevel.MEDIUM, RiskLevel.MEDIUM, True, True),
        (None, RiskLevel.HIGH, RiskLevel.MEDIUM, True, True),
        (None, RiskLevel.LOW, RiskLevel.MEDIUM, True, False),  # min 미만
        (None, RiskLevel.MEDIUM, RiskLevel.MEDIUM, False, False),  # first_run_send 꺼짐
        # 이전 LOW → 어느 등급으로 상승하든 (min 이상이면) 알림
        (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.MEDIUM, True, True),
        (RiskLevel.LOW, RiskLevel.HIGH, RiskLevel.MEDIUM, True, True),
        (RiskLevel.LOW, RiskLevel.CRITICAL, RiskLevel.MEDIUM, True, True),
        (RiskLevel.LOW, RiskLevel.LOW, RiskLevel.MEDIUM, True, False),  # 동일
        # MEDIUM → 더 높은 등급
        (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.MEDIUM, True, True),
        (RiskLevel.MEDIUM, RiskLevel.CRITICAL, RiskLevel.MEDIUM, True, True),
        (RiskLevel.MEDIUM, RiskLevel.MEDIUM, RiskLevel.MEDIUM, True, False),  # 동일
        (RiskLevel.MEDIUM, RiskLevel.LOW, RiskLevel.MEDIUM, True, False),  # 하향
        # HIGH → CRITICAL
        (RiskLevel.HIGH, RiskLevel.CRITICAL, RiskLevel.MEDIUM, True, True),
        (RiskLevel.HIGH, RiskLevel.HIGH, RiskLevel.MEDIUM, True, False),
        (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.MEDIUM, True, False),  # 하향
        # min_level=HIGH 인 경우
        (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, True, False),  # MEDIUM 은 min 미만
        (RiskLevel.LOW, RiskLevel.HIGH, RiskLevel.HIGH, True, True),
        (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.HIGH, True, True),
    ],
)
def test_should_alert_decision_table(previous, new, min_level, first_run, expected) -> None:
    assert (
        should_alert(
            new_level=new,
            previous_level=previous,
            min_level=min_level,
            first_run_send=first_run,
        )
        is expected
    )


# ===== is_duplicate_alert =====


async def test_is_duplicate_returns_true_for_recent_sent_same_level() -> None:
    tables = MagicMock()
    tables.alert_history = MagicMock()
    tables.alert_history.list_recent = AsyncMock(
        return_value=[
            AlertHistory(
                company_id="c-1",
                sent_at=_now(),
                risk_level=RiskLevel.MEDIUM,
                sent_to="ops@example.com",
                status=AlertStatus.SENT,
            )
        ]
    )
    assert (
        await is_duplicate_alert(
            tables, company_id="c-1", risk_level=RiskLevel.MEDIUM, dedup_days=90, now=_now()
        )
        is True
    )


async def test_is_duplicate_returns_false_when_only_failed_history() -> None:
    tables = MagicMock()
    tables.alert_history = MagicMock()
    tables.alert_history.list_recent = AsyncMock(
        return_value=[
            AlertHistory(
                company_id="c-1",
                sent_at=_now(),
                risk_level=RiskLevel.MEDIUM,
                sent_to="ops@example.com",
                status=AlertStatus.FAILED,
            )
        ]
    )
    assert (
        await is_duplicate_alert(
            tables, company_id="c-1", risk_level=RiskLevel.MEDIUM, dedup_days=90, now=_now()
        )
        is False
    )


async def test_is_duplicate_returns_false_for_different_level() -> None:
    tables = MagicMock()
    tables.alert_history = MagicMock()
    tables.alert_history.list_recent = AsyncMock(
        return_value=[
            AlertHistory(
                company_id="c-1",
                sent_at=_now(),
                risk_level=RiskLevel.HIGH,
                sent_to="ops@example.com",
                status=AlertStatus.SENT,
            )
        ]
    )
    assert (
        await is_duplicate_alert(
            tables, company_id="c-1", risk_level=RiskLevel.MEDIUM, dedup_days=90, now=_now()
        )
        is False
    )


async def test_is_duplicate_returns_false_when_dedup_disabled() -> None:
    tables = MagicMock()
    tables.alert_history = MagicMock()
    tables.alert_history.list_recent = AsyncMock(return_value=[])
    assert (
        await is_duplicate_alert(
            tables, company_id="c-1", risk_level=RiskLevel.MEDIUM, dedup_days=0, now=_now()
        )
        is False
    )
    tables.alert_history.list_recent.assert_not_called()


# ===== render_alert =====


def test_render_alert_subject_includes_company_and_korean_level() -> None:
    subject = render_alert_subject(company_name="ACME", snapshot=_snapshot())
    assert "ACME" in subject
    assert "주의" in subject  # MEDIUM = 주의
    assert "55.0" in subject


def test_render_alert_html_escapes_company_name() -> None:
    snap = _snapshot(risk_level=RiskLevel.HIGH)
    html = render_alert_html(
        company_name="<script>X</script>",
        snapshot=snap,
        previous_level=RiskLevel.MEDIUM,
        key_risk_factors=["요인A", "<b>injected</b>"],
        summary="요약 내용",
    )
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;injected&lt;/b&gt;" in html
    assert "요인A" in html
    assert "경고" in html  # HIGH 한국어
    assert "주의" in html  # 이전 등급 MEDIUM 한국어


def test_render_alert_text_includes_drilldown_info() -> None:
    snap = _snapshot()
    text = render_alert_text(
        company_name="ACME",
        snapshot=snap,
        previous_level=None,
        key_risk_factors=["요인1"],
        summary="요약",
    )
    assert "ACME" in text
    assert "(첫 실행)" in text
    assert "job-1" in text
    assert "요인1" in text


# ===== maybe_send_alert =====


def _make_alert_deps(
    *,
    history: list[AlertHistory] | None = None,
    send_side_effect=None,
) -> tuple[MagicMock, MagicMock]:
    tables = MagicMock()
    tables.alert_history = MagicMock()
    tables.alert_history.list_recent = AsyncMock(return_value=history or [])
    tables.alert_history.insert = AsyncMock()
    gmail = MagicMock()
    gmail.send_html = AsyncMock(
        side_effect=send_side_effect,
        return_value="msg-abc" if send_side_effect is None else None,
    )
    return tables, gmail


async def test_maybe_send_alert_sends_when_threshold_crossed_and_no_dup() -> None:
    tables, gmail = _make_alert_deps()
    snap = _snapshot(risk_level=RiskLevel.HIGH)

    msg_id = await maybe_send_alert(
        tables=tables,
        gmail=gmail,
        company_name="ACME",
        recipient_email="ops@example.com",
        snapshot=snap,
        previous_level=RiskLevel.LOW,
        key_risk_factors=["요인1"],
        summary="요약",
        min_level=RiskLevel.MEDIUM,
        dedup_days=90,
        first_run_send=True,
        now=_now(),
    )

    assert msg_id == "msg-abc"
    gmail.send_html.assert_awaited_once()
    history = tables.alert_history.insert.call_args.args[0]
    assert history.status is AlertStatus.SENT
    assert history.gmail_message_id == "msg-abc"
    assert history.risk_level is RiskLevel.HIGH


async def test_maybe_send_alert_skips_when_below_threshold() -> None:
    tables, gmail = _make_alert_deps()
    snap = _snapshot(risk_level=RiskLevel.LOW)

    msg_id = await maybe_send_alert(
        tables=tables,
        gmail=gmail,
        company_name="ACME",
        recipient_email="ops@example.com",
        snapshot=snap,
        previous_level=None,
        key_risk_factors=[],
        summary="",
        min_level=RiskLevel.MEDIUM,
        dedup_days=90,
        first_run_send=True,
        now=_now(),
    )

    assert msg_id is None
    gmail.send_html.assert_not_called()
    tables.alert_history.insert.assert_not_called()


async def test_maybe_send_alert_skips_when_duplicate() -> None:
    tables, gmail = _make_alert_deps(
        history=[
            AlertHistory(
                company_id="c-1",
                sent_at=_now(),
                risk_level=RiskLevel.MEDIUM,
                sent_to="ops@example.com",
                status=AlertStatus.SENT,
            )
        ]
    )
    snap = _snapshot(risk_level=RiskLevel.MEDIUM)

    msg_id = await maybe_send_alert(
        tables=tables,
        gmail=gmail,
        company_name="ACME",
        recipient_email="ops@example.com",
        snapshot=snap,
        previous_level=RiskLevel.LOW,
        key_risk_factors=[],
        summary="",
        min_level=RiskLevel.MEDIUM,
        dedup_days=90,
        first_run_send=True,
        now=_now(),
    )

    assert msg_id is None
    gmail.send_html.assert_not_called()
    tables.alert_history.insert.assert_not_called()


async def test_maybe_send_alert_records_failed_then_raises() -> None:
    tables, gmail = _make_alert_deps(send_side_effect=GmailApiError("rate limited"))
    snap = _snapshot(risk_level=RiskLevel.HIGH)

    with pytest.raises(GmailApiError):
        await maybe_send_alert(
            tables=tables,
            gmail=gmail,
            company_name="ACME",
            recipient_email="ops@example.com",
            snapshot=snap,
            previous_level=RiskLevel.LOW,
            key_risk_factors=["요인1"],
            summary="요약",
            min_level=RiskLevel.MEDIUM,
            dedup_days=90,
            first_run_send=True,
            now=_now(),
        )

    tables.alert_history.insert.assert_awaited_once()
    history = tables.alert_history.insert.call_args.args[0]
    assert history.status is AlertStatus.FAILED
    assert history.gmail_message_id is None
