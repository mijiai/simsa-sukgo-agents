from datetime import datetime

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    title: str
    description: str
    url: str
    naver_link: str
    published_at: datetime
    is_negative: bool = False
    matched_keywords: list[str] = Field(default_factory=list)
