from src.agents.collector.clients import NaverNewsClient
from src.agents.collector.dart_client import DartClient, DartApiError
from src.config.logging import get_logger
from src.config.settings import get_settings

logger = get_logger(__name__)

_naver_news_client: NaverNewsClient | None = None
_dart_client: DartClient | None = None


def get_naver_news_client() -> NaverNewsClient:
    global _naver_news_client
    if _naver_news_client is None:
        settings = get_settings()
        _naver_news_client = NaverNewsClient(
            settings.naver_client_id,
            settings.naver_client_secret,
        )
    return _naver_news_client


def get_dart_client() -> DartClient | None:
    """DART_API_KEY 가 설정돼 있을 때만 DartClient 반환, 없으면 None.

    None 을 반환하면 collector service 가 DART 단계를 graceful skip 한다.
    """
    global _dart_client
    if _dart_client is None:
        settings = get_settings()
        if not settings.dart_api_key:
            logger.info("dart.client.skipped_no_api_key")
            return None
        try:
            _dart_client = DartClient(api_key=settings.dart_api_key)
            logger.info("dart.client.initialized")
        except DartApiError as exc:
            logger.warning("dart.client.init_failed", error=str(exc))
            return None
    return _dart_client


async def close_collector_clients() -> None:
    global _naver_news_client, _dart_client
    if _naver_news_client is not None:
        await _naver_news_client.close()
        _naver_news_client = None
    if _dart_client is not None:
        await _dart_client.close()
        _dart_client = None


def reset_collector_clients_for_tests() -> None:
    global _naver_news_client, _dart_client
    _naver_news_client = None
    _dart_client = None