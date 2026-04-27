import json
import re
from typing import Any

from anthropic import APIError, AsyncAnthropic

from src.common.exceptions import AnthropicApiError
from src.config.logging import get_logger

logger = get_logger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 2048,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            raise AnthropicApiError("api_key is empty")
        if not model:
            raise AnthropicApiError("model is empty")

        self._client = client or AsyncAnthropic(api_key=api_key)
        self._owns_client = client is None
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            raise AnthropicApiError(str(exc), status_code=status) from exc

        text = "".join(
            getattr(block, "text", "") for block in message.content if getattr(block, "text", None)
        ).strip()

        if not text:
            raise AnthropicApiError("empty response from Claude")

        return _parse_json_loose(text)


def _parse_json_loose(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(cleaned)
        if not match:
            raise AnthropicApiError(f"non-JSON response: {text[:200]}") from None
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise AnthropicApiError(f"JSON parse failed: {text[:200]}") from exc

    if not isinstance(parsed, dict):
        raise AnthropicApiError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed
