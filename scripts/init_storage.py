"""Azure Storage 초기 셋업 — Blob 컨테이너 + Table 10개 + templates/ 하위 폴더를 생성한다.

멱등 — 이미 존재하면 그냥 통과한다. 신규 환경 셋업이나 신규 테이블/폴더 추가 후 재실행해도 안전.

실행:
    uv run python scripts/init_storage.py
"""

import asyncio

from src.config.logging import configure_logging, get_logger
from src.config.settings import get_settings
from src.storage.blob_store import BlobStore
from src.storage.table_store import ALL_TABLES, TableStore

# Azure Blob 은 진짜 폴더 개념이 없어서 prefix 에 blob 이 1개라도 있어야 Portal 에서 폴더로 보임.
# 각 templates/ 하위 폴더에 _README.md 를 두면 (1) Portal 에서 폴더 가시성 (2) 사용자가 Portal 에서
# 폴더 누를 때 어떤 형식을 올려야 하는지 안내 — 두 가지를 동시에 충족.
_REPORT_SAMPLES_README = """\
# Report Samples

Agent 3 (report_generate) startup 시 로드되는 보고서 전거 샘플.

- 지원 형식: `.docx`
- 외부 보고서를 익명화 후 업로드 (실 고객정보 금지)
"""

_FINANCIAL_SAMPLES_README = """\
# Financial Samples

Agent 2 (analyze_financials) startup 시 로드되는 전거 샘플.

- 지원 형식: `.docx`, `.pdf`, `.xls`, `.xlsx`
- 숫자 위주는 `.xlsx` 권장 (다년도 매핑 정확도 ↑)
- 외부 보고서를 익명화 후 업로드 (실 고객정보 금지)
"""

TEMPLATE_FOLDERS: tuple[tuple[str, bytes], ...] = (
    ("templates/report_samples/_README.md", _REPORT_SAMPLES_README.encode("utf-8")),
    ("templates/financial_samples/_README.md", _FINANCIAL_SAMPLES_README.encode("utf-8")),
)


async def _ensure_template_folders(blob: BlobStore, logger) -> None:
    for path, content in TEMPLATE_FOLDERS:
        if await blob.exists(path):
            logger.debug("blob.template_folder.exists", path=path)
            continue
        await blob.upload(path, content, content_type="text/markdown; charset=utf-8")
        logger.info("blob.template_folder.created", path=path)


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt="console")
    logger = get_logger("init_storage")

    if not settings.azure_storage_connection_string:
        raise SystemExit("AZURE_STORAGE_CONNECTION_STRING not set in .env")

    logger.info(
        "init.start",
        container=settings.azure_storage_blob_container,
        tables=len(ALL_TABLES),
        template_folders=len(TEMPLATE_FOLDERS),
    )

    async with BlobStore(
        settings.azure_storage_connection_string,
        settings.azure_storage_blob_container,
    ) as blob:
        await blob.initialize_container()
        await _ensure_template_folders(blob, logger)

    async with TableStore(settings.azure_storage_connection_string) as tables:
        await tables.initialize_tables()

    logger.info("init.done")


if __name__ == "__main__":
    asyncio.run(main())
