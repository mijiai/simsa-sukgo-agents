import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.monitoring.gmail_client import GMAIL_SEND_SCOPE, GmailClient
from src.common.exceptions import GmailApiError


def _credentials_json() -> dict:
    return {
        "token": "old-token",
        "refresh_token": "rt-xxx",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csecret",
        "scopes": [GMAIL_SEND_SCOPE],
    }


def _make_blob(creds: dict | None = None, *, raise_download: bool = False) -> MagicMock:
    blob = MagicMock()
    if raise_download:
        blob.download = AsyncMock(side_effect=RuntimeError("blob boom"))
    else:
        blob.download = AsyncMock(
            return_value=json.dumps(creds or _credentials_json()).encode("utf-8")
        )
    return blob


def test_build_message_returns_base64url_raw() -> None:
    body = GmailClient._build_message(
        sender="sender@example.com",
        recipient="r@example.com",
        subject="hello",
        html_body="<b>hi</b>",
        text_body="hi",
    )
    assert "raw" in body
    # urlsafe_b64 alphabet only
    assert all(c.isalnum() or c in "-_=" for c in body["raw"])


async def test_send_html_loads_creds_then_sends() -> None:
    blob = _make_blob()
    client = GmailClient(blob, "credentials/gmail_oauth.json", sender_address="me@x.com")

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.refresh_token = "rt-xxx"
    fake_service = MagicMock()
    fake_send = MagicMock()
    fake_send.execute = MagicMock(return_value={"id": "msg-123"})
    fake_service.users.return_value.messages.return_value.send.return_value = fake_send

    with (
        patch(
            "src.agents.monitoring.gmail_client.Credentials.from_authorized_user_info",
            return_value=fake_creds,
        ),
        patch(
            "src.agents.monitoring.gmail_client.build",
            return_value=fake_service,
        ),
    ):
        msg_id = await client.send_html(
            recipient="r@example.com",
            subject="hi",
            html_body="<b>hi</b>",
        )

    assert msg_id == "msg-123"
    blob.download.assert_awaited_once_with("credentials/gmail_oauth.json")
    fake_service.users.return_value.messages.return_value.send.assert_called_once()
    send_kwargs = fake_service.users.return_value.messages.return_value.send.call_args.kwargs
    assert send_kwargs["userId"] == "me"
    assert "raw" in send_kwargs["body"]


async def test_send_html_refreshes_expired_creds() -> None:
    blob = _make_blob()
    client = GmailClient(blob, "credentials/gmail_oauth.json")

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.refresh_token = "rt-xxx"
    fake_creds.refresh = MagicMock()
    fake_service = MagicMock()
    fake_send = MagicMock()
    fake_send.execute = MagicMock(return_value={"id": "msg-1"})
    fake_service.users.return_value.messages.return_value.send.return_value = fake_send

    with (
        patch(
            "src.agents.monitoring.gmail_client.Credentials.from_authorized_user_info",
            return_value=fake_creds,
        ),
        patch("src.agents.monitoring.gmail_client.build", return_value=fake_service),
    ):
        await client.send_html(recipient="r@x.com", subject="s", html_body="b")

    fake_creds.refresh.assert_called_once()


async def test_send_html_raises_when_no_refresh_token() -> None:
    blob = _make_blob({"token": "x", "client_id": "c", "client_secret": "s"})
    client = GmailClient(blob, "credentials/gmail_oauth.json")

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.refresh_token = None

    with patch(
        "src.agents.monitoring.gmail_client.Credentials.from_authorized_user_info",
        return_value=fake_creds,
    ):
        with pytest.raises(GmailApiError):
            await client.send_html(recipient="r@x.com", subject="s", html_body="b")


async def test_send_html_raises_on_blob_download_failure() -> None:
    blob = _make_blob(raise_download=True)
    client = GmailClient(blob, "credentials/gmail_oauth.json")
    with pytest.raises(GmailApiError):
        await client.send_html(recipient="r@x.com", subject="s", html_body="b")


async def test_send_html_raises_on_invalid_credentials_json() -> None:
    blob = MagicMock()
    blob.download = AsyncMock(return_value=b"not json")
    client = GmailClient(blob, "credentials/gmail_oauth.json")
    with pytest.raises(GmailApiError):
        await client.send_html(recipient="r@x.com", subject="s", html_body="b")


async def test_send_html_caches_creds_after_first_call() -> None:
    blob = _make_blob()
    client = GmailClient(blob, "credentials/gmail_oauth.json")

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.refresh_token = "rt"
    fake_service = MagicMock()
    fake_send = MagicMock()
    fake_send.execute = MagicMock(return_value={"id": "msg-1"})
    fake_service.users.return_value.messages.return_value.send.return_value = fake_send

    with (
        patch(
            "src.agents.monitoring.gmail_client.Credentials.from_authorized_user_info",
            return_value=fake_creds,
        ),
        patch("src.agents.monitoring.gmail_client.build", return_value=fake_service),
    ):
        await client.send_html(recipient="a@x.com", subject="s", html_body="b")
        await client.send_html(recipient="b@x.com", subject="s", html_body="b")

    # Only one blob download (creds cached)
    blob.download.assert_awaited_once()
