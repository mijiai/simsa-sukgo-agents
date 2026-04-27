import json
from datetime import UTC, datetime, timedelta

from src.agents.report.prompts import SYSTEM_PROMPT, build_user_prompt
from src.agents.report.schemas import ReportRequest, ReportResponse
from src.agents.report.templates import get_cached_templates
from src.common.anthropic_client import AnthropicClient
from src.common.constants import RiskLevel
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


def _result_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/analyze/result.json"


def _report_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/report/report.md"


async def report_generate_service(
    request: ReportRequest,
    blob: BlobStore,
    tables: TableStore,
    anthropic: AnthropicClient,
    *,
    sas_expiry_hours: int,
) -> ReportResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.REPORTING,
        current_agent=AgentName.REPORT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.REPORT)
    logger.info("report.start", job_id=request.job_id, model=anthropic.model)

    try:
        raw_bytes = await blob.download(_raw_blob_path(request.job_id))
        raw = json.loads(raw_bytes.decode("utf-8"))

        result_bytes = await blob.download(_result_blob_path(request.job_id))
        analysis = json.loads(result_bytes.decode("utf-8"))

        risk_level = RiskLevel(analysis["risk_level"])
        risk_score = float(analysis["risk_score"])
        company_id = analysis.get("company_id") or raw.get("company_id")

        templates = get_cached_templates()
        if not templates:
            logger.warning("report.templates.empty", job_id=request.job_id)

        user_prompt = build_user_prompt(raw=raw, analysis=analysis, templates=templates)
        markdown = await anthropic.complete_text(system=SYSTEM_PROMPT, user=user_prompt)

        report_path = _report_blob_path(request.job_id)
        await blob.upload(
            report_path,
            markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        report_url = blob.generate_sas_url(report_path, timedelta(hours=sas_expiry_hours))

        finished_at = datetime.now(UTC)
        await tables.agent_status.update_done(
            request.job_id,
            AgentName.REPORT,
            output_blob_path=report_path,
        )
        await tables.jobs.update_status(
            request.job_id,
            JobStatus.DONE,
            report_blob_path=report_path,
            finished_at=finished_at,
        )
        if company_id:
            await tables.jobs_ref.update_after_done(
                company_id=company_id,
                job_id=request.job_id,
                risk_level=risk_level,
                finished_at=finished_at,
            )
        else:
            logger.warning("report.jobs_ref.skipped_no_company_id", job_id=request.job_id)

        logger.info(
            "report.done",
            job_id=request.job_id,
            risk_level=risk_level.value,
            chars=len(markdown),
        )

        return ReportResponse(
            job_id=request.job_id,
            risk_level=risk_level,
            risk_score=risk_score,
            report_url=report_url,
            report_blob_path=report_path,
        )

    except Exception as exc:
        logger.error("report.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.REPORT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
