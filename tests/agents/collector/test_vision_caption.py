"""PR4 Vision caption — extractors.caption_image_with_vision + service 통합."""

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from src.agents.collector.extractors import caption_image_with_vision
from src.agents.collector.internal_db import reset_for_tests as reset_internal_db
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service
from src.common.exceptions import AnthropicApiError


def _make_png(width: int = 100, height: int = 50) -> bytes:
    img = Image.new("RGB", (width, height), color="purple")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_deps() -> tuple[MagicMock, MagicMock, MagicMock]:
    reset_internal_db()
    blob = MagicMock()
    blob.upload = AsyncMock()
    blob.list_prefix = AsyncMock(return_value=[])
    blob.download = AsyncMock(return_value=_make_png())

    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.update_status = AsyncMock()
    tables.companies = MagicMock()
    tables.companies.find_by_name = AsyncMock(return_value=None)
    tables.agent_status = MagicMock()
    tables.agent_status.update_running = AsyncMock()
    tables.agent_status.update_done = AsyncMock()
    tables.agent_status.update_failed = AsyncMock()

    naver = MagicMock()
    naver.search = AsyncMock(return_value=[])

    return blob, tables, naver


# ───────────────────── caption_image_with_vision ─────────────────────


async def test_caption_image_with_vision_returns_text_on_success() -> None:
    blob = MagicMock()
    blob.download = AsyncMock(return_value=_make_png())

    anthropic = MagicMock()
    anthropic.complete_with_image = AsyncMock(return_value="지분 구조도 캡션")

    caption = await caption_image_with_vision(blob, "jobs/J/input/x.png", "x.png", anthropic)
    assert caption == "지분 구조도 캡션"
    anthropic.complete_with_image.assert_awaited_once()
    call_kwargs = anthropic.complete_with_image.call_args.kwargs
    assert call_kwargs["image_filename"] == "x.png"
    assert isinstance(call_kwargs["image_bytes"], bytes)


async def test_caption_image_with_vision_returns_none_on_api_failure() -> None:
    blob = MagicMock()
    blob.download = AsyncMock(return_value=_make_png())

    anthropic = MagicMock()
    anthropic.complete_with_image = AsyncMock(
        side_effect=AnthropicApiError("rate limited", status_code=429)
    )

    caption = await caption_image_with_vision(blob, "jobs/J/input/x.png", "x.png", anthropic)
    assert caption is None  # 실패해도 raise X — 분석은 계속


async def test_caption_image_with_vision_returns_none_on_blob_failure() -> None:
    blob = MagicMock()
    blob.download = AsyncMock(side_effect=RuntimeError("blob fail"))

    anthropic = MagicMock()
    anthropic.complete_with_image = AsyncMock()

    caption = await caption_image_with_vision(blob, "jobs/J/input/x.png", "x.png", anthropic)
    assert caption is None
    anthropic.complete_with_image.assert_not_called()


# ───────────────────── collector service integration ─────────────────────


async def test_service_skips_vision_when_anthropic_none() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(return_value=["jobs/job-1/input/photo.png"])

    request = CollectRequest(job_id="job-1", company_name="ACME")
    response = await collect_company_data_service(
        request, blob, tables, naver, vision_anthropic=None
    )

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert response.extracted_image_count == 1
    assert payload["extracted_images"][0]["caption"] is None


async def test_service_calls_vision_per_image_when_enabled() -> None:
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(
        return_value=[
            "jobs/job-2/input/ownership_chart.png",
            "jobs/job-2/input/product.png",
        ]
    )
    blob.download = AsyncMock(return_value=_make_png())

    anthropic = MagicMock()
    anthropic.complete_with_image = AsyncMock(side_effect=["지분 구조 캡션", "상품 카탈로그 캡션"])

    request = CollectRequest(job_id="job-2", company_name="ACME")
    await collect_company_data_service(
        request,
        blob,
        tables,
        naver,
        vision_anthropic=anthropic,
        vision_model="claude-haiku-4-5-20251001",
    )

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    captions = [img["caption"] for img in payload["extracted_images"]]
    assert "지분 구조 캡션" in captions
    assert "상품 카탈로그 캡션" in captions
    # 모델 인자 전달 확인
    for call in anthropic.complete_with_image.call_args_list:
        assert call.kwargs["model"] == "claude-haiku-4-5-20251001"


async def test_service_vision_failure_does_not_break_collect() -> None:
    """Vision API 가 실패해도 collect 전체는 정상 완료, caption 만 None."""
    blob, tables, naver = _make_deps()
    blob.list_prefix = AsyncMock(return_value=["jobs/job-3/input/x.png"])
    blob.download = AsyncMock(return_value=_make_png())

    anthropic = MagicMock()
    anthropic.complete_with_image = AsyncMock(side_effect=AnthropicApiError("vision down"))

    request = CollectRequest(job_id="job-3", company_name="ACME")
    response = await collect_company_data_service(
        request, blob, tables, naver, vision_anthropic=anthropic
    )

    assert response.status == "collect_done"
    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["extracted_images"][0]["caption"] is None
