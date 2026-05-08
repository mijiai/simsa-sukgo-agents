"""LLM 우회 REST 엔드포인트 등록.

frontend / artifact 가 단일 MCP tool 을 결정론적으로 호출하는 시나리오에서
Claude 중간자 없이 직접 호출할 수 있도록 매 tool 을 1:1 REST 엔드포인트로
노출한다. 응답 직렬화는 service 함수 결과를 `model_dump(mode="json")` 그대로
반환하며 인증은 적용하지 않는다 (현재 정책).

라우트 매핑:
    GET    /api/jobs                                  list_analysis_jobs
    GET    /api/jobs/{job_id}                         get_analysis_job_detail
    POST   /api/jobs                                  create_analysis_job
    POST   /api/jobs/{job_id}/collect                 collect_company_data
    POST   /api/jobs/{job_id}/analyze                 analyze_financials
    POST   /api/jobs/{job_id}/report                  report_generate
    POST   /api/upload-urls                           create_upload_url
    GET    /api/monitors                              monitor_list
    GET    /api/monitors/{company_id}/snapshot        monitor_get_latest_snapshot
    POST   /api/monitors                              monitor_register
    DELETE /api/monitors/{company_id}                 monitor_deregister
    POST   /api/monitors/{company_id}/run             monitor_run_now
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.agents.collector.factory import get_dart_client, get_naver_news_client
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service
from src.agents.financial.factory import get_anthropic_client
from src.agents.financial.schemas import AnalyzeRequest
from src.agents.financial.service import analyze_financials_service
from src.agents.monitoring.factory import get_gmail_client
from src.agents.monitoring.run_service import monitor_run_now_service
from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorGetLatestSnapshotRequest,
    MonitorRegisterRequest,
    MonitorRunNowRequest,
)
from src.agents.monitoring.service import (
    monitor_deregister_service,
    monitor_get_latest_snapshot_service,
    monitor_list_service,
    monitor_register_service,
)
from src.agents.report.factory import (
    get_narrative_anthropic_client,
    get_report_anthropic_client,
)
from src.agents.report.schemas import ReportRequest
from src.agents.report.service import report_generate_service
from src.common.exceptions import EntityNotFoundError, SimsaSukgoError
from src.config.logging import get_logger
from src.config.settings import get_settings
from src.mcp.job_tools import (
    CreateAnalysisJobRequest,
    CreateUploadUrlRequest,
    GetAnalysisJobDetailRequest,
    ListAnalysisJobsRequest,
    create_analysis_job_service,
    create_upload_url_service,
    get_analysis_job_detail_service,
    list_analysis_jobs_service,
)
from src.storage.factory import get_blob_store, get_table_store
from src.storage.schemas import JobStatus

logger = get_logger(__name__)


HandlerT = Callable[[Request], Awaitable[JSONResponse]]


def _with_error_handling(fn: HandlerT) -> HandlerT:
    """공통 예외 → HTTP 매핑.

    EntityNotFoundError → 404, ValueError(=Pydantic ValidationError 포함) → 400,
    SimsaSukgoError → 500 (스택 트레이스 미노출), 그 외 → 500 + 로그.
    """

    @wraps(fn)
    async def wrapper(request: Request) -> JSONResponse:
        try:
            return await fn(request)
        except EntityNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except SimsaSukgoError as exc:
            logger.error("rest.error", path=request.url.path, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=500)
        except Exception as exc:
            logger.exception("rest.unexpected_error", path=request.url.path, error=str(exc))
            return JSONResponse({"error": "internal server error"}, status_code=500)

    return wrapper


async def _read_json(request: Request) -> dict[str, Any]:
    """body 가 비어있어도 빈 dict 반환 (POST /analyze 등 body 없는 호출 호환)."""
    raw = await request.body()
    if not raw:
        return {}
    import json

    return json.loads(raw)


def register_rest_routes(mcp: FastMCP) -> None:
    # ─── Jobs (analysis) ──────────────────────────────────────────────

    @mcp.custom_route("/api/jobs", methods=["GET"], include_in_schema=False)
    @_with_error_handling
    async def list_jobs(request: Request) -> JSONResponse:
        params = request.query_params
        req = ListAnalysisJobsRequest(
            user_id=params.get("user_id"),
            status=JobStatus(params["status"]) if params.get("status") else None,
            limit=int(params.get("limit", 50)),
            offset=int(params.get("offset", 0)),
        )
        response = await list_analysis_jobs_service(req, get_table_store())
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/jobs/{job_id}", methods=["GET"], include_in_schema=False)
    @_with_error_handling
    async def get_job_detail(request: Request) -> JSONResponse:
        req = GetAnalysisJobDetailRequest(job_id=request.path_params["job_id"])
        response = await get_analysis_job_detail_service(
            req,
            get_table_store(),
            get_blob_store(),
            sas_expiry_hours=get_settings().report_sas_expiry_hours,
        )
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/jobs", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def create_job(request: Request) -> JSONResponse:
        body = await _read_json(request)
        req = CreateAnalysisJobRequest(
            company_name=body.get("company_name"),
            custom_prompt=body.get("custom_prompt"),
            file_blob_paths=body.get("file_blob_paths") or [],
            files=[],
            user_id=None,
        )
        settings = get_settings()
        response = await create_analysis_job_service(
            req,
            get_blob_store(),
            get_table_store(),
            upload_blob_prefix=settings.upload_blob_prefix,
        )
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/jobs/{job_id}/collect", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def collect_job(request: Request) -> JSONResponse:
        body = await _read_json(request)
        req = CollectRequest(
            job_id=request.path_params["job_id"],
            company_name=body.get("company_name"),
        )
        settings = get_settings()
        vision_anthropic = (
            get_anthropic_client()
            if settings.image_vision_enabled and settings.anthropic_api_key
            else None
        )
        response = await collect_company_data_service(
            req,
            get_blob_store(),
            get_table_store(),
            get_naver_news_client(),
            dart=get_dart_client(),
            vision_anthropic=vision_anthropic,
            vision_model=settings.image_vision_model if vision_anthropic else None,
        )
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/jobs/{job_id}/analyze", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def analyze_job(request: Request) -> JSONResponse:
        req = AnalyzeRequest(job_id=request.path_params["job_id"])
        response = await analyze_financials_service(
            req, get_blob_store(), get_table_store(), get_anthropic_client()
        )
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/jobs/{job_id}/report", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def report_job(request: Request) -> JSONResponse:
        req = ReportRequest(job_id=request.path_params["job_id"])
        settings = get_settings()
        response = await report_generate_service(
            req,
            get_blob_store(),
            get_table_store(),
            get_report_anthropic_client(),
            sas_expiry_hours=settings.report_sas_expiry_hours,
            base_docx_blob_path=settings.report_base_docx_blob_path,
            appendix_row_threshold=settings.appendix_row_threshold,
            narrative_anthropic=get_narrative_anthropic_client(),
        )
        return JSONResponse(response.model_dump(mode="json"))

    # ─── Upload URLs ──────────────────────────────────────────────────

    @mcp.custom_route("/api/upload-urls", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def create_upload_url(request: Request) -> JSONResponse:
        body = await _read_json(request)
        req = CreateUploadUrlRequest(
            filename=body.get("filename"),
            content_type=body.get("content_type"),
        )
        settings = get_settings()
        response = create_upload_url_service(
            req,
            get_blob_store(),
            expiry_minutes=settings.upload_sas_expiry_minutes,
            upload_prefix=settings.upload_blob_prefix,
        )
        return JSONResponse(response.model_dump(mode="json"))

    # ─── Monitors ─────────────────────────────────────────────────────

    @mcp.custom_route("/api/monitors", methods=["GET"], include_in_schema=False)
    @_with_error_handling
    async def list_monitors(_request: Request) -> JSONResponse:
        response = await monitor_list_service(get_table_store())
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route(
        "/api/monitors/{company_id}/snapshot",
        methods=["GET"],
        include_in_schema=False,
    )
    @_with_error_handling
    async def get_monitor_snapshot(request: Request) -> JSONResponse:
        req = MonitorGetLatestSnapshotRequest(company_id=request.path_params["company_id"])
        response = await monitor_get_latest_snapshot_service(
            req, get_table_store(), get_blob_store()
        )
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/monitors", methods=["POST"], include_in_schema=False)
    @_with_error_handling
    async def register_monitor(request: Request) -> JSONResponse:
        body = await _read_json(request)
        req = MonitorRegisterRequest(
            company_id=body.get("company_id"),
            company_name=body.get("company_name"),
            recipient_email=body.get("recipient_email"),
            origin_job_id=body.get("origin_job_id"),
        )
        response = await monitor_register_service(req, get_table_store())
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route("/api/monitors/{company_id}", methods=["DELETE"], include_in_schema=False)
    @_with_error_handling
    async def deregister_monitor(request: Request) -> JSONResponse:
        req = MonitorDeregisterRequest(company_id=request.path_params["company_id"])
        response = await monitor_deregister_service(req, get_table_store())
        return JSONResponse(response.model_dump(mode="json"))

    @mcp.custom_route(
        "/api/monitors/{company_id}/run",
        methods=["POST"],
        include_in_schema=False,
    )
    @_with_error_handling
    async def run_monitor_now(request: Request) -> JSONResponse:
        req = MonitorRunNowRequest(company_id=request.path_params["company_id"])
        response = await monitor_run_now_service(
            req,
            get_blob_store(),
            get_table_store(),
            get_naver_news_client(),
            get_anthropic_client(),
            gmail=get_gmail_client(),
            settings=get_settings(),
        )
        return JSONResponse(response.model_dump(mode="json"))
