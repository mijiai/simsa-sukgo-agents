from collections.abc import Callable

import httpx
import pytest

from src.agents.collector.clients import NaverNewsClient, _parse_pub_date, _strip_html
from src.common.exceptions import NaverApiError


def _sample_item(
    title: str = "ACME, 신규 사업 진출",
    description: str = "ACME가 신사업에 진출했다",
    pub_date: str = "Sat, 22 Sep 2024 12:00:00 +0900",
    originallink: str = "https://news.example.com/1",
    link: str = "https://n.news.naver.com/article/1",
) -> dict:
    return {
        "title": title,
        "description": description,
        "originallink": originallink,
        "link": link,
        "pubDate": pub_date,
    }


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> NaverNewsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return NaverNewsClient(
        client_id="cid",
        client_secret="csecret",
        http=http,
        max_attempts=3,
        backoff_base=0,
    )


def test_strip_html_removes_b_tag_and_decodes_entities() -> None:
    assert _strip_html("<b>키워드</b> 관련 &amp; 기사") == "키워드 관련 & 기사"
    assert _strip_html("&quot;테스트&quot;") == '"테스트"'


def test_parse_pub_date_returns_aware_datetime() -> None:
    dt = _parse_pub_date("Sat, 22 Sep 2024 12:00:00 +0900")
    assert dt.year == 2024
    assert dt.tzinfo is not None


def test_constructor_rejects_empty_credentials() -> None:
    with pytest.raises(NaverApiError):
        NaverNewsClient("", "secret")
    with pytest.raises(NaverApiError):
        NaverNewsClient("id", "")


async def test_search_parses_items_and_strips_html() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        assert request.url.host == "openapi.naver.com"
        assert "query=ACME" in str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    _sample_item(title="<b>ACME</b>, 신규 사업"),
                    _sample_item(title="ACME, 신제품 출시", description="<b>신제품</b> 라인업"),
                ]
            },
        )

    client = _make_client(handler)
    try:
        results = await client.search("ACME", max_results=10)
    finally:
        await client.close()

    assert seen_headers["x-naver-client-id"] == "cid"
    assert seen_headers["x-naver-client-secret"] == "csecret"
    assert len(results) == 2
    assert results[0].title == "ACME, 신규 사업"
    assert results[1].title == "ACME, 신제품 출시"
    assert results[1].description == "신제품 라인업"


async def test_search_paginates_until_max_results() -> None:
    page_calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start"))
        display = int(request.url.params.get("display"))
        page_calls.append(start)
        items = [_sample_item(title=f"Article {start + i}") for i in range(display)]
        return httpx.Response(200, json={"items": items})

    client = _make_client(handler)
    try:
        results = await client.search("Q", max_results=150)
    finally:
        await client.close()

    assert len(results) == 150
    assert page_calls == [1, 101]


async def test_search_stops_when_items_fewer_than_display() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_sample_item()]})

    client = _make_client(handler)
    try:
        results = await client.search("Q", max_results=50)
    finally:
        await client.close()
    assert len(results) == 1


async def test_search_empty_response_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    client = _make_client(handler)
    try:
        results = await client.search("Q", max_results=10)
    finally:
        await client.close()
    assert results == []


async def test_search_rejects_empty_query() -> None:
    client = _make_client(lambda r: httpx.Response(200, json={"items": []}))
    try:
        with pytest.raises(NaverApiError, match="query is empty"):
            await client.search("")
    finally:
        await client.close()


async def test_429_retries_then_succeeds() -> None:
    call_count = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"items": [_sample_item()]})

    client = _make_client(handler)
    try:
        results = await client.search("Q", max_results=1)
    finally:
        await client.close()

    assert call_count["n"] == 3
    assert len(results) == 1


async def test_500_retries_then_exhausts() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = _make_client(handler)
    try:
        with pytest.raises(NaverApiError, match="status 500") as ei:
            await client.search("Q")
    finally:
        await client.close()
    assert ei.value.status_code == 500


async def test_401_does_not_retry() -> None:
    call_count = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(401, text="auth failed")

    client = _make_client(handler)
    try:
        with pytest.raises(NaverApiError, match="client error 401") as ei:
            await client.search("Q")
    finally:
        await client.close()
    assert ei.value.status_code == 401
    assert call_count["n"] == 1


async def test_request_error_retries_then_exhausts() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = _make_client(handler)
    try:
        with pytest.raises(NaverApiError, match="request failed"):
            await client.search("Q")
    finally:
        await client.close()


async def test_close_does_not_close_external_http_client() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []}))
    external_http = httpx.AsyncClient(transport=transport)
    client = NaverNewsClient("id", "secret", http=external_http, backoff_base=0)
    await client.close()
    # external client still usable
    assert not external_http.is_closed
    await external_http.aclose()
