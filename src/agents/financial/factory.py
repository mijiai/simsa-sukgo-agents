from src.common.anthropic_client import AnthropicClient
from src.config.settings import get_settings

_anthropic_client: AnthropicClient | None = None


def get_anthropic_client() -> AnthropicClient:
    global _anthropic_client
    if _anthropic_client is None:
        settings = get_settings()
        _anthropic_client = AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
        )
    return _anthropic_client


async def close_anthropic_client() -> None:
    global _anthropic_client
    if _anthropic_client is not None:
        await _anthropic_client.close()
        _anthropic_client = None


def reset_anthropic_client_for_tests() -> None:
    global _anthropic_client
    _anthropic_client = None
