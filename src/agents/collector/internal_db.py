"""내부 신용 DB 로더 — 사전 등록된 기업의 재무 데이터를 startup 시 메모리 캐시.

Blob 레이아웃:
    internal/companies/{company_id}/financial.{xlsx,xls,pdf,docx}

여기서 `company_id` 는 보통 사업자번호 (10자리). `Companies` 테이블의 RowKey 와 일치.

흐름:
    startup → load_internal_db() → dict[company_id, extracted_text]
    collect_company_data() → Companies.find_by_name() 으로 company_id 얻으면
                           → get_company_data(company_id) → raw.json 의
                             internal_credit_data 필드에 채움
    analyze_financials() → raw.internal_credit_data 를 prompt 에 inject
"""

from src.agents.financial.templates import extract_document_text
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore

logger = get_logger(__name__)

INTERNAL_PREFIX = "internal/companies/"
_BASE_NAME = "financial."
_SUPPORTED_SUFFIXES = (".docx", ".pdf", ".xls", ".xlsx")

_internal_db: dict[str, str] = {}


def _parse_company_id(path: str, prefix: str) -> str | None:
    """`internal/companies/1248100998/financial.xlsx` → `1248100998`."""
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :]
    if "/" not in rest:
        return None
    company_id, filename = rest.split("/", 1)
    lower = filename.lower()
    if not lower.startswith(_BASE_NAME) or not lower.endswith(_SUPPORTED_SUFFIXES):
        return None
    return company_id


async def load_internal_db(blob: BlobStore, prefix: str = INTERNAL_PREFIX) -> dict[str, str]:
    """List + extract all per-company internal credit files into a memory cache.

    Idempotent — re-running refreshes the cache.
    """
    global _internal_db
    paths = await blob.list_prefix(prefix)

    db: dict[str, str] = {}
    for path in sorted(paths):
        company_id = _parse_company_id(path, prefix)
        if company_id is None:
            continue
        try:
            data = await blob.download(path)
            text = extract_document_text(path, data)
        except Exception as exc:
            logger.warning("internal_db.load_failed", path=path, error=str(exc))
            continue
        if not text:
            logger.warning("internal_db.empty", path=path)
            continue
        db[company_id] = text
        logger.info("internal_db.loaded", company_id=company_id, path=path, chars=len(text))

    _internal_db = db
    logger.info("internal_db.cached", count=len(db), prefix=prefix)
    return db


def get_company_data(company_id: str) -> str | None:
    return _internal_db.get(company_id)


def reset_for_tests(db: dict[str, str] | None = None) -> None:
    global _internal_db
    _internal_db = dict(db) if db else {}
