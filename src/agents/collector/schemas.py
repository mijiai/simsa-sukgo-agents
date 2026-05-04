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
    """업로드된 xlsx 시트 또는 PDF 표를 구조화한 결과.

    financial agent 가 columns/rows 를 직접 인용해 분석 bullet 작성.
    report agent 가 같은 데이터를 docx 표로 렌더.
    """

    source_file: str
    sheet_name: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False


class ExtractedImage(BaseModel):
    """업로드된 이미지의 메타데이터 + 휴리스틱 역할 분류.

    PR4 부터 caption 필드 추가 — IMAGE_VISION_ENABLED=true 시 collector 가 Vision API 로 채움.
    """

    source_file: str
    blob_path: str
    suspected_role: str = "unknown"  # ownership_chart / product_catalog / unknown
    width: int | None = None
    height: int | None = None
    caption: str | None = None


class ExtractedDoc(BaseModel):
    """PDF 텍스트 추출 결과 (표는 ExtractedTable 로 분리 저장)."""

    source_file: str
    text: str = ""
    page_count: int = 0
    truncated: bool = False


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
    output_blob_path: str
