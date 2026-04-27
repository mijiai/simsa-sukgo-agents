from unittest.mock import AsyncMock, MagicMock

import pytest

from src.common.anthropic_client import AnthropicClient, _parse_json_loose
from src.common.exceptions import AnthropicApiError


def test_parse_json_loose_bare_object() -> None:
    assert _parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_json_loose_strips_code_fence() -> None:
    text = '```json\n{"risk_level": "LOW"}\n```'
    assert _parse_json_loose(text) == {"risk_level": "LOW"}


def test_parse_json_loose_strips_plain_fence() -> None:
    text = '```\n{"x": 2}\n```'
    assert _parse_json_loose(text) == {"x": 2}


def test_parse_json_loose_extracts_from_surrounding_text() -> None:
    text = '여기 결과입니다:\n{"risk_score": 42.5}\n끝.'
    assert _parse_json_loose(text) == {"risk_score": 42.5}


def test_parse_json_loose_raises_on_non_json() -> None:
    with pytest.raises(AnthropicApiError):
        _parse_json_loose("hello world, no JSON here")


def test_parse_json_loose_raises_on_array_top_level() -> None:
    with pytest.raises(AnthropicApiError):
        _parse_json_loose("[1, 2, 3]")


def test_anthropic_client_init_validates_api_key() -> None:
    with pytest.raises(AnthropicApiError):
        AnthropicClient(api_key="", model="m")


def test_anthropic_client_init_validates_model() -> None:
    with pytest.raises(AnthropicApiError):
        AnthropicClient(api_key="k", model="")


async def test_complete_json_returns_parsed_dict() -> None:
    text_block = MagicMock()
    text_block.text = '{"risk_level": "LOW", "risk_score": 10.0, "summary": "ok"}'

    message = MagicMock()
    message.content = [text_block]

    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)

    client = AnthropicClient(api_key="k", model="claude-haiku-4-5-20251001", client=inner)
    result = await client.complete_json(system="sys", user="usr")

    assert result == {"risk_level": "LOW", "risk_score": 10.0, "summary": "ok"}
    inner.messages.create.assert_awaited_once()
    create_kwargs = inner.messages.create.call_args.kwargs
    assert create_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert create_kwargs["system"] == "sys"
    assert create_kwargs["messages"] == [{"role": "user", "content": "usr"}]


async def test_complete_json_raises_on_empty_response() -> None:
    text_block = MagicMock()
    text_block.text = ""
    message = MagicMock()
    message.content = [text_block]

    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)

    client = AnthropicClient(api_key="k", model="m", client=inner)
    with pytest.raises(AnthropicApiError):
        await client.complete_json(system="sys", user="usr")
