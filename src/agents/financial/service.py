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
from src.common.constants import ReportSection
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


def _result_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/analyze/result.json"


# ReportSection 유효 값 집합 — 모듈 로드 시 1회 계산
_VALID_SECTION_IDS: frozenset[str] = frozenset(s.value for s in ReportSection)


_MAX_BULLETS_PER_SECTION = 5  # ClaudeJudgment.section_insights[*].bullets max_length 와 동기화


def _filter_section_insights(judgment_dict: dict, job_id: str) -> dict:
    """LLM 이 hallucinate 한 section_id 제거 + bullets 길이 제한.

    1) ReportSection enum 에 없는 section_id 가 포함되면 Pydantic 이
       ValidationError 를 발생시켜 분석 전체가 실패한다 → 미리 제거.
    2) bullets 가 스키마 max_length 를 넘기면 마찬가지로 ValidationError →
       앞 N 개만 남기고 자른다 (의미 우선순위 보존).
    """
    raw_insights = judgment_dict.get("section_insights")
    if not isinstance(raw_insights, list):
        return judgment_dict

    filtered: list[dict] = []
    bullets_trimmed = 0
    invalid_ids: list = []
    for si in raw_insights:
        if not isinstance(si, dict):
            invalid_ids.append(si)
            continue
        if si.get("section_id") not in _VALID_SECTION_IDS:
            invalid_ids.append(si.get("section_id"))
            continue
        bullets = si.get("bullets") or []
        if isinstance(bullets, list) and len(bullets) > _MAX_BULLETS_PER_SECTION:
            bullets_trimmed += len(bullets) - _MAX_BULLETS_PER_SECTION
            si = {**si, "bullets": bullets[:_MAX_BULLETS_PER_SECTION]}
        filtered.append(si)

    dropped = len(raw_insights) - len(filtered)
    if dropped:
        logger.warning(
            "analyze.section_insights.invalid_ids_dropped",
            job_id=job_id,
            dropped=dropped,
            invalid_ids=invalid_ids,
        )
    if bullets_trimmed:
        logger.warning(
            "analyze.section_insights.bullets_trimmed",
            job_id=job_id,
            trimmed_count=bullets_trimmed,
            max_per_section=_MAX_BULLETS_PER_SECTION,
        )

    if dropped or bullets_trimmed:
        judgment_dict = {**judgment_dict, "section_insights": filtered}

    return judgment_dict

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

        # LLM hallucination 방어: ReportSection enum 에 없는 section_id 사전 제거
        judgment_dict = _filter_section_insights(judgment_dict, request.job_id)

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
            section_insights=judgment.section_insights,
            input_summary=AnalysisInputSummary(
                news_count=len(raw.get("news") or []),
                lawsuit_count=len(raw.get("lawsuits") or []),
                uploaded_file_count=len(raw.get("uploaded_files") or []),
                financial_years=raw.get("financial_years") or [],
                extracted_table_count=len(raw.get("extracted_tables") or []),
                extracted_image_count=len(raw.get("extracted_images") or []),
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
            section_insights_count=len(judgment.section_insights),
        )

        return AnalyzeResponse(
            job_id=request.job_id,
            risk_level=judgment.risk_level,
            risk_score=judgment.risk_score,
            key_risk_factors=judgment.key_risk_factors,
            data_gaps=judgment.data_gaps,
            section_insights_count=len(judgment.section_insights),
            output_blob_path=result_path,
        )

    except Exception as exc:
        logger.error("analyze.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.ANALYZE, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise