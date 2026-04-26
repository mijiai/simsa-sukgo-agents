"""Azure Storage 초기 셋업 — Blob 컨테이너 + Table 10개를 생성한다.

멱등 — 이미 존재하면 그냥 통과한다. 신규 환경 셋업이나 신규 테이블 추가 후 재실행해도 안전.

실행:
    uv run python scripts/init_storage.py
"""

import asyncio

from src.config.logging import configure_logging, get_logger
from src.config.settings import get_settings
from src.storage.blob_store import BlobStore
from src.storage.table_store import ALL_TABLES, TableStore


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
    )

    async with BlobStore(
        settings.azure_storage_connection_string,
        settings.azure_storage_blob_container,
    ) as blob:
        await blob.initialize_container()

    async with TableStore(settings.azure_storage_connection_string) as tables:
        await tables.initialize_tables()

    logger.info("init.done")


if __name__ == "__main__":
    asyncio.run(main())
