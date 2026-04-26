import base64
import binascii
import os
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.factory import get_blob_store, get_table_store
from src.storage.schemas import (
    AgentName,
    AgentStatus,
    AgentStatusValue,
    AnalysisJob,
    AnalysisJobRef,
    Company,
    JobStatus,
)
from src.storage.table_store import TableStore

logger = get_logger(__name__)


class InputFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    content_type: str | None = None


class CreateAnalysisJobRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    custom_prompt: str | None = Field(default=None, max_length=10000)
    files: list[InputFile] = Field(default_factory=list, max_length=20)
    user_id: str | None = None


class CreateAnalysisJobResponse(BaseModel):
    job_id: str
    status: Literal["ready"] = "ready"
    company_id: str
    company_name: str
    input_blob_paths: list[str]


def _sanitize_filename(filename: str) -> str:
    basename = os.path.basename(filename)
    if not basename or basename in {".", ".."}:
        raise ValueError(f"invalid filename: {filename!r}")
    return basename


def _decode_file(file: InputFile) -> bytes:
    try:
        return base64.b64decode(file.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"file '{file.filename}': invalid base64") from exc


async def create_analysis_job_service(
    request: CreateAnalysisJobRequest,
    blob: BlobStore,
    tables: TableStore,
) -> CreateAnalysisJobResponse:
    job_id = str(uuid4())
    now = datetime.now(UTC)
    input_prefix = f"jobs/{job_id}/input/"

    existing = await tables.companies.find_by_name(request.company_name)
    if existing is not None:
        company_id = existing.company_id
        logger.info("create_job.company.reused", company_id=company_id)
    else:
        company_id = str(uuid4())
        await tables.companies.upsert(
            Company(
                company_id=company_id,
                company_name=request.company_name,
                created_at=now,
                updated_at=now,
            )
        )
        logger.info("create_job.company.created", company_id=company_id)

    uploaded: list[str] = []
    for file in request.files:
        safe_name = _sanitize_filename(file.filename)
        content = _decode_file(file)
        path = f"{input_prefix}{safe_name}"
        await blob.upload(path, content, content_type=file.content_type)
        uploaded.append(path)

    if request.custom_prompt:
        prompt_path = f"{input_prefix}prompt.txt"
        await blob.upload(
            prompt_path,
            request.custom_prompt.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        uploaded.append(prompt_path)

    await tables.jobs.upsert(
        AnalysisJob(
            job_id=job_id,
            company_name=request.company_name,
            user_id=request.user_id,
            status=JobStatus.PENDING,
            custom_prompt=request.custom_prompt,
            input_blob_prefix=input_prefix,
            created_at=now,
            updated_at=now,
        )
    )

    await tables.jobs_ref.upsert(
        AnalysisJobRef(
            job_id=job_id,
            company_id=company_id,
            company_name=request.company_name,
            status=JobStatus.PENDING,
            created_at=now,
        )
    )

    for agent_name in (AgentName.COLLECT, AgentName.ANALYZE, AgentName.REPORT):
        await tables.agent_status.upsert(
            AgentStatus(
                job_id=job_id,
                agent_name=agent_name,
                status=AgentStatusValue.PENDING,
            )
        )

    logger.info(
        "create_job.done",
        job_id=job_id,
        company_id=company_id,
        files_uploaded=len(uploaded),
    )

    return CreateAnalysisJobResponse(
        job_id=job_id,
        status="ready",
        company_id=company_id,
        company_name=request.company_name,
        input_blob_paths=uploaded,
    )


def register_job_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def create_analysis_job(
        company_name: str,
        custom_prompt: str | None = None,
        files: list[InputFile] | None = None,
        user_id: str | None = None,
    ) -> dict:
        """
        기업 심사 분석 Job을 생성한다. 분석 흐름의 가장 첫 Tool로,
        AnalysisJobs / AgentStatus / AnalysisJobsRef Table을 초기화하고
        업로드된 파일과 커스텀 프롬프트를 Blob 스토리지에 저장한다.

        사용 시점:
        - 사용자가 새 기업 분석을 시작할 때 가장 먼저 호출
        - 후속 Tool(collect_company_data, analyze_financials, report_generate)에
          반드시 이 Tool이 반환하는 job_id를 그대로 전달

        입력:
        - company_name: 분석할 기업명 (필수)
        - custom_prompt: 분석 요청 프롬프트 (선택, 1만자 이내)
        - files: 첨부 파일 목록 (선택, 각 항목은 filename + content_base64)
        - user_id: 요청 사용자 식별자 (선택)

        출력:
        - job_id: 후속 Agent에 전달할 식별자
        - status: "ready"
        - company_id: 기업 마스터 식별자 (Companies 테이블)
        - input_blob_paths: 업로드된 입력 파일 경로 목록
        """
        request = CreateAnalysisJobRequest(
            company_name=company_name,
            custom_prompt=custom_prompt,
            files=files or [],
            user_id=user_id,
        )
        response = await create_analysis_job_service(
            request,
            get_blob_store(),
            get_table_store(),
        )
        return response.model_dump()
