from datetime import datetime

from pydantic import BaseModel


class NewsArticle(BaseModel):
    title: str
    description: str
    url: str
    naver_link: str
    published_at: datetime
