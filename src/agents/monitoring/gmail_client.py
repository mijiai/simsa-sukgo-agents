import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.common.exceptions import GmailApiError
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore

logger = get_logger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailClient:
    """Lightweight Gmail send-only client.

    Loads OAuth credentials JSON (client_id/secret + refresh_token) from
    Azure Blob on first send. google-auth handles token refresh transparently.
    """

    def __init__(
        self,
        blob: BlobStore,
        credentials_blob_path: str,
        *,
        sender_address: str = "",
    ) -> None:
        self._blob = blob
        self._credentials_blob_path = credentials_blob_path
        self._sender_address = sender_address
        self._creds: Credentials | None = None
        self._service: Any = None

    async def _ensure_creds(self) -> Credentials:
        if self._creds is not None:
            return self._creds
        try:
            data = await self._blob.download(self._credentials_blob_path)
        except Exception as exc:
            raise GmailApiError(
                f"failed to load gmail credentials from blob {self._credentials_blob_path}: {exc}"
            ) from exc

        try:
            info = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GmailApiError(f"gmail credentials JSON parse failed: {exc}") from exc

        try:
            creds = Credentials.from_authorized_user_info(info, scopes=[GMAIL_SEND_SCOPE])
        except Exception as exc:
            raise GmailApiError(f"gmail credentials invalid: {exc}") from exc

        if not creds.valid:
            if not creds.refresh_token:
                raise GmailApiError("gmail credentials have no refresh_token")
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise GmailApiError(f"gmail token refresh failed: {exc}") from exc

        self._creds = creds
        return creds

    def _build_service(self, creds: Credentials) -> Any:
        if self._service is None:
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    @staticmethod
    def _build_message(
        *,
        sender: str,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> dict[str, str]:
        message = MIMEMultipart("alternative")
        message["To"] = recipient
        if sender:
            message["From"] = sender
        message["Subject"] = subject
        if text_body:
            message.attach(MIMEText(text_body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))
        raw_bytes = message.as_bytes()
        return {"raw": base64.urlsafe_b64encode(raw_bytes).decode("ascii")}

    async def send_html(
        self,
        *,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> str:
        """Send an email. Returns Gmail message_id on success."""
        creds = await self._ensure_creds()
        service = self._build_service(creds)
        body = self._build_message(
            sender=self._sender_address,
            recipient=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        try:
            sent = service.users().messages().send(userId="me", body=body).execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            raise GmailApiError(str(exc), status_code=status) from exc

        message_id = sent.get("id", "")
        logger.info(
            "gmail.send.done",
            recipient=recipient,
            subject_chars=len(subject),
            message_id=message_id,
        )
        return message_id
