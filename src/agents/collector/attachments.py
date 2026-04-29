"""사용자 첨부 파일 파싱 — Blob `jobs/{job_id}/input/*` 의 실제 내용을 추출.

기존엔 collector 가 파일명만 raw.json 에 적었음. 그 결과 사용자가 PDF/XLSX 첨부해도
financial Agent 는 파일명만 보고 내용은 모르는 상황. 이 모듈이 파일을 다운로드해
텍스트를 뽑아 raw.json 의 `attached_documents` 필드에 채운다.

지원 형식: .docx / .pdf / .xls / .xlsx (templates/financial 과 동일)
지원 안 되는 확장자는 silent skip + 로그 (e.g. .png, .csv).
손상 파일은 warning + 빈 텍스트로 기록.

토큰 폭발 방지를 위해 파일당 8000자 truncate (internal_credit_data 와 동일 정책).
"""

from dataclasses import dataclass

from src.agents.financial.templates import extract_document_text
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore

logger = get_logger(__name__)

INPUT_PREFIX_TEMPLATE = "jobs/{job_id}/input/"
PROMPT_FILENAME = "prompt.txt"
PER_FILE_TRUNCATE = 8000
_SUPPORTED_SUFFIXES = (".docx", ".pdf", ".xls", ".xlsx")


@dataclass(frozen=True)
class ExtractedAttachment:
    filename: str
    text: str  # 추출된 텍스트 (truncate 적용). 미지원 형식·실패 시 빈 문자열
    chars: int  # text 길이 — UI 표시 / 응답 메타


async def extract_attachments(blob: BlobStore, job_id: str) -> list[ExtractedAttachment]:
    """List + download + extract all attachments under `jobs/{job_id}/input/`.

    Returns:
        ExtractedAttachment 리스트. 파일이 없으면 빈 리스트.
        지원 안 되는 확장자도 (filename + 빈 text) 형태로 포함됨 → 사용자에게
        "이 파일은 첨부되었지만 파싱은 못 했음" 신호를 raw.json 에 남길 수 있음.
    """
    prefix = INPUT_PREFIX_TEMPLATE.format(job_id=job_id)
    prompt_path = f"{prefix}{PROMPT_FILENAME}"
    paths = await blob.list_prefix(prefix)

    results: list[ExtractedAttachment] = []
    for path in sorted(paths):
        if path == prompt_path or not path.startswith(prefix):
            continue
        filename = path[len(prefix) :]
        if not filename:
            continue

        text = ""
        if filename.lower().endswith(_SUPPORTED_SUFFIXES):
            try:
                data = await blob.download(path)
                text = extract_document_text(path, data)[:PER_FILE_TRUNCATE]
            except Exception as exc:
                logger.warning("collect.attachment.failed", path=path, error=str(exc))
                text = ""
            if not text:
                logger.warning("collect.attachment.empty", path=path)
        else:
            logger.info("collect.attachment.unsupported_skipped", filename=filename)

        results.append(ExtractedAttachment(filename=filename, text=text, chars=len(text)))

    extracted_count = sum(1 for a in results if a.text)
    logger.info(
        "collect.attachments.summary",
        job_id=job_id,
        total=len(results),
        extracted=extracted_count,
        skipped=len(results) - extracted_count,
    )
    return results
