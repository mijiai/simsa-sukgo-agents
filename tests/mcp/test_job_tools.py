import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.mcp.job_tools import (
    CreateAnalysisJobRequest,
    CreateUploadUrlRequest,
    InputFile,
    _sanitize_filename,
    create_analysis_job_service,
    create_upload_url_service,
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
