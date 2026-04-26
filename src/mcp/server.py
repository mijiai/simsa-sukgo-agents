from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config.logging import get_logger

logger = get_logger(__name__)


SERVER_INSTRUCTIONS = """\
심사숙고 MCP 서버 — 기업 심사 리포트 Multi-Agent.

오케스트레이션 흐름:
  1. create_analysis_job(...)            → job_id 발급
  2. collect_company_data(job_id, ...)   → 자료 수집
  3. analyze_financials(job_id)          → 재무 분석
  4. report_generate(job_id)             → 보고서 생성
  5. (옵션) monitor_register(origin_job_id=job_id)

각 Tool은 job_id 하나만 다음 Tool에 전달하며, 대용량 데이터는 직접 주고받지 않는다.
"""


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    logger.info("mcp_server.startup")
    # TODO(step-1): Azure Storage 연결 확인 (blob/table/sql ping)
    # TODO(step-1): AgentStatus 테이블에서 status=running 잔존 Job 감지 → 재개
    # TODO(step-4): APScheduler 시작 등록
    try:
        yield
    finally:
        # TODO(step-4): APScheduler graceful shutdown
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

    return mcp
