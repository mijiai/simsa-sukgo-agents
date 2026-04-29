import json
from datetime import UTC, datetime

from src.agents.collector.attachments import extract_attachments
from src.agents.collector.clients import NaverNewsClient
from src.agents.collector.internal_db import get_company_data
from src.agents.collector.schemas import CollectRequest, CollectResponse
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


async def collect_company_data_service(
    request: CollectRequest,
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
) -> CollectResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.COLLECTING,
        current_agent=AgentName.COLLECT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.COLLECT)
    logger.info("collect.start", job_id=request.job_id, company=request.company_name)

    try:
        attachments = await extract_attachments(blob, request.job_id)
        uploaded_files = [a.filename for a in attachments]
        attached_documents = [
            {"filename": a.filename, "text": a.text} for a in attachments if a.text
        ]

        company = await tables.companies.find_by_name(request.company_name)
        company_id = company.company_id if company else None
        if company is None:
            logger.warning(
                "collect.company.not_found",
                job_id=request.job_id,
                company_name=request.company_name,
            )

        news = await naver.search(request.company_name, max_results=30)

        internal_credit_data = get_company_data(company_id) if company_id else None
        if company_id and internal_credit_data is None:
            logger.info(
                "collect.internal_db.miss",
                job_id=request.job_id,
                company_id=company_id,
            )
        elif internal_credit_data:
            logger.info(
                "collect.internal_db.hit",
                job_id=request.job_id,
                company_id=company_id,
                chars=len(internal_credit_data),
            )

        raw_payload = {
            "company_name": request.company_name,
            "company_id": company_id,
            "collected_at": datetime.now(UTC).isoformat(),
            "news": [article.model_dump(mode="json") for article in news],
            "lawsuits": [],
            "uploaded_files": uploaded_files,
            "attached_documents": attached_documents,
            "financial_years": [],
            "internal_credit_data": internal_credit_data,
        }

        raw_path = _raw_blob_path(request.job_id)
        await blob.upload(
            raw_path,
            json.dumps(raw_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )

        await tables.agent_status.update_done(
            request.job_id,
            AgentName.COLLECT,
            output_blob_path=raw_path,
        )
        logger.info(
            "collect.done",
            job_id=request.job_id,
            news_count=len(news),
            files_count=len(uploaded_files),
            attached_documents_count=len(attached_documents),
        )

        return CollectResponse(
            job_id=request.job_id,
            company_name=request.company_name,
            company_id=company_id,
            news_count=len(news),
            uploaded_files=uploaded_files,
            attached_documents_count=len(attached_documents),
            has_internal_credit_data=internal_credit_data is not None,
            output_blob_path=raw_path,
        )

    except Exception as exc:
        logger.error("collect.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.COLLECT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
