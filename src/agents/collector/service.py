"""collector service — 자료 수집 비즈니스 로직."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from src.agents.collector.clients import NaverNewsClient
from src.agents.collector.dart_client import DartClient
from src.agents.collector.extractors import (
    caption_image_with_vision,
    extract_uploaded_file,
)
from src.agents.collector.internal_db import get_company_data
from src.agents.collector.schemas import (
    CollectRequest,
    CollectResponse,
    DartFinancialYear,
    ExtractedDoc,
    ExtractedImage,
    ExtractedTable,
)
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, Company, JobStatus
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
    """각 업로드 파일을 병렬로 처리하고 종류별로 분류."""
    tables: list[ExtractedTable] = []
    images: list[ExtractedImage] = []
    docs: list[ExtractedDoc] = []

    if not uploaded_files:
        return tables, images, docs

    prefix = _input_prefix(job_id)

    async def _process_one(filename: str) -> dict | None:
        blob_path = f"{prefix}{filename}"
        t0 = time.monotonic()
        try:
            extracted = await extract_uploaded_file(blob, blob_path, filename)
        except Exception as exc:
            logger.warning("collect.extract.failed", file=filename, error=str(exc))
            return None
        if extracted is None:
            logger.info("collect.extract.unsupported_skipped", file=filename)
            return None

        if extracted["kind"] == "image" and vision_anthropic is not None:
            extracted["content"]["caption"] = await caption_image_with_vision(
                blob,
                extracted["content"]["blob_path"],
                filename,
                vision_anthropic,
                model=vision_model,
            )

        logger.info(
            "collect.extract.file_done",
            file=filename,
            kind=extracted["kind"],
            elapsed_s=round(time.monotonic() - t0, 3),
        )
        return extracted

    results = await asyncio.gather(*[_process_one(f) for f in uploaded_files])

    for filename, extracted in zip(uploaded_files, results):
        if extracted is None:
            continue
        kind = extracted["kind"]
        content = extracted["content"]
        if kind == "xlsx":
            for sheet in content:
                tables.append(ExtractedTable(source_file=filename, **sheet))
            doc_text = extracted.get("doc_text")
            if doc_text:
                docs.append(ExtractedDoc(source_file=filename, text=doc_text, page_count=0))
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
            images.append(
                ExtractedImage(
                    source_file=filename,
                    blob_path=content["blob_path"],
                    suspected_role=content["suspected_role"],
                    width=content["width"],
                    height=content["height"],
                    caption=content.get("caption"),
                )
            )

    return tables, images, docs


async def _fetch_dart_financials(
    dart: DartClient,
    company_name: str,
    company: Company | None,
) -> tuple[str | None, list[DartFinancialYear]]:
    """DART 에서 corp_code 를 확인하고 다년도 주요계정을 조회한다.

    1. Companies 테이블에 dart_corp_code 가 이미 있으면 그대로 사용 (API 1회 절약).
    2. 없으면 company.json 으로 검색 후 반환 (Companies UPSERT 는 호출자가 담당).
    3. 조회 실패해도 빈 리스트 반환 — collector 전체를 실패시키지 않음.

    Returns:
        (corp_code_or_none, [DartFinancialYear, ...])
    """
    t0 = time.monotonic()

    # corp_code 결정
    cached_code = company.dart_corp_code if company else None
    if cached_code:
        corp_code: str | None = cached_code
        logger.info("dart.corp_code.cache_hit", company=company_name, corp_code=corp_code)
    else:
        t_search = time.monotonic()
        corp_code = await dart.search_corp_code(company_name)
        logger.info(
            "dart.corp_code.search_done",
            company=company_name,
            found=corp_code is not None,
            elapsed_s=round(time.monotonic() - t_search, 3),
        )
        if not corp_code:
            return None, []

    # 다년도 주요계정 조회
    try:
        t_accounts = time.monotonic()
        results = await dart.get_multi_year_accounts(corp_code)
        logger.info(
            "dart.multi_year.fetched",
            corp_code=corp_code,
            count=len(results),
            elapsed_s=round(time.monotonic() - t_accounts, 3),
        )
    except Exception as exc:
        logger.warning("dart.multi_year.failed", corp_code=corp_code, error=str(exc))
        return corp_code, []

    dart_years: list[DartFinancialYear] = []
    for result in results:
        dart_year = DartFinancialYear.from_dart_result(result)
        dart_years.append(dart_year)

    years_with_data = [dy.year for dy in dart_years if dy.has_data]
    logger.info(
        "dart.financials.done",
        corp_code=corp_code,
        years_fetched=[dy.year for dy in dart_years],
        years_with_data=years_with_data,
        total_elapsed_s=round(time.monotonic() - t0, 3),
    )
    return corp_code, dart_years


async def collect_company_data_service(
    request: CollectRequest,
    blob: BlobStore,
    tables: TableStore,
    naver: NaverNewsClient,
    *,
    dart: DartClient | None = None,
    vision_anthropic: AnthropicClient | None = None,
    vision_model: str | None = None,
) -> CollectResponse:
    t_total = time.monotonic()
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.COLLECTING,
        current_agent=AgentName.COLLECT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.COLLECT)
    logger.info("collect.start", job_id=request.job_id, company=request.company_name)

    try:
        # ─── Phase 1: 파일 목록 조회 + 회사 조회 + 뉴스 검색 (병렬) ──────
        t0 = time.monotonic()
        uploaded_files, company, news = await asyncio.gather(
            _list_uploaded_files(blob, request.job_id),
            tables.companies.find_by_name(request.company_name),
            naver.search(request.company_name, max_results=30),
        )
        logger.info(
            "collect.phase1.done",
            job_id=request.job_id,
            elapsed_s=round(time.monotonic() - t0, 3),
            uploaded_count=len(uploaded_files),
            news_count=len(news),
        )

        company_id = company.company_id if company else None
        if company is None:
            logger.warning(
                "collect.company.not_found",
                job_id=request.job_id,
                company_name=request.company_name,
            )
        internal_credit_data = get_company_data(company_id) if company_id else None

        # ─── Phase 2: 파일 추출 + DART 재무 조회 (병렬) ──────────────────
        t0 = time.monotonic()
        dart_corp_code: str | None = None
        dart_financials: list[DartFinancialYear] = []

        if dart is not None:
            (extracted_tables, extracted_images, extracted_docs), (dart_corp_code, dart_financials) = (
                await asyncio.gather(
                    _extract_all_uploads(
                        blob,
                        request.job_id,
                        uploaded_files,
                        vision_anthropic=vision_anthropic,
                        vision_model=vision_model,
                    ),
                    _fetch_dart_financials(dart, request.company_name, company),
                )
            )
        else:
            extracted_tables, extracted_images, extracted_docs = await _extract_all_uploads(
                blob,
                request.job_id,
                uploaded_files,
                vision_anthropic=vision_anthropic,
                vision_model=vision_model,
            )
            logger.info("collect.dart.skipped_no_client", job_id=request.job_id)

        dart_financial_years = [dy.year for dy in dart_financials if dy.has_data]
        logger.info(
            "collect.phase2.done",
            job_id=request.job_id,
            elapsed_s=round(time.monotonic() - t0, 3),
            extracted_tables=len(extracted_tables),
            extracted_images=len(extracted_images),
            extracted_docs=len(extracted_docs),
            dart_corp_code=dart_corp_code,
            dart_financial_years=dart_financial_years,
        )

        financial_years = _infer_financial_years(extracted_tables)

        # ─── Phase 3: DART 기업개황 (corp_code 확인 후 순차) ─────────────
        dart_company_info: dict[str, Any] = {}
        if dart is not None and dart_corp_code:
            t0 = time.monotonic()
            try:
                dart_company_info = await dart.get_company_info(dart_corp_code)
                logger.info(
                    "collect.dart_company.done",
                    job_id=request.job_id,
                    corp_code=dart_corp_code,
                    elapsed_s=round(time.monotonic() - t0, 3),
                )
            except Exception as exc:
                logger.warning(
                    "collect.dart_company.failed",
                    corp_code=dart_corp_code,
                    error=str(exc),
                )

            # Companies 테이블에 dart_corp_code 를 캐시 (다음 분석 시 검색 skip)
            if company is not None and company.dart_corp_code != dart_corp_code:
                from src.storage.schemas import Company as CompanySchema

                updated = CompanySchema(
                    company_id=company.company_id,
                    company_name=company.company_name,
                    business_no=company.business_no,
                    corp_no=company.corp_no,
                    dart_corp_code=dart_corp_code,
                    industry_code=company.industry_code,
                    industry_name=company.industry_name,
                    created_at=company.created_at,
                    updated_at=datetime.now(UTC),
                )
                await tables.companies.upsert(updated)
                logger.info(
                    "collect.dart_corp_code.cached",
                    company_id=company.company_id,
                    dart_corp_code=dart_corp_code,
                )

        # ─── raw.json 조립 ─────────────────────────────────────────────────
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
            # DART 데이터 — raw_items 포함 (전체 저장, prompt 에는 accounts 만 inject)
            "dart_corp_code": dart_corp_code,
            "dart_financials": [dy.model_dump(mode="json") for dy in dart_financials],
            "dart_company_info": dart_company_info,
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
            total_elapsed_s=round(time.monotonic() - t_total, 3),
            news_count=len(news),
            files_count=len(uploaded_files),
            extracted_tables=len(extracted_tables),
            extracted_images=len(extracted_images),
            extracted_docs=len(extracted_docs),
            financial_years=financial_years,
            dart_corp_code=dart_corp_code,
            dart_financial_years=dart_financial_years,
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
            dart_corp_code=dart_corp_code,
            dart_financial_years=dart_financial_years,
            output_blob_path=raw_path,
        )

    except Exception as exc:
        logger.error("collect.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.COLLECT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise
