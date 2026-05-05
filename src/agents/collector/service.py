import json
import re
from datetime import UTC, datetime
from typing import Any

from src.agents.collector.clients import NaverNewsClient
from src.agents.collector.extractors import (
    caption_image_with_vision,
    extract_uploaded_file,
)
from src.agents.collector.internal_db import get_company_data
from src.agents.collector.schemas import (
    CollectRequest,
    CollectResponse,
    ExtractedDoc,
    ExtractedImage,
    ExtractedTable,
)
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)

_YEAR_PATTERN = re.compile(r"(?<!\d)(20[1-3]\d)(?!\d)")  # 2010~2039 4자리


def _input_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/input/"


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


async def _list_uploaded_files(blob: BlobStore, job_id: str) -> list[str]:
    prefix = _input_prefix(job_id)
    blob_paths = await blob.list_prefix(prefix)
    prompt_path = f"{prefix}prompt.txt"
    return [
        path[len(prefix) :]
        for path in blob_paths
        if path != prompt_path and path.startswith(prefix)
    ]


def _infer_financial_years(tables: list[ExtractedTable]) -> list[int]:
    """columns 에서 4자리 연도 패턴 (2010~2039) 추출 → 정렬 + 중복 제거."""
    years: set[int] = set()
    for tbl in tables:
        for col in tbl.columns:
            for match in _YEAR_PATTERN.finditer(str(col)):
                years.add(int(match.group(1)))
    return sorted(years)


async def _extract_all_uploads(
    blob: BlobStore,
    job_id: str,
    uploaded_files: list[str],
    *,
    vision_anthropic: AnthropicClient | None = None,
    vision_model: str | None = None,
) -> tuple[list[ExtractedTable], list[ExtractedImage], list[ExtractedDoc]]:
    """각 업로드 파일을 extract_uploaded_file 로 처리하고 종류별로 분류.

    개별 파일 추출 실패는 warning 로그만 남기고 다음 파일로 진행. 전체 collect 는 실패 X.

    vision_anthropic 가 주어지면 이미지마다 Vision API 1회 호출해 caption 생성 (PR4).
    """
    tables: list[ExtractedTable] = []
    images: list[ExtractedImage] = []
    docs: list[ExtractedDoc] = []
    prefix = _input_prefix(job_id)

    for filename in uploaded_files:
        blob_path = f"{prefix}{filename}"
        try:
            extracted = await extract_uploaded_file(blob, blob_path, filename)
        except Exception as exc:
            logger.warning(
                "collect.extract.failed",
                file=filename,
                error=str(exc),
            )
            continue
        if extracted is None:
            logger.info("collect.extract.unsupported_skipped", file=filename)
            continue

        kind = extracted["kind"]
        content = extracted["content"]
        if kind == "xlsx":
            for sheet in content:
                tables.append(ExtractedTable(source_file=filename, **sheet))
            # sparse 시트 (PDF 인쇄용 .xls 등) 는 dispatcher 가 doc_text 로 fallback
            doc_text = extracted.get("doc_text")
            if doc_text:
                docs.append(
                    ExtractedDoc(
                        source_file=filename,
                        text=doc_text,
                        page_count=0,  # sheet 수는 의미 없음 — 0 으로 통일
                        truncated=False,
                    )
                )
        elif kind == "pdf":
            docs.append(
                ExtractedDoc(
                    source_file=filename,
                    text=content["text"],
                    page_count=content["page_count"],
                    truncated=content["truncated"],
                )
            )
            for tbl in content["tables"]:
                tables.append(ExtractedTable(source_file=filename, **tbl))
        elif kind == "image":
            caption: str | None = None
            if vision_anthropic is not None:
                caption = await caption_image_with_vision(
                    blob,
                    content["blob_path"],
                    filename,
                    vision_anthropic,
                    model=vision_model,
                )
                if caption:
                    logger.info(
                        "collect.image.captioned",
                        file=filename,
                        chars=len(caption),
                    )
            images.append(
                ExtractedImage(
                    source_file=filename,
                    blob_path=content["blob_path"],
                    suspected_role=content["suspected_role"],
                    width=content["width"],
                    height=content["height"],
                    caption=caption,
                )
            )

    return tables, images, docs


async def collect_company_data_service(
    request: CollectRequest,
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
    *,
    vision_anthropic: AnthropicClient | None = None,
    vision_model: str | None = None,
) -> CollectResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.COLLECTING,
        current_agent=AgentName.COLLECT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.COLLECT)
    logger.info("collect.start", job_id=request.job_id, company=request.company_name)

    try:
        uploaded_files = await _list_uploaded_files(blob, request.job_id)
        extracted_tables, extracted_images, extracted_docs = await _extract_all_uploads(
            blob,
            request.job_id,
            uploaded_files,
            vision_anthropic=vision_anthropic,
            vision_model=vision_model,
        )

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

        financial_years = _infer_financial_years(extracted_tables)

        raw_payload: dict[str, Any] = {
            "company_name": request.company_name,
            "company_id": company_id,
            "collected_at": datetime.now(UTC).isoformat(),
            "news": [article.model_dump(mode="json") for article in news],
            "lawsuits": [],
            "uploaded_files": uploaded_files,
            "financial_years": financial_years,
            "internal_credit_data": internal_credit_data,
            "extracted_tables": [t.model_dump(mode="json") for t in extracted_tables],
            "extracted_images": [i.model_dump(mode="json") for i in extracted_images],
            "extracted_docs": [d.model_dump(mode="json") for d in extracted_docs],
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
            extracted_tables=len(extracted_tables),
            extracted_images=len(extracted_images),
            extracted_docs=len(extracted_docs),
            financial_years=financial_years,
        )

        return CollectResponse(
            job_id=request.job_id,
            company_name=request.company_name,
            company_id=company_id,
            news_count=len(news),
            uploaded_files=uploaded_files,
            financial_years=financial_years,
            has_internal_credit_data=internal_credit_data is not None,
            extracted_table_count=len(extracted_tables),
            extracted_image_count=len(extracted_images),
            extracted_doc_count=len(extracted_docs),
            output_blob_path=raw_path,
        )

    except Exception as exc:
        logger.error("collect.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.COLLECT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
