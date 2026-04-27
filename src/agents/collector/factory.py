from src.agents.collector.clients import NaverNewsClient
from src.config.settings import get_settings

_naver_news_client: NaverNewsClient | None = None


def get_naver_news_client() -> NaverNewsClient:
    global _naver_news_client
    if _naver_news_client is None:
        settings = get_settings()
        _naver_news_client = NaverNewsClient(
            settings.naver_client_id,
            settings.naver_client_secret,
        )
    return _naver_news_client


async def close_collector_clients() -> None:
    global _naver_news_client
    if _naver_news_client is not None:
        await _naver_news_client.close()
        _naver_news_client = None


def reset_collector_clients_for_tests() -> None:
    global _naver_news_client
    _naver_news_client = None
