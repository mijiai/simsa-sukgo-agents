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


async def test_complete_with_image_passes_base64_and_media_type() -> None:
    """PR4 vision API — image bytes 가 base64 로 인코딩되어 messages.create 에 전달."""
    import base64

    text_block = MagicMock()
    text_block.text = "지분 구조도 캡션"
    message = MagicMock()
    message.content = [text_block]

    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)

    client = AnthropicClient(api_key="k", model="claude-haiku-4-5-20251001", client=inner)
    img_bytes = b"\x89PNG\r\n\x1a\nFAKEDATA"
    caption = await client.complete_with_image(
        system="sys",
        user_text="caption please",
        image_bytes=img_bytes,
        image_filename="ownership.png",
    )

    assert caption == "지분 구조도 캡션"
    inner.messages.create.assert_awaited_once()
    call_kwargs = inner.messages.create.call_args.kwargs

    # max_tokens default 256
    assert call_kwargs["max_tokens"] == 256
    # messages 의 content[0] 이 base64 image, content[1] 이 텍스트
    user_message = call_kwargs["messages"][0]
    assert user_message["role"] == "user"
    parts = user_message["content"]
    assert parts[0]["type"] == "image"
    assert parts[0]["source"]["type"] == "base64"
    assert parts[0]["source"]["media_type"] == "image/png"
    assert parts[0]["source"]["data"] == base64.b64encode(img_bytes).decode("ascii")
    assert parts[1]["type"] == "text"
    assert parts[1]["text"] == "caption please"


async def test_complete_with_image_resolves_media_type_from_extension() -> None:
    text_block = MagicMock()
    text_block.text = "ok"
    message = MagicMock()
    message.content = [text_block]
    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)
    client = AnthropicClient(api_key="k", model="claude-haiku-4-5-20251001", client=inner)

    for filename, expected_media in [
        ("a.jpg", "image/jpeg"),
        ("b.JPEG", "image/jpeg"),
        ("c.gif", "image/gif"),
        ("d.webp", "image/webp"),
        ("e.unknown_ext", "image/png"),  # fallback
    ]:
        await client.complete_with_image(
            system="s",
            user_text="t",
            image_bytes=b"data",
            image_filename=filename,
        )
        last_call = inner.messages.create.call_args
        media_type = last_call.kwargs["messages"][0]["content"][0]["source"]["media_type"]
        assert media_type == expected_media, f"{filename} → {media_type}"


async def test_complete_with_image_uses_override_model_when_given() -> None:
    text_block = MagicMock()
    text_block.text = "ok"
    message = MagicMock()
    message.content = [text_block]
    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)
    client = AnthropicClient(api_key="k", model="default-model", client=inner)

    await client.complete_with_image(
        system="s",
        user_text="t",
        image_bytes=b"x",
        image_filename="x.png",
        model="override-model",
    )
    assert inner.messages.create.call_args.kwargs["model"] == "override-model"


async def test_complete_with_image_raises_anthropic_api_error_on_api_failure() -> None:
    """vision API 가 APIError 를 던지면 AnthropicApiError 로 래핑되어 raise."""
    from anthropic import APIError

    inner = MagicMock()
    inner.messages = MagicMock()
    api_err = APIError("rate limited", request=MagicMock(), body=None)
    api_err.status_code = 429  # type: ignore[attr-defined]
    inner.messages.create = AsyncMock(side_effect=api_err)
    client = AnthropicClient(api_key="k", model="m", client=inner)

    with pytest.raises(AnthropicApiError) as excinfo:
        await client.complete_with_image(
            system="s",
            user_text="t",
            image_bytes=b"x",
            image_filename="x.png",
        )
    assert "rate limited" in str(excinfo.value)


async def test_complete_with_image_raises_on_empty_response() -> None:
    """LLM 이 빈 응답 주면 AnthropicApiError."""
    text_block = MagicMock()
    text_block.text = "   "  # whitespace only → strip 후 empty
    message = MagicMock()
    message.content = [text_block]
    inner = MagicMock()
    inner.messages = MagicMock()
    inner.messages.create = AsyncMock(return_value=message)
    client = AnthropicClient(api_key="k", model="m", client=inner)

    with pytest.raises(AnthropicApiError) as excinfo:
        await client.complete_with_image(
            system="s",
            user_text="t",
            image_bytes=b"x",
            image_filename="x.png",
        )
    assert "empty vision response" in str(excinfo.value)


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
