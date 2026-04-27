import asyncio
import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx

from src.agents.collector.filters import find_negative_keywords
from src.agents.collector.schemas import NewsArticle
from src.common.exceptions import NaverApiError
from src.config.logging import get_logger

logger = get_logger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", text))


def _parse_pub_date(s: str) -> datetime:
    return parsedate_to_datetime(s)


class NaverNewsClient:
    BASE_URL = "https://openapi.naver.com/v1/search/news.json"
    MAX_DISPLAY = 100
    MAX_START = 1000

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        if not client_id:
            raise NaverApiError("client_id is empty")
        if not client_secret:
            raise NaverApiError("client_secret is empty")

        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        self._owns_http = http is None
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base

    async def __aenter__(self) -> "NaverNewsClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search(
        self,
        query: str,
        *,
        sort: Literal["sim", "date"] = "date",
        max_results: int = 100,
    ) -> list[NewsArticle]:
        if not query:
            raise NaverApiError("query is empty")
        if max_results <= 0:
            return []

        articles: list[NewsArticle] = []
        start = 1
        while len(articles) < max_results and start <= self.MAX_START:
            remaining = max_results - len(articles)
            display = min(self.MAX_DISPLAY, remaining)
            payload = await self._search_page(query, display=display, start=start, sort=sort)
            items = payload.get("items", [])
            if not items:
                break
            articles.extend(self._parse_article(item) for item in items)
            if len(items) < display:
                break
            start += len(items)

        logger.info("naver.search.done", query=query, count=len(articles))
        return articles[:max_results]

    async def _search_page(
        self,
        query: str,
        *,
        display: int,
        start: int,
        sort: str,
    ) -> dict[str, Any]:
        params = {"query": query, "display": display, "start": start, "sort": sort}
        headers = {
            "X-Naver-Client-Id": self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }

        backoff = self._backoff_base
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.get(self.BASE_URL, params=params, headers=headers)
            except httpx.RequestError as exc:
                logger.warning("naver.request.error", attempt=attempt, error=type(exc).__name__)
                if attempt < self._max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise NaverApiError(f"request failed after {attempt} attempts") from exc

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                logger.warning(
                    "naver.retryable.status",
                    attempt=attempt,
                    status=response.status_code,
                )
                if attempt < self._max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise NaverApiError(
                    f"status {response.status_code} not resolved after {attempt} attempts",
                    status_code=response.status_code,
                )

            raise NaverApiError(
                f"client error {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        raise NaverApiError("retry loop exited unexpectedly")

    def _parse_article(self, item: dict[str, Any]) -> NewsArticle:
        title = _strip_html(item.get("title", ""))
        description = _strip_html(item.get("description", ""))
        keywords = find_negative_keywords(f"{title}\n{description}")
        return NewsArticle(
            title=title,
            description=description,
            url=item.get("originallink") or item.get("link", ""),
            naver_link=item.get("link", ""),
            published_at=_parse_pub_date(item["pubDate"]),
            is_negative=bool(keywords),
            matched_keywords=keywords,
        )
