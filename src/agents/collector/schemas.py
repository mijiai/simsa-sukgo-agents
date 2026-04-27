from datetime import datetime
from typing import Literal

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


class CollectResponse(BaseModel):
    job_id: str
    status: Literal["collect_done"] = "collect_done"
    company_name: str
    company_id: str | None = None
    news_count: int
    lawsuit_count: int = 0
    financial_years: list[int] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list)
    output_blob_path: str
