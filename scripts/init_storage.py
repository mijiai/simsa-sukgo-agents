"""Azure Storage 초기 셋업 — Blob 컨테이너 + Table 10개 + 안내용 placeholder blob 들을 생성한다.

멱등 — 이미 존재하면 그냥 통과한다. 신규 환경 셋업이나 신규 테이블/폴더 추가 후 재실행해도 안전.

생성되는 placeholder:
- templates/report_samples/_README.md
- templates/financial_samples/_README.md
- internal/_README.md

실행:
    uv run python scripts/init_storage.py
"""

import asyncio

from src.config.logging import configure_logging, get_logger
from src.config.settings import get_settings
from src.storage.blob_store import BlobStore
from src.storage.table_store import ALL_TABLES, TableStore

# Azure Blob 은 진짜 폴더 개념이 없어서 prefix 에 blob 이 1개라도 있어야 Portal 에서 폴더로 보임.
# 각 폴더에 _README.md 를 두면 (1) Portal 에서 폴더 가시성 (2) 사용자가 Portal 에서
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
- 모든 분석에 동일하게 inject — 톤·관점·판단 기준 가이드 용도
"""

_INTERNAL_README = """\
# Internal Credit DB

사전 등록된 기업의 내부 신용분석 자료 (Agent 2 분석 시 회사 매칭 → inject).

레이아웃:
```
internal/companies/{company_id}/financial.{xlsx,xls,pdf,docx}
```

- `company_id` 는 `Companies` 테이블의 RowKey 와 일치 (보통 사업자번호 10자리)
- 회사당 1개 파일 (`financial.*`)
- startup 시 일괄 로드 → 메모리 캐시
- collect_company_data 가 회사명 매칭하면 raw.json 의 `internal_credit_data` 필드에 inject
- 매칭 실패 시 분석은 외부 자료 (뉴스 / 첨부) 만으로 진행
"""

PLACEHOLDER_BLOBS: tuple[tuple[str, bytes], ...] = (
    ("templates/report_samples/_README.md", _REPORT_SAMPLES_README.encode("utf-8")),
    ("templates/financial_samples/_README.md", _FINANCIAL_SAMPLES_README.encode("utf-8")),
    ("internal/_README.md", _INTERNAL_README.encode("utf-8")),
)


async def _ensure_placeholder_blobs(blob: BlobStore, logger) -> None:
    for path, content in PLACEHOLDER_BLOBS:
        if await blob.exists(path):
            logger.debug("blob.placeholder.exists", path=path)
            continue
        await blob.upload(path, content, content_type="text/markdown; charset=utf-8")
        logger.info("blob.placeholder.created", path=path)


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
        placeholder_blobs=len(PLACEHOLDER_BLOBS),
    )

    async with BlobStore(
        settings.azure_storage_connection_string,
        settings.azure_storage_blob_container,
    ) as blob:
        await blob.initialize_container()
        await _ensure_placeholder_blobs(blob, logger)

    async with TableStore(settings.azure_storage_connection_string) as tables:
        await tables.initialize_tables()

    logger.info("init.done")


if __name__ == "__main__":
    asyncio.run(main())
