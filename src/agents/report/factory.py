from src.common.anthropic_client import AnthropicClient
from src.config.settings import get_settings

_report_anthropic_client: AnthropicClient | None = None
_narrative_anthropic_client: AnthropicClient | None = None


def get_report_anthropic_client() -> AnthropicClient:
    global _report_anthropic_client
    if _report_anthropic_client is None:
        settings = get_settings()
        _report_anthropic_client = AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.report_model,
            max_tokens=settings.report_max_tokens,
        )
    return _report_anthropic_client


def get_narrative_anthropic_client() -> AnthropicClient:
    global _narrative_anthropic_client
    if _narrative_anthropic_client is None:
        settings = get_settings()
        _narrative_anthropic_client = AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.narrative_model,
            max_tokens=settings.report_max_tokens,
        )
    return _narrative_anthropic_client


async def close_report_anthropic_client() -> None:
    global _report_anthropic_client
    if _report_anthropic_client is not None:
        await _report_anthropic_client.close()
        _report_anthropic_client = None


async def close_narrative_anthropic_client() -> None:
    global _narrative_anthropic_client
    if _narrative_anthropic_client is not None:
        await _narrative_anthropic_client.close()
        _narrative_anthropic_client = None


def reset_report_anthropic_client_for_tests() -> None:
    global _report_anthropic_client, _narrative_anthropic_client
    _report_anthropic_client = None
    _narrative_anthropic_client = None
