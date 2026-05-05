"""report_generate Tool 의 service — 3단계 파이프라인.

흐름:
    1. raw.json + result.json 로드
    2. template_spec 로드 (yaml → 메모리 캐시)
    3. Planner LLM (1회) → ReportPlan (검증/fallback)
    4. financial.section_insights + plan → SectionContent (LLM 호출 X)
    5. docx + md 동시 렌더 → Blob 양쪽 업로드
    6. Status update + jobs_ref UPDATE (기존 로직 보존)
    7. ReportResponse 에 docx_url + report_url 둘 다 반환
"""

import json
from datetime import UTC, datetime, timedelta

from src.agents.financial.schemas import SectionInsight
from src.agents.report.narrative import NarrativeMap, run_narrative_writer
from src.agents.report.planner import run_planner
from src.agents.report.renderer import render_report_docx, render_report_markdown
from src.agents.report.schemas import ReportRequest, ReportResponse
from src.agents.report.sections import SectionContent, build_section_content
from src.agents.report.template_spec import ReportTemplate, load_template
from src.agents.report.templates import get_cached_templates
from src.common.anthropic_client import AnthropicClient
from src.common.constants import ReportSection, RiskLevel
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)

DEFAULT_TEMPLATE_NAME = "loan_application_v1"
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


def _result_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/analyze/result.json"


def _report_md_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/report/report.md"


def _report_docx_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/report/report.docx"


def _build_sections(
    template: ReportTemplate,
    plan,
    section_insights: list[SectionInsight],
    narratives: NarrativeMap,
) -> dict[ReportSection, SectionContent]:
    sections: dict[ReportSection, SectionContent] = {}
    for spec in template.sections:
        plan_section = plan.sections.get(spec.section_id.value)
        # plan 보강은 planner.merge_with_fallback 가 끝낸 상태이지만 안전망으로 한 번 더
        if plan_section is None:
            from src.agents.report.planner import PlannedSection

            plan_section = PlannedSection()
        narrative = narratives.narratives.get(spec.section_id.value, "")
        sections[spec.section_id] = build_section_content(
            spec, plan_section, section_insights, narrative=narrative
        )
    return sections


async def _maybe_download_base_docx(blob: BlobStore, base_blob_path: str) -> bytes | None:
    """base docx 가 Blob 에 있으면 다운로드. 없으면 None — 새 Document() 로 진행."""
    if not base_blob_path:
        return None
    try:
        return await blob.download(base_blob_path)
    except Exception as exc:
        logger.warning(
            "report.base_docx.download_failed",
            blob_path=base_blob_path,
            error=str(exc),
        )
        return None


async def report_generate_service(
    request: ReportRequest,
    blob: BlobStore,
    tables: TableStore,
    anthropic: AnthropicClient,
    *,
    sas_expiry_hours: int,
    template_name: str = DEFAULT_TEMPLATE_NAME,
    base_docx_blob_path: str = "",
    appendix_row_threshold: int = 0,
) -> ReportResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.REPORTING,
        current_agent=AgentName.REPORT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.REPORT)
    logger.info(
        "report.start",
        job_id=request.job_id,
        model=anthropic.model,
        template=template_name,
    )

    try:
        # 1. 데이터 로드
        raw_bytes = await blob.download(_raw_blob_path(request.job_id))
        raw = json.loads(raw_bytes.decode("utf-8"))
        result_bytes = await blob.download(_result_blob_path(request.job_id))
        analysis = json.loads(result_bytes.decode("utf-8"))

        risk_level = RiskLevel(analysis["risk_level"])
        risk_score = float(analysis["risk_score"])
        company_id = analysis.get("company_id") or raw.get("company_id")

        # 2. 템플릿 로드
        template = load_template(template_name)

        # 3. Planner LLM (1회 호출)
        plan = await run_planner(template, raw, analysis, anthropic)
        logger.info(
            "report.planner.done",
            job_id=request.job_id,
            section_count=len(plan.sections),
        )

        # 3.5. Narrative Writer LLM (1회 호출) — 섹션별 서술형 paragraph
        narratives = await run_narrative_writer(
            template, raw, analysis, plan, anthropic, samples=get_cached_templates()
        )
        non_empty_narratives = sum(1 for v in narratives.narratives.values() if v.strip())
        logger.info(
            "report.narrative.done",
            job_id=request.job_id,
            section_count=len(narratives.narratives),
            non_empty_count=non_empty_narratives,
        )

        # 4. 섹션 컨텐츠 조립 (LLM 호출 X)
        section_insights = [
            SectionInsight.model_validate(si) for si in (analysis.get("section_insights") or [])
        ]
        sections = _build_sections(template, plan, section_insights, narratives)

        # 5. docx + md 렌더 (LLM 호출 X). base docx 와 appendix 정책은 PR4.
        base_docx_bytes = await _maybe_download_base_docx(blob, base_docx_blob_path)
        docx_bytes = await render_report_docx(
            template,
            sections,
            blob,
            base_docx_bytes=base_docx_bytes,
            appendix_row_threshold=appendix_row_threshold,
        )
        md_text = render_report_markdown(
            template,
            sections,
            appendix_row_threshold=appendix_row_threshold,
        )

        # 6. Blob 양쪽 업로드
        docx_path = _report_docx_blob_path(request.job_id)
        md_path = _report_md_blob_path(request.job_id)
        await blob.upload(docx_path, docx_bytes, content_type=_DOCX_CONTENT_TYPE)
        await blob.upload(
            md_path,
            md_text.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        report_url = blob.generate_sas_url(md_path, timedelta(hours=sas_expiry_hours))
        docx_url = blob.generate_sas_url(docx_path, timedelta(hours=sas_expiry_hours))

        # 7. Status updates (기존 로직 보존)
        finished_at = datetime.now(UTC)
        await tables.agent_status.update_done(
            request.job_id,
            AgentName.REPORT,
            output_blob_path=docx_path,
        )
        await tables.jobs.update_status(
            request.job_id,
            JobStatus.DONE,
            report_blob_path=docx_path,
            risk_level=risk_level,
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
            docx_bytes=len(docx_bytes),
            md_chars=len(md_text),
        )

        return ReportResponse(
            job_id=request.job_id,
            risk_level=risk_level,
            risk_score=risk_score,
            report_url=report_url,
            report_blob_path=md_path,
            docx_url=docx_url,
            docx_blob_path=docx_path,
        )

    except Exception as exc:
        logger.error("report.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.REPORT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
