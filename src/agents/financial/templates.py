from io import BytesIO

from docx import Document
from pypdf import PdfReader

from src.config.logging import get_logger
from src.storage.blob_store import BlobStore

logger = get_logger(__name__)

_DOCX_SUFFIX = ".docx"
_PDF_SUFFIX = ".pdf"
_samples: list[str] = []


def extract_docx_text(data: bytes) -> str:
    doc = Document(BytesIO(data))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract(path: str, data: bytes) -> str:
    lower = path.lower()
    if lower.endswith(_DOCX_SUFFIX):
        return extract_docx_text(data)
    if lower.endswith(_PDF_SUFFIX):
        return extract_pdf_text(data)
    return ""


async def load_financial_samples(blob: BlobStore, prefix: str) -> list[str]:
    """List + download + extract all .docx / .pdf samples under the given Blob prefix.

    Caches the result in module memory so the analyze service can read without I/O.
    Idempotent — calling again refreshes the cache.
    """
    global _samples
    paths = await blob.list_prefix(prefix)
    sample_paths = sorted(p for p in paths if p.lower().endswith((_DOCX_SUFFIX, _PDF_SUFFIX)))

    samples: list[str] = []
    for path in sample_paths:
        try:
            data = await blob.download(path)
            text = _extract(path, data)
        except Exception as exc:
            logger.warning("financial.sample.load_failed", path=path, error=str(exc))
            continue
        if not text:
            logger.warning("financial.sample.empty", path=path)
            continue
        samples.append(text)
        logger.info("financial.sample.loaded", path=path, chars=len(text))

    _samples = samples
    logger.info("financial.samples.cached", count=len(samples), prefix=prefix)
    return samples


def get_cached_samples() -> list[str]:
    return list(_samples)


def reset_samples_for_tests(samples: list[str] | None = None) -> None:
    global _samples
    _samples = list(samples) if samples else []
