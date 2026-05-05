import base64
import binascii
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.config.logging import get_logger
from src.config.settings import get_settings
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


class CreateUploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=255)


class CreateUploadUrlResponse(BaseModel):
    upload_url: str
    blob_path: str
    expires_at: datetime
    required_headers: dict[str, str]


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


def create_upload_url_service(
    request: CreateUploadUrlRequest,
    blob: BlobStore,
    *,
    expiry_minutes: int,
    upload_prefix: str,
) -> CreateUploadUrlResponse:
    safe_name = _sanitize_filename(request.filename)
    upload_id = str(uuid4())
    blob_path = f"{upload_prefix}{upload_id}/{safe_name}"

    upload_url, expires_at = blob.generate_upload_sas_url(
        blob_path,
        timedelta(minutes=expiry_minutes),
        content_type=request.content_type,
    )

    required_headers: dict[str, str] = {"x-ms-blob-type": "BlockBlob"}
    if request.content_type:
        required_headers["Content-Type"] = request.content_type

    logger.info(
        "create_upload_url.issued",
        blob_path=blob_path,
        expires_at=expires_at.isoformat(),
        has_content_type=request.content_type is not None,
    )

    return CreateUploadUrlResponse(
        upload_url=upload_url,
        blob_path=blob_path,
        expires_at=expires_at,
        required_headers=required_headers,
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

    @mcp.tool()
    async def create_upload_url(
        filename: str,
        content_type: str | None = None,
    ) -> dict:
        """
        artifact 가 큰 파일을 직접 Azure Blob 에 업로드하기 위한 1회용 SAS URL 발급.

        사용 이유:
        - create_analysis_job 의 files 인자(base64) 는 LLM 출력 토큰을 거치므로
          ~50KB 가 실용 한계. 그 이상은 이 Tool 로 업로드 후 file_blob_paths
          (PR-B 예정) 로 경로만 전달.
        - artifact JS 가 직접 fetch(PUT, upload_url) 호출 → Claude 우회.

        사용 시점:
        - artifact 가 사용자 첨부 파일 1개당 1회 호출
        - upload_url 만료(default 15분) 안에 PUT 완료해야 함

        입력:
        - filename: 원본 파일명 (경로 components 제거됨; basename 만 사용)
        - content_type: 선택. 지정 시 SAS 가 강제하므로 PUT 시 동일 헤더 필요.

        출력:
        - upload_url: PUT 대상 SAS URL
        - blob_path: PUT 성공 후 file_blob_paths 로 넘길 경로
        - expires_at: SAS 만료 시각 (ISO8601 UTC)
        - required_headers: PUT 시 반드시 포함할 헤더 (x-ms-blob-type 등)

        보안:
        - 업로드별 uuid prefix (uploads/{uuid}/) 로 격리 → 다른 path injection 차단
        - write+create 권한만 (read 없음)
        - 짧은 TTL (default 15분)
        """
        request = CreateUploadUrlRequest(filename=filename, content_type=content_type)
        settings = get_settings()
        response = create_upload_url_service(
            request,
            get_blob_store(),
            expiry_minutes=settings.upload_sas_expiry_minutes,
            upload_prefix=settings.upload_blob_prefix,
        )
        return response.model_dump(mode="json")
