from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.agents.collector.factory import (
    close_collector_clients,
    get_dart_client,
    get_naver_news_client,
)
from src.agents.collector.internal_db import load_internal_db
from src.agents.collector.tools import register_collector_tools
from src.agents.financial.factory import close_anthropic_client, get_anthropic_client
from src.agents.financial.templates import load_financial_samples
from src.agents.financial.tools import register_financial_tools
from src.agents.monitoring.factory import get_gmail_client, get_scheduler, set_scheduler
from src.agents.monitoring.scheduler import (
    needs_catchup,
    schedule_catchup,
    setup_scheduler,
    shutdown_scheduler,
)
from src.agents.monitoring.schemas import MonitorGetLatestSnapshotRequest
from src.agents.monitoring.service import (
    monitor_get_latest_snapshot_service,
    monitor_list_service,
)
from src.agents.monitoring.tools import register_monitoring_tools
from src.agents.report.factory import (
    close_report_anthropic_client,
    get_report_anthropic_client,
)
from src.agents.report.templates import load_report_samples
from src.agents.report.tools import register_report_tools
from src.common.exceptions import EntityNotFoundError
from src.config.logging import get_logger
from src.config.settings import get_settings
from src.mcp.job_tools import (
    GetAnalysisJobDetailRequest,
    get_analysis_job_detail_service,
    register_job_tools,
)
from src.storage.factory import close_storage, get_blob_store, get_table_store

logger = get_logger(__name__)


SERVER_INSTRUCTIONS = """\
심사숙고 MCP 서버 — 기업 심사 리포트 Multi-Agent.

오케스트레이션 흐름:
  0. (대용량 파일) create_upload_url(filename) → artifact 가 직접 Blob PUT → blob_path 획득
  1. create_analysis_job(...)            → job_id 발급
  2. collect_company_data(job_id, ...)   → 자료 수집
  3. analyze_financials(job_id)          → 재무 분석
  4. report_generate(job_id)             → 보고서 생성
  5. (옵션) monitor_register(origin_job_id=job_id)

각 Tool은 job_id 하나만 다음 Tool에 전달하며, 대용량 데이터는 직접 주고받지 않는다.
파일 첨부는 base64 inline (create_analysis_job.files) 또는 SAS 업로드 후 경로 전달
(create_upload_url → file_blob_paths) 두 가지를 지원한다.
"""


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("mcp_server.startup")
    if settings.azure_storage_connection_string:
        get_blob_store()
        get_table_store()
        logger.info("storage.singletons.initialized")
        try:
            internal = await load_internal_db(get_blob_store())
            if not internal:
                logger.warning("internal_db.empty_at_startup")
        except Exception as exc:
            logger.warning("internal_db.load_failed", error=str(exc))
    else:
        logger.warning("storage.skipped_no_connection_string")
    if settings.naver_client_id and settings.naver_client_secret:
        get_naver_news_client()
        logger.info("naver.client.initialized")
    else:
        logger.warning("naver.skipped_no_credentials")
    if settings.dart_api_key:
        dart = get_dart_client()
        if dart is not None:
            try:
                await dart.warmup()
                logger.info("dart.corp_map.warmed_up")
            except Exception as exc:
                logger.warning("dart.corp_map.warmup_failed", error=str(exc))
    if settings.anthropic_api_key:
        get_anthropic_client()
        get_report_anthropic_client()
        logger.info(
            "anthropic.client.initialized",
            analyzer_model=settings.anthropic_model,
            report_model=settings.report_model,
        )
        if settings.azure_storage_connection_string:
            try:
                samples = await load_report_samples(
                    get_blob_store(), settings.report_samples_blob_prefix
                )
                if not samples:
                    logger.warning(
                        "report.templates.empty_at_startup",
                        prefix=settings.report_samples_blob_prefix,
                    )
            except Exception as exc:
                logger.warning("report.templates.load_failed", error=str(exc))
            try:
                fin_samples = await load_financial_samples(
                    get_blob_store(), settings.financial_samples_blob_prefix
                )
                if not fin_samples:
                    logger.warning(
                        "financial.samples.empty_at_startup",
                        prefix=settings.financial_samples_blob_prefix,
                    )
            except Exception as exc:
                logger.warning("financial.samples.load_failed", error=str(exc))
    else:
        logger.warning("anthropic.skipped_no_api_key")
    # TODO(step-1): AgentStatus 테이블에서 status=running 잔존 Job 감지 → 재개
    # 모니터링 스케줄러 시작 (storage + naver + anthropic 모두 준비됐을 때만)
    if (
        settings.monitoring_scheduler_enabled
        and settings.azure_storage_connection_string
        and settings.naver_client_id
        and settings.naver_client_secret
        and settings.anthropic_api_key
    ):
        scheduler = setup_scheduler(
            blob=get_blob_store(),
            tables=get_table_store(),
            naver=get_naver_news_client(),
            anthropic=get_anthropic_client(),
            gmail=get_gmail_client(),
            settings=settings,
        )
        set_scheduler(scheduler)
        if await needs_catchup(
            get_table_store(),
            threshold_days=settings.monitoring_catchup_threshold_days,
        ):
            await schedule_catchup(
                scheduler,
                blob=get_blob_store(),
                tables=get_table_store(),
                naver=get_naver_news_client(),
                anthropic=get_anthropic_client(),
                gmail=get_gmail_client(),
                settings=settings,
            )
    else:
        logger.warning("monitor.scheduler.skipped", enabled=settings.monitoring_scheduler_enabled)
    try:
        yield
    finally:
        shutdown_scheduler(get_scheduler())
        set_scheduler(None)
        await close_collector_clients()
        await close_anthropic_client()
        await close_report_anthropic_client()
        await close_storage()
        logger.info("mcp_server.shutdown")


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        name="simsasukgo",
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
    )

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/api/jobs/{job_id}", methods=["GET"], include_in_schema=False)
    async def get_job_detail(request: Request) -> JSONResponse:
        job_id = request.path_params["job_id"]
        try:
            req = GetAnalysisJobDetailRequest(job_id=job_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            response = await get_analysis_job_detail_service(
                req,
                get_table_store(),
                get_blob_store(),
                sas_expiry_hours=get_settings().report_sas_expiry_hours,
            )
        except EntityNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/monitors", methods=["GET"], include_in_schema=False)
    async def list_monitors(_request: Request) -> JSONResponse:
        response = await monitor_list_service(get_table_store())
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route(
        "/api/monitors/{company_id}/snapshot",
        methods=["GET"],
        include_in_schema=False,
    )
    async def get_monitor_latest_snapshot(request: Request) -> JSONResponse:
        company_id = request.path_params["company_id"]
        try:
            req = MonitorGetLatestSnapshotRequest(company_id=company_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            response = await monitor_get_latest_snapshot_service(
                req, get_table_store(), get_blob_store()
            )
        except EntityNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(response.model_dump(mode="json"))

    register_job_tools(mcp)
    register_collector_tools(mcp)
    register_financial_tools(mcp)
    register_report_tools(mcp)
    register_monitoring_tools(mcp)

    return mcp
