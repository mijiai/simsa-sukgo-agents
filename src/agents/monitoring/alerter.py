from datetime import UTC, datetime, timedelta
from html import escape

from src.agents.monitoring.gmail_client import GmailClient
from src.common.constants import RiskLevel
from src.common.exceptions import GmailApiError
from src.config.logging import get_logger
from src.storage.schemas import AlertHistory, AlertStatus, MonitoringSnapshot
from src.storage.table_store import TableStore

logger = get_logger(__name__)

_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_RISK_COLOR = {
    RiskLevel.LOW: "#4caf50",
    RiskLevel.MEDIUM: "#ff9800",
    RiskLevel.HIGH: "#f44336",
    RiskLevel.CRITICAL: "#b71c1c",
}

_RISK_KOREAN = {
    RiskLevel.LOW: "정상",
    RiskLevel.MEDIUM: "주의",
    RiskLevel.HIGH: "경고",
    RiskLevel.CRITICAL: "위험",
}


def should_alert(
    *,
    new_level: RiskLevel,
    previous_level: RiskLevel | None,
    min_level: RiskLevel,
    first_run_send: bool,
) -> bool:
    """결정 규칙:

    - 새 등급이 min_level 이상 (rank 비교)
    - AND (이전 등급 None 일 때 first_run_send=True 이거나, 이전 등급이 새 등급보다 낮음)
    - 등급 같음 / 하향 / 새 등급이 min_level 미만 → False
    """
    if _RISK_RANK[new_level] < _RISK_RANK[min_level]:
        return False
    if previous_level is None:
        return first_run_send
    return _RISK_RANK[new_level] > _RISK_RANK[previous_level]


async def is_duplicate_alert(
    tables: TableStore,
    *,
    company_id: str,
    risk_level: RiskLevel,
    dedup_days: int,
    now: datetime | None = None,
) -> bool:
    """3개월(default) 내 동일 (company_id, risk_level) sent 이력 있으면 True."""
    if dedup_days <= 0:
        return False
    now = now or datetime.now(UTC)
    since = now - timedelta(days=dedup_days)
    history = await tables.alert_history.list_recent(company_id, since)
    return any(h.risk_level is risk_level and h.status is AlertStatus.SENT for h in history)


def render_alert_subject(*, company_name: str, snapshot: MonitoringSnapshot) -> str:
    return (
        f"[심사숙고] {company_name} 위험 등급 {_RISK_KOREAN[snapshot.risk_level]} "
        f"진입 — 점수 {snapshot.risk_score:.1f}"
    )


def render_alert_html(
    *,
    company_name: str,
    snapshot: MonitoringSnapshot,
    previous_level: RiskLevel | None,
    key_risk_factors: list[str],
    summary: str,
) -> str:
    color = _RISK_COLOR[snapshot.risk_level]
    level_kr = _RISK_KOREAN[snapshot.risk_level]
    previous_label = _RISK_KOREAN[previous_level] if previous_level else "(첫 실행)"

    factors_html = (
        "<ul>" + "".join(f"<li>{escape(f)}</li>" for f in key_risk_factors[:8]) + "</ul>"
        if key_risk_factors
        else "<p>(별도 명시된 핵심 위험 요인 없음)</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, 'Apple SD Gothic Neo', sans-serif; \
color: #222; max-width: 640px; margin: 0 auto; padding: 24px;">
  <div style="border-left: 6px solid {color}; padding: 12px 16px; \
background: #fafafa; border-radius: 4px;">
    <div style="font-size: 14px; color: #666;">심사숙고 모니터링 알림</div>
    <h2 style="margin: 8px 0 4px; color: {color};">
      {escape(company_name)} 위험 등급 {level_kr}
    </h2>
    <div style="font-size: 14px; color: #666;">
      이전 등급: <b>{previous_label}</b> → 현재 등급: <b style="color: {color};">{level_kr}</b>
      &nbsp;|&nbsp; 위험 점수: <b>{snapshot.risk_score:.1f} / 100</b>
    </div>
  </div>

  <h3 style="margin-top: 24px;">분석 요약</h3>
  <p style="line-height: 1.6;">{escape(summary or "(요약 없음)")}</p>

  <h3>핵심 위험 요인</h3>
  {factors_html}

  <h3>참조 정보</h3>
  <ul style="font-size: 13px; color: #555;">
    <li>실행 일자: {snapshot.run_date.isoformat()}</li>
    <li>분석 Job ID: <code>{escape(snapshot.analysis_job_id)}</code></li>
    <li>스냅샷 Blob: <code>{escape(snapshot.snapshot_blob_path or "")}</code></li>
  </ul>

  <hr style="border: 0; border-top: 1px solid #eee; margin: 24px 0;">
  <p style="font-size: 12px; color: #999;">
    심사숙고 사후관리 모니터링 자동 발송 메일입니다.
    수신을 중단하려면 monitor_deregister Tool 을 호출하세요.
  </p>
</body>
</html>"""


def render_alert_text(
    *,
    company_name: str,
    snapshot: MonitoringSnapshot,
    previous_level: RiskLevel | None,
    key_risk_factors: list[str],
    summary: str,
) -> str:
    level_kr = _RISK_KOREAN[snapshot.risk_level]
    previous_label = _RISK_KOREAN[previous_level] if previous_level else "(첫 실행)"
    factors = "\n".join(f"  - {f}" for f in key_risk_factors[:8]) or "  (없음)"
    return (
        f"[심사숙고 모니터링 알림]\n\n"
        f"기업: {company_name}\n"
        f"등급: {previous_label} → {level_kr}\n"
        f"위험 점수: {snapshot.risk_score:.1f} / 100\n"
        f"실행 일자: {snapshot.run_date.isoformat()}\n\n"
        f"분석 요약:\n{summary or '(요약 없음)'}\n\n"
        f"핵심 위험 요인:\n{factors}\n\n"
        f"분석 Job ID: {snapshot.analysis_job_id}\n"
        f"스냅샷 Blob: {snapshot.snapshot_blob_path or ''}\n"
    )


async def maybe_send_alert(
    *,
    tables: TableStore,
    gmail: GmailClient,
    company_name: str,
    recipient_email: str,
    snapshot: MonitoringSnapshot,
    previous_level: RiskLevel | None,
    key_risk_factors: list[str],
    summary: str,
    min_level: RiskLevel,
    dedup_days: int,
    first_run_send: bool,
    now: datetime | None = None,
) -> str | None:
    """If the snapshot warrants an alert and isn't a duplicate, send it.

    Returns the Gmail message_id when sent, None when skipped.
    AlertHistory row is written for both SENT and FAILED outcomes.
    """
    company_id = snapshot.company_id
    if not should_alert(
        new_level=snapshot.risk_level,
        previous_level=previous_level,
        min_level=min_level,
        first_run_send=first_run_send,
    ):
        logger.info(
            "alert.skip.threshold",
            company_id=company_id,
            risk_level=snapshot.risk_level.value,
            previous_risk_level=previous_level.value if previous_level else None,
        )
        return None

    if await is_duplicate_alert(
        tables,
        company_id=company_id,
        risk_level=snapshot.risk_level,
        dedup_days=dedup_days,
        now=now,
    ):
        logger.info(
            "alert.skip.dedup",
            company_id=company_id,
            risk_level=snapshot.risk_level.value,
            dedup_days=dedup_days,
        )
        return None

    subject = render_alert_subject(company_name=company_name, snapshot=snapshot)
    html_body = render_alert_html(
        company_name=company_name,
        snapshot=snapshot,
        previous_level=previous_level,
        key_risk_factors=key_risk_factors,
        summary=summary,
    )
    text_body = render_alert_text(
        company_name=company_name,
        snapshot=snapshot,
        previous_level=previous_level,
        key_risk_factors=key_risk_factors,
        summary=summary,
    )

    sent_at = now or datetime.now(UTC)
    try:
        message_id = await gmail.send_html(
            recipient=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except GmailApiError as exc:
        await tables.alert_history.insert(
            AlertHistory(
                company_id=company_id,
                sent_at=sent_at,
                risk_level=snapshot.risk_level,
                sent_to=recipient_email,
                status=AlertStatus.FAILED,
                gmail_message_id=None,
            )
        )
        logger.error(
            "alert.send.failed",
            company_id=company_id,
            recipient=recipient_email,
            error=str(exc),
        )
        raise

    await tables.alert_history.insert(
        AlertHistory(
            company_id=company_id,
            sent_at=sent_at,
            risk_level=snapshot.risk_level,
            sent_to=recipient_email,
            status=AlertStatus.SENT,
            gmail_message_id=message_id,
        )
    )
    logger.info(
        "alert.send.done",
        company_id=company_id,
        recipient=recipient_email,
        message_id=message_id,
        risk_level=snapshot.risk_level.value,
    )
    return message_id
