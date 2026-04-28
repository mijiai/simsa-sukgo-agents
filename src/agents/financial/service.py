import json
from datetime import UTC, datetime

from src.agents.financial.prompts import SYSTEM_PROMPT, build_user_prompt
from src.agents.financial.schemas import (
    AnalysisInputSummary,
    AnalysisResult,
    AnalyzeRequest,
    AnalyzeResponse,
    ClaudeJudgment,
)
from src.agents.financial.templates import get_cached_samples
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


def _result_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/analyze/result.json"


async def analyze_financials_service(
    request: AnalyzeRequest,
    blob: BlobStore,
    tables: TableStore,
    anthropic: AnthropicClient,
) -> AnalyzeResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.ANALYZING,
        current_agent=AgentName.ANALYZE,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.ANALYZE)
    logger.info("analyze.start", job_id=request.job_id, model=anthropic.model)

    try:
        raw_bytes = await blob.download(_raw_blob_path(request.job_id))
        raw = json.loads(raw_bytes.decode("utf-8"))

        company_name = raw.get("company_name", "")
        company_id = raw.get("company_id")

        samples = get_cached_samples()
        if not samples:
            logger.warning("analyze.samples.empty", job_id=request.job_id)

        user_prompt = build_user_prompt(company_name=company_name, raw=raw, samples=samples)
        judgment_dict = await anthropic.complete_json(system=SYSTEM_PROMPT, user=user_prompt)
        judgment = ClaudeJudgment.model_validate(judgment_dict)

        result = AnalysisResult(
            job_id=request.job_id,
            company_name=company_name,
            company_id=company_id,
            analyzed_at=datetime.now(UTC),
            model=anthropic.model,
            risk_level=judgment.risk_level,
            risk_score=judgment.risk_score,
            summary=judgment.summary,
            key_risk_factors=judgment.key_risk_factors,
            positive_signals=judgment.positive_signals,
            data_gaps=judgment.data_gaps,
            input_summary=AnalysisInputSummary(
                news_count=len(raw.get("news") or []),
                lawsuit_count=len(raw.get("lawsuits") or []),
                uploaded_file_count=len(raw.get("uploaded_files") or []),
                financial_years=raw.get("financial_years") or [],
            ),
        )

        result_path = _result_blob_path(request.job_id)
        await blob.upload(
            result_path,
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
            content_type="application/json",
        )

        await tables.agent_status.update_done(
            request.job_id,
            AgentName.ANALYZE,
            output_blob_path=result_path,
        )
        logger.info(
            "analyze.done",
            job_id=request.job_id,
            risk_level=judgment.risk_level.value,
            risk_score=judgment.risk_score,
        )

        return AnalyzeResponse(
            job_id=request.job_id,
            risk_level=judgment.risk_level,
            risk_score=judgment.risk_score,
            key_risk_factors=judgment.key_risk_factors,
            data_gaps=judgment.data_gaps,
            output_blob_path=result_path,
        )

    except Exception as exc:
        logger.error("analyze.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.ANALYZE, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
