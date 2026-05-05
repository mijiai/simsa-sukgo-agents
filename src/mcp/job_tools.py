import base64
import binascii
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.common.constants import RiskLevel
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
    file_blob_paths: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="create_upload_url 로 업로드된 blob path 목록. "
        "uploads/ prefix 하위만 허용; 그 외는 거부 (path injection 방어).",
    )
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


class ListAnalysisJobsRequest(BaseModel):
    user_id: str | None = Field(default=None, description="요청 사용자 필터; None 이면 전체")
    status: JobStatus | None = Field(
        default=None, description="JobStatus filter (예: 'done', 'collecting'). None 이면 전체"
    )
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ListedAnalysisJob(BaseModel):
    job_id: str
    company_id: str | None = None
    company_name: str
    status: JobStatus
    user_id: str | None = None
    risk_level: RiskLevel | None = None
    current_agent: AgentName | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    report_blob_path: str | None = None


class ListAnalysisJobsResponse(BaseModel):
    jobs: list[ListedAnalysisJob] = Field(default_factory=list)
    total: int = Field(ge=0, description="필터 후 전체 매치 count (페이지 슬라이스 전)")
    limit: int
    offset: int


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


def _validate_uploaded_blob_path(path: str, allowed_prefix: str) -> str:
    """artifact 가 넘긴 blob_path 가 uploads/ 격리 prefix 안에 있는지 검증.

    create_upload_url 이 발급한 path 가 아니면 거부 — 임의 blob 복사 시도 차단
    (jobs/X/report/result.json 같은 internal path 로 덮어쓰는 공격 방어).
    """
    if not path.startswith(allowed_prefix):
        raise ValueError(f"file_blob_paths 항목은 '{allowed_prefix}' 로 시작해야 합니다: {path!r}")
    if ".." in path.split("/"):
        raise ValueError(f"path traversal 의심 segment 포함: {path!r}")
    basename = os.path.basename(path)
    if not basename or basename in {".", ".."}:
        raise ValueError(f"basename 이 비어있습니다: {path!r}")
    return basename


async def create_analysis_job_service(
    request: CreateAnalysisJobRequest,
    blob: BlobStore,
    tables: TableStore,
    *,
    upload_blob_prefix: str = "uploads/",
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

    for src_path in request.file_blob_paths:
        basename = _validate_uploaded_blob_path(src_path, upload_blob_prefix)
        dst_path = f"{input_prefix}{basename}"
        await blob.copy(src_path, dst_path)
        uploaded.append(dst_path)
        logger.info(
            "create_job.file_blob_path.copied",
            src=src_path,
            dst=dst_path,
        )

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
            company_id=company_id,
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


async def list_analysis_jobs_service(
    request: ListAnalysisJobsRequest,
    tables: TableStore,
) -> ListAnalysisJobsResponse:
    jobs, total = await tables.jobs.list_filtered(
        user_id=request.user_id,
        status=request.status,
        limit=request.limit,
        offset=request.offset,
    )
    items = [
        ListedAnalysisJob(
            job_id=j.job_id,
            company_id=j.company_id,
            company_name=j.company_name,
            status=j.status,
            user_id=j.user_id,
            risk_level=j.risk_level,
            current_agent=j.current_agent,
            created_at=j.created_at,
            updated_at=j.updated_at,
            finished_at=j.finished_at,
            report_blob_path=j.report_blob_path,
        )
        for j in jobs
    ]
    logger.info(
        "list_analysis_jobs.done",
        total=total,
        returned=len(items),
        user_id=request.user_id,
        status=request.status.value if request.status else None,
    )
    return ListAnalysisJobsResponse(
        jobs=items, total=total, limit=request.limit, offset=request.offset
    )


def register_job_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def create_analysis_job(
        company_name: str,
        custom_prompt: str | None = None,
        files: list[InputFile] | None = None,
        file_blob_paths: list[str] | None = None,
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

        파일 첨부 두 가지 방식 (혼합 가능):
        1) files (base64 inline): LLM 출력 토큰을 거치므로 ~50KB 이하 권장
           — custom_prompt.txt 같은 작은 텍스트만.
        2) file_blob_paths: artifact 가 create_upload_url 로 받은 SAS URL 에
           직접 PUT 한 뒤 그 blob_path 목록을 전달. 크기 제한 사실상 없음.
           uploads/ prefix 하위만 허용 (path injection 방어).

        입력:
        - company_name: 분석할 기업명 (필수)
        - custom_prompt: 분석 요청 프롬프트 (선택, 1만자 이내)
        - files: 인라인 첨부 파일 (선택, 각 항목 filename + content_base64)
        - file_blob_paths: 사전 업로드된 blob path 목록 (선택)
        - user_id: 요청 사용자 식별자 (선택)

        출력:
        - job_id: 후속 Agent에 전달할 식별자
        - status: "ready"
        - company_id: 기업 마스터 식별자 (Companies 테이블)
        - input_blob_paths: jobs/{job_id}/input/ 하위로 정규화된 파일 경로 목록
        """
        request = CreateAnalysisJobRequest(
            company_name=company_name,
            custom_prompt=custom_prompt,
            files=files or [],
            file_blob_paths=file_blob_paths or [],
            user_id=user_id,
        )
        settings = get_settings()
        response = await create_analysis_job_service(
            request,
            get_blob_store(),
            get_table_store(),
            upload_blob_prefix=settings.upload_blob_prefix,
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

    @mcp.tool()
    async def list_analysis_jobs(
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """
        분석 Job 목록 조회 — frontend 의 list 페이지 mount 시 사용.

        사용 시점:
        - 사용자 dashboard / "내 분석 목록" 페이지 mount

        입력:
        - user_id: 옵션. 지정 시 해당 사용자 Job 만 (전체는 None)
        - status: 옵션. JobStatus 값 ('pending', 'collecting', 'analyzing',
          'reporting', 'done', 'failed'). 전체는 None
        - limit: 1~200, default 50
        - offset: pagination, default 0

        출력:
        - jobs: [{ job_id, company_id, company_name, status, user_id,
                   risk_level, current_agent, created_at, updated_at,
                   finished_at, report_blob_path }]
                created_at DESC 정렬.
        - total: 필터 후 전체 매치 count (페이지 슬라이스 전 — pagination 표시용)
        - limit, offset: 입력 그대로 echo

        주의:
        - 응답에 job 본문 데이터 (collect/raw, analyze/result 등) 는 포함 X.
          상세는 별도 도구로 (예: monitor 의 monitor_get_latest_snapshot 패턴).
        - PoC 구현은 메모리 sort/slice — 운영 규모 큰 데이터셋이면 별도 인덱스 필요.
        """
        status_enum = JobStatus(status) if status else None
        request = ListAnalysisJobsRequest(
            user_id=user_id, status=status_enum, limit=limit, offset=offset
        )
        response = await list_analysis_jobs_service(request, get_table_store())
        return response.model_dump(mode="json")
