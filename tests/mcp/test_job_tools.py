import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.mcp.job_tools import (
    CreateAnalysisJobRequest,
    CreateUploadUrlRequest,
    GetAnalysisJobDetailRequest,
    InputFile,
    ListAnalysisJobsRequest,
    _sanitize_filename,
    create_analysis_job_service,
    create_upload_url_service,
    get_analysis_job_detail_service,
    list_analysis_jobs_service,
)
from src.storage.schemas import AgentName, Company, JobStatus


def _now() -> datetime:
    return datetime(2026, 4, 26, 9, 0, tzinfo=UTC)


def _make_stores() -> tuple[MagicMock, MagicMock]:
    blob = MagicMock()
    blob.upload = AsyncMock()
    blob.copy = AsyncMock()

    tables = MagicMock()
    tables.companies = MagicMock()
    tables.companies.find_by_name = AsyncMock(return_value=None)
    tables.companies.upsert = AsyncMock()
    tables.jobs = MagicMock()
    tables.jobs.upsert = AsyncMock()
    tables.jobs_ref = MagicMock()
    tables.jobs_ref.upsert = AsyncMock()
    tables.agent_status = MagicMock()
    tables.agent_status.upsert = AsyncMock()
    return blob, tables


async def test_create_job_full_flow_with_new_company() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME Corp",
        custom_prompt="Do thorough analysis",
        files=[
            InputFile(
                filename="report.pdf",
                content_base64=base64.b64encode(b"PDF data").decode(),
                content_type="application/pdf",
            )
        ],
        user_id="user-1",
    )

    response = await create_analysis_job_service(request, blob, tables)

    tables.companies.find_by_name.assert_awaited_once_with("ACME Corp")
    tables.companies.upsert.assert_awaited_once()
    company_arg = tables.companies.upsert.call_args.args[0]
    assert company_arg.company_id == response.company_id
    assert company_arg.company_name == "ACME Corp"

    tables.jobs.upsert.assert_awaited_once()
    job_arg = tables.jobs.upsert.call_args.args[0]
    assert job_arg.job_id == response.job_id
    assert job_arg.company_name == "ACME Corp"
    assert job_arg.user_id == "user-1"
    assert job_arg.status is JobStatus.PENDING
    assert job_arg.input_blob_prefix == f"jobs/{response.job_id}/input/"

    tables.jobs_ref.upsert.assert_awaited_once()
    ref_arg = tables.jobs_ref.upsert.call_args.args[0]
    assert ref_arg.job_id == response.job_id
    assert ref_arg.company_id == response.company_id
    assert ref_arg.status is JobStatus.PENDING

    assert tables.agent_status.upsert.await_count == 3
    agent_names = {call.args[0].agent_name for call in tables.agent_status.upsert.call_args_list}
    assert agent_names == {AgentName.COLLECT, AgentName.ANALYZE, AgentName.REPORT}

    assert blob.upload.await_count == 2
    file_call = blob.upload.call_args_list[0]
    assert file_call.args[0] == f"jobs/{response.job_id}/input/report.pdf"
    assert file_call.args[1] == b"PDF data"
    assert file_call.kwargs["content_type"] == "application/pdf"
    prompt_call = blob.upload.call_args_list[1]
    assert prompt_call.args[0] == f"jobs/{response.job_id}/input/prompt.txt"
    assert prompt_call.args[1] == b"Do thorough analysis"

    assert response.status == "ready"
    assert len(response.input_blob_paths) == 2


async def test_create_job_reuses_existing_company() -> None:
    blob, tables = _make_stores()
    existing = Company(
        company_id="existing-id-123",
        company_name="ACME",
        created_at=_now(),
        updated_at=_now(),
    )
    tables.companies.find_by_name = AsyncMock(return_value=existing)

    request = CreateAnalysisJobRequest(company_name="ACME")
    response = await create_analysis_job_service(request, blob, tables)

    assert response.company_id == "existing-id-123"
    tables.companies.upsert.assert_not_called()
    tables.jobs_ref.upsert.assert_awaited_once()
    ref_arg = tables.jobs_ref.upsert.call_args.args[0]
    assert ref_arg.company_id == "existing-id-123"


async def test_create_job_no_files_no_prompt_skips_blob_upload() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(company_name="ACME")
    response = await create_analysis_job_service(request, blob, tables)

    blob.upload.assert_not_called()
    assert response.input_blob_paths == []
    assert response.status == "ready"


async def test_create_job_invalid_base64_raises_value_error() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        files=[InputFile(filename="x.bin", content_base64="not-valid-base64!!!")],
    )
    with pytest.raises(ValueError, match="invalid base64"):
        await create_analysis_job_service(request, blob, tables)


async def test_create_job_sanitizes_filename_path_traversal() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        files=[
            InputFile(
                filename="../../etc/passwd",
                content_base64=base64.b64encode(b"x").decode(),
            )
        ],
    )
    response = await create_analysis_job_service(request, blob, tables)
    blob_path = blob.upload.call_args.args[0]
    assert blob_path == f"jobs/{response.job_id}/input/passwd"


def test_sanitize_filename_strips_path_components() -> None:
    assert _sanitize_filename("../../etc/passwd") == "passwd"
    assert _sanitize_filename("/abs/path/file.txt") == "file.txt"
    assert _sanitize_filename("file.txt") == "file.txt"


def test_sanitize_filename_rejects_dotdot_and_dot() -> None:
    with pytest.raises(ValueError):
        _sanitize_filename("..")
    with pytest.raises(ValueError):
        _sanitize_filename(".")


def test_input_file_validation() -> None:
    with pytest.raises(ValidationError):
        InputFile(filename="", content_base64="abc")
    with pytest.raises(ValidationError):
        InputFile(filename="x", content_base64="")


def test_create_request_validation() -> None:
    with pytest.raises(ValidationError):
        CreateAnalysisJobRequest(company_name="")
    with pytest.raises(ValidationError):
        CreateAnalysisJobRequest(company_name="x", custom_prompt="x" * 10001)
    too_many_files = [InputFile(filename=f"f{i}.bin", content_base64="YQ==") for i in range(21)]
    with pytest.raises(ValidationError):
        CreateAnalysisJobRequest(company_name="x", files=too_many_files)


async def test_create_job_copies_file_blob_paths_into_input_prefix() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        file_blob_paths=[
            "uploads/abc123/재무.xls",
            "uploads/def456/사업계획서.pdf",
        ],
    )
    response = await create_analysis_job_service(request, blob, tables)

    assert blob.copy.await_count == 2
    src_dst = [call.args for call in blob.copy.await_args_list]
    assert (
        "uploads/abc123/재무.xls",
        f"jobs/{response.job_id}/input/재무.xls",
    ) in src_dst
    assert (
        "uploads/def456/사업계획서.pdf",
        f"jobs/{response.job_id}/input/사업계획서.pdf",
    ) in src_dst

    # input_blob_paths 에는 정규화된 jobs/.../input/ 경로만 노출
    assert all(p.startswith(f"jobs/{response.job_id}/input/") for p in response.input_blob_paths)


async def test_create_job_rejects_paths_outside_uploads_prefix() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        file_blob_paths=["jobs/other/report/report.docx"],  # 잘못된 prefix
    )
    with pytest.raises(ValueError, match="uploads/"):
        await create_analysis_job_service(request, blob, tables)
    blob.copy.assert_not_called()


async def test_create_job_rejects_path_traversal_in_blob_path() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        file_blob_paths=["uploads/abc/../../jobs/other/x.txt"],
    )
    with pytest.raises(ValueError, match="path traversal"):
        await create_analysis_job_service(request, blob, tables)
    blob.copy.assert_not_called()


async def test_create_job_combines_inline_files_and_blob_paths() -> None:
    blob, tables = _make_stores()
    request = CreateAnalysisJobRequest(
        company_name="ACME",
        files=[
            InputFile(
                filename="prompt.txt",
                content_base64=base64.b64encode(b"small").decode(),
            )
        ],
        file_blob_paths=["uploads/U/big.xls"],
    )
    response = await create_analysis_job_service(request, blob, tables)

    # base64 inline → upload, file_blob_paths → copy
    assert blob.upload.await_count == 1  # inline file (no custom_prompt → no prompt.txt)
    assert blob.copy.await_count == 1
    assert len(response.input_blob_paths) == 2


def _make_upload_blob() -> MagicMock:
    blob = MagicMock()
    blob.generate_upload_sas_url = MagicMock(
        return_value=(
            "https://acc.blob.core.windows.net/c/uploads/U/재무.xls?sig=X&se=...&sp=cw",
            datetime(2026, 5, 4, 9, 15, tzinfo=UTC),
        )
    )
    return blob


def test_create_upload_url_returns_isolated_path_and_required_headers() -> None:
    blob = _make_upload_blob()
    request = CreateUploadUrlRequest(filename="재무.xls")
    response = create_upload_url_service(request, blob, expiry_minutes=15, upload_prefix="uploads/")

    assert response.blob_path.startswith("uploads/")
    assert response.blob_path.endswith("/재무.xls")
    # 격리: filename 외에 uuid 1단계가 끼어있어야 함
    parts = response.blob_path.split("/")
    assert len(parts) == 3 and parts[0] == "uploads" and parts[1] != ""

    assert response.upload_url.startswith("https://")
    assert response.required_headers == {"x-ms-blob-type": "BlockBlob"}
    assert response.expires_at == datetime(2026, 5, 4, 9, 15, tzinfo=UTC)

    blob.generate_upload_sas_url.assert_called_once()
    call_kwargs = blob.generate_upload_sas_url.call_args
    assert call_kwargs.args[0] == response.blob_path
    assert call_kwargs.kwargs == {"content_type": None}


def test_create_upload_url_includes_content_type_header_when_given() -> None:
    blob = _make_upload_blob()
    request = CreateUploadUrlRequest(filename="x.pdf", content_type="application/pdf")
    response = create_upload_url_service(request, blob, expiry_minutes=15, upload_prefix="uploads/")

    assert response.required_headers == {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": "application/pdf",
    }
    blob.generate_upload_sas_url.assert_called_once()
    assert blob.generate_upload_sas_url.call_args.kwargs == {"content_type": "application/pdf"}


def test_create_upload_url_sanitizes_path_traversal_in_filename() -> None:
    blob = _make_upload_blob()
    request = CreateUploadUrlRequest(filename="../../etc/passwd")
    response = create_upload_url_service(request, blob, expiry_minutes=15, upload_prefix="uploads/")
    # basename 만 살아남아야 함; '..' 으로 prefix 탈출 불가
    assert "/passwd" in response.blob_path
    assert ".." not in response.blob_path


def test_create_upload_url_request_validates_filename_length() -> None:
    with pytest.raises(ValidationError):
        CreateUploadUrlRequest(filename="")
    with pytest.raises(ValidationError):
        CreateUploadUrlRequest(filename="x" * 256)


# ===== list_analysis_jobs =====


from src.common.constants import RiskLevel  # noqa: E402
from src.storage.schemas import AnalysisJob  # noqa: E402


def _job(
    job_id: str,
    *,
    company_id: str | None = "comp-1",
    user_id: str | None = "user-1",
    status: JobStatus = JobStatus.DONE,
    risk_level: RiskLevel | None = RiskLevel.MEDIUM,
    created_at: datetime | None = None,
) -> AnalysisJob:
    base = created_at or datetime(2026, 4, 28, 10, 0, tzinfo=UTC)
    return AnalysisJob(
        job_id=job_id,
        company_name="ACME",
        company_id=company_id,
        user_id=user_id,
        status=status,
        risk_level=risk_level,
        created_at=base,
        updated_at=base,
    )


def _make_tables_for_list(
    list_filtered_return: tuple[list[AnalysisJob], int],
) -> MagicMock:
    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.list_filtered = AsyncMock(return_value=list_filtered_return)
    return tables


async def test_list_jobs_propagates_filter_args_to_repo() -> None:
    tables = _make_tables_for_list(([], 0))
    request = ListAnalysisJobsRequest(user_id="user-1", status=JobStatus.DONE, limit=10, offset=20)
    await list_analysis_jobs_service(request, tables)
    tables.jobs.list_filtered.assert_awaited_once_with(
        user_id="user-1", status=JobStatus.DONE, limit=10, offset=20
    )


async def test_list_jobs_maps_jobs_to_listed_items_with_total() -> None:
    jobs = [
        _job("j-1", risk_level=RiskLevel.HIGH),
        _job("j-2", risk_level=None),
    ]
    tables = _make_tables_for_list((jobs, 7))  # 전체 7건 중 2건 페이지
    request = ListAnalysisJobsRequest(limit=2, offset=0)
    response = await list_analysis_jobs_service(request, tables)

    assert response.total == 7
    assert response.limit == 2
    assert response.offset == 0
    assert len(response.jobs) == 2
    assert response.jobs[0].job_id == "j-1"
    assert response.jobs[0].risk_level is RiskLevel.HIGH
    assert response.jobs[0].company_id == "comp-1"
    assert response.jobs[1].job_id == "j-2"
    assert response.jobs[1].risk_level is None


async def test_list_jobs_empty_result_returns_zero_total() -> None:
    tables = _make_tables_for_list(([], 0))
    request = ListAnalysisJobsRequest()
    response = await list_analysis_jobs_service(request, tables)
    assert response.total == 0
    assert response.jobs == []


def test_list_jobs_request_validates_limit_range() -> None:
    with pytest.raises(ValidationError):
        ListAnalysisJobsRequest(limit=0)
    with pytest.raises(ValidationError):
        ListAnalysisJobsRequest(limit=201)
    with pytest.raises(ValidationError):
        ListAnalysisJobsRequest(offset=-1)


# ===== get_analysis_job_detail =====


import json  # noqa: E402

from src.common.exceptions import EntityNotFoundError  # noqa: E402
from src.storage.schemas import AgentStatus, AgentStatusValue  # noqa: E402


def _job_for_detail(
    job_id: str = "j-detail",
    *,
    status: JobStatus = JobStatus.DONE,
    risk_level: RiskLevel | None = RiskLevel.HIGH,
) -> AnalysisJob:
    return AnalysisJob(
        job_id=job_id,
        company_id="comp-1",
        company_name="ACME",
        user_id="user-1",
        status=status,
        risk_level=risk_level,
        custom_prompt="분석해줘",
        created_at=datetime(2026, 4, 28, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 28, 10, 30, tzinfo=UTC),
        finished_at=datetime(2026, 4, 28, 10, 30, tzinfo=UTC),
    )


def _agent_status_row(
    job_id: str, agent: AgentName, status: AgentStatusValue = AgentStatusValue.DONE
) -> AgentStatus:
    return AgentStatus(
        job_id=job_id,
        agent_name=agent,
        status=status,
        started_at=datetime(2026, 4, 28, 10, 5, tzinfo=UTC),
        finished_at=datetime(2026, 4, 28, 10, 10, tzinfo=UTC),
        duration_sec=300,
        output_blob_path=f"jobs/{job_id}/{agent.value}/result.json",
    )


def _make_detail_deps(
    *,
    job: AnalysisJob | None = None,
    job_side_effect=None,
    agents: list[AgentStatus] | None = None,
    raw_payload: dict | None = None,
    result_payload: dict | None = None,
    md_exists: bool = False,
    docx_exists: bool = False,
    blob_download_fails: bool = False,
) -> tuple[MagicMock, MagicMock]:
    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.get = AsyncMock(
        side_effect=job_side_effect, return_value=job if job_side_effect is None else None
    )
    tables.agent_status = MagicMock()
    tables.agent_status.list_for_job = AsyncMock(return_value=agents or [])

    blob = MagicMock()
    if blob_download_fails:
        blob.download = AsyncMock(side_effect=RuntimeError("blob down"))
    else:

        async def _download(path: str) -> bytes:
            if path.endswith("/raw.json") and raw_payload is not None:
                return json.dumps(raw_payload).encode("utf-8")
            if path.endswith("/result.json") and result_payload is not None:
                return json.dumps(result_payload).encode("utf-8")
            raise RuntimeError(f"not mocked: {path}")

        blob.download = AsyncMock(side_effect=_download)

    async def _exists(path: str) -> bool:
        if path.endswith("/report.md"):
            return md_exists
        if path.endswith("/report.docx"):
            return docx_exists
        return False

    blob.exists = AsyncMock(side_effect=_exists)
    blob.generate_sas_url = MagicMock(side_effect=lambda p, _exp: f"https://sas/{p}?sig=X")
    return tables, blob


async def test_get_detail_returns_meta_when_no_artifacts() -> None:
    """status=PENDING 같은 초기 상태 — 메타만 있고 collect/analyze/report 모두 None."""
    tables, blob = _make_detail_deps(
        job=_job_for_detail(status=JobStatus.PENDING, risk_level=None),
        agents=[],
        # raw / result 다운로드는 _download 가 None payload 면 RuntimeError → 안전 fallback
    )
    response = await get_analysis_job_detail_service(
        GetAnalysisJobDetailRequest(job_id="j-detail"), tables, blob
    )
    assert response.job_id == "j-detail"
    assert response.company_name == "ACME"
    assert response.company_id == "comp-1"
    assert response.status is JobStatus.PENDING
    assert response.risk_level is None
    assert response.collect is None
    assert response.analyze is None
    assert response.report is None
    assert response.agents == []


async def test_get_detail_full_happy_path_includes_all_sections() -> None:
    raw = {
        "news": [{"t": "n1"}, {"t": "n2"}, {"t": "n3"}],
        "lawsuits": [{"x": 1}],
        "extracted_tables": [{}, {}],
        "extracted_images": [{}],
        "extracted_docs": [{}, {}, {}],
        "financial_years": [2022, 2023, 2024],
        "internal_credit_data": "...",
        "uploaded_files": ["report.xls", "plan.pdf"],
    }
    result = {
        "risk_level": "HIGH",
        "risk_score": 72.1,
        "summary": "위험 신호 다수",
        "key_risk_factors": ["부채비율", "연체율"],
        "positive_signals": ["순이익 흑자"],
        "data_gaps": ["담보 평가서 부족"],
        "section_insights": [{}, {}, {}, {}, {}],
        "model": "claude-haiku-4-5-20251001",
        "analyzed_at": "2026-04-28T10:25:00+00:00",
    }
    tables, blob = _make_detail_deps(
        job=_job_for_detail(),
        agents=[
            _agent_status_row("j-detail", AgentName.REPORT),
            _agent_status_row("j-detail", AgentName.COLLECT),
            _agent_status_row("j-detail", AgentName.ANALYZE),
        ],
        raw_payload=raw,
        result_payload=result,
        md_exists=True,
        docx_exists=True,
    )
    response = await get_analysis_job_detail_service(
        GetAnalysisJobDetailRequest(job_id="j-detail"),
        tables,
        blob,
        sas_expiry_hours=24,
    )

    # 메타
    assert response.risk_level is RiskLevel.HIGH
    assert response.user_id == "user-1"

    # agents 가 collect → analyze → report 순서로 정렬됨
    assert [a.agent_name for a in response.agents] == [
        AgentName.COLLECT,
        AgentName.ANALYZE,
        AgentName.REPORT,
    ]
    assert all(a.duration_sec == 300 for a in response.agents)

    # collect summary
    assert response.collect is not None
    assert response.collect.news_count == 3
    assert response.collect.lawsuit_count == 1
    assert response.collect.extracted_table_count == 2
    assert response.collect.extracted_image_count == 1
    assert response.collect.extracted_doc_count == 3
    assert response.collect.financial_years == [2022, 2023, 2024]
    assert response.collect.has_internal_credit_data is True
    assert response.collect.uploaded_files == ["report.xls", "plan.pdf"]

    # analyze summary
    assert response.analyze is not None
    assert response.analyze.risk_level is RiskLevel.HIGH
    assert response.analyze.risk_score == 72.1
    assert response.analyze.key_risk_factors == ["부채비율", "연체율"]
    assert response.analyze.positive_signals == ["순이익 흑자"]
    assert response.analyze.data_gaps == ["담보 평가서 부족"]
    assert response.analyze.section_insights_count == 5
    assert response.analyze.model == "claude-haiku-4-5-20251001"

    # report SAS URLs
    assert response.report is not None
    assert response.report.md_url == "https://sas/jobs/j-detail/report/report.md?sig=X"
    assert response.report.docx_url == "https://sas/jobs/j-detail/report/report.docx?sig=X"


async def test_get_detail_only_md_present_omits_docx() -> None:
    tables, blob = _make_detail_deps(
        job=_job_for_detail(),
        agents=[],
        md_exists=True,
        docx_exists=False,
    )
    response = await get_analysis_job_detail_service(
        GetAnalysisJobDetailRequest(job_id="j-detail"), tables, blob
    )
    assert response.report is not None
    assert response.report.md_url is not None
    assert response.report.docx_url is None
    assert response.report.docx_blob_path is None


async def test_get_detail_raises_when_job_missing() -> None:
    tables, blob = _make_detail_deps(
        job_side_effect=EntityNotFoundError("AnalysisJobs", "job", "missing")
    )
    with pytest.raises(EntityNotFoundError):
        await get_analysis_job_detail_service(
            GetAnalysisJobDetailRequest(job_id="missing"), tables, blob
        )


async def test_get_detail_blob_failures_fallback_to_null_sections() -> None:
    """Blob 다운로드/존재 검사 실패 → 해당 섹션만 None, 메타는 정상 반환."""
    tables, blob = _make_detail_deps(
        job=_job_for_detail(),
        agents=[],
        blob_download_fails=True,  # raw / result 둘 다 실패
        md_exists=False,
        docx_exists=False,
    )
    response = await get_analysis_job_detail_service(
        GetAnalysisJobDetailRequest(job_id="j-detail"), tables, blob
    )
    # 메타는 살아있어야 함
    assert response.job_id == "j-detail"
    assert response.risk_level is RiskLevel.HIGH
    # Blob 의존 섹션들은 None 으로 fallback
    assert response.collect is None
    assert response.analyze is None
    assert response.report is None
