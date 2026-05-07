from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    title: str
    description: str
    url: str
    naver_link: str
    published_at: datetime


class CollectRequest(BaseModel):
    job_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1, max_length=200)


class ExtractedTable(BaseModel):
    source_file: str
    sheet_name: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False


class ExtractedImage(BaseModel):
    source_file: str
    blob_path: str
    suspected_role: str = "unknown"
    width: int | None = None
    height: int | None = None
    caption: str | None = None


class ExtractedDoc(BaseModel):
    source_file: str
    text: str = ""
    page_count: int = 0
    truncated: bool = False


# ─── DART 재무 데이터 스키마 ─────────────────────────────────────────────────

class DartKeyAccounts(BaseModel):
    """단일회사 주요계정 정규화 결과 — 원단위(KRW)."""

    total_assets: int | None = None
    total_liabilities: int | None = None
    total_equity: int | None = None
    current_assets: int | None = None
    noncurrent_assets: int | None = None
    current_liabilities: int | None = None
    noncurrent_liabilities: int | None = None
    revenue: int | None = None
    gross_profit: int | None = None
    operating_income: int | None = None
    net_income: int | None = None
    net_income_parent: int | None = None
    operating_cf: int | None = None
    finance_costs: int | None = None
    interest_expense: int | None = None
    depreciation: int | None = None
    # 추가
    paid_in_capital: int | None = None
    retained_earnings: int | None = None
    pretax_income: int | None = None
    total_comprehensive_income: int | None = None


# DartKeyAccounts 유효 필드 집합 — dart_client.py 의 _VALID_ACCOUNT_FIELDS 와 동기화
_DART_KEY_ACCOUNT_FIELDS: frozenset[str] = frozenset(DartKeyAccounts.model_fields.keys())


class DartFinancialYear(BaseModel):
    """연도별 DART 재무 데이터 — raw.json 의 dart_financials 리스트 원소.

    dart_client.get_key_accounts() 가 반환하는 3개 기간(당기/전기/전전기) 중
    하나에 대응한다.
    """

    year: int
    period: str = ""          # "thstrm" | "frmtrm" | "bfefrmtrm"
    period_nm: str = ""       # DART 원본 기간명 (예: "제25기 (2024.01.01~2024.12.31)")
    reprt_code: str = "11011"
    fs_div: str = ""          # "CFS"(연결) | "OFS"(별도) | ""
    currency: str = "KRW"
    accounts: DartKeyAccounts
    has_data: bool = False
    error: str | None = None
    raw_items: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_dart_result(cls, result: dict[str, Any]) -> "DartFinancialYear":
        """dart_client.get_key_accounts() 의 개별 기간 dict 로부터 생성.

        accounts 딕셔너리 → DartKeyAccounts 변환 시 Pydantic model_fields 를 사용해
        유효 필드만 필터링 (hasattr 는 Pydantic v2 에서 신뢰할 수 없음).
        """
        accounts_raw: dict[str, Any] = result.get("accounts") or {}
        # 유효 필드만 추출 — 알 수 없는 키가 있어도 ValidationError 방지
        valid_accounts = {
            k: v for k, v in accounts_raw.items()
            if k in _DART_KEY_ACCOUNT_FIELDS
        }
        return cls(
            year=result["year"],
            period=result.get("period", ""),
            period_nm=result.get("period_nm", ""),
            reprt_code=result.get("reprt_code", "11011"),
            fs_div=result.get("fs_div", ""),
            currency=result.get("currency", "KRW"),
            accounts=DartKeyAccounts(**valid_accounts),
            has_data=result.get("has_data", False),
            error=result.get("error"),
            raw_items=result.get("raw_items") or [],
        )


class CollectResponse(BaseModel):
    job_id: str
    status: Literal["collect_done"] = "collect_done"
    company_name: str
    company_id: str | None = None
    news_count: int
    lawsuit_count: int = 0
    financial_years: list[int] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list)
    has_internal_credit_data: bool = False
    extracted_table_count: int = 0
    extracted_image_count: int = 0
    extracted_doc_count: int = 0
    dart_corp_code: str | None = None
    dart_financial_years: list[int] = Field(
        default_factory=list,
        description="DART 에서 실제 데이터를 수신한 연도 목록",
    )
    output_blob_path: str