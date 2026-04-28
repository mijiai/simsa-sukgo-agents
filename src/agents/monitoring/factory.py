from src.agents.monitoring.gmail_client import GmailClient
from src.config.settings import get_settings
from src.storage.factory import get_blob_store

_gmail_client: GmailClient | None = None


def get_gmail_client() -> GmailClient:
    global _gmail_client
    if _gmail_client is None:
        settings = get_settings()
        _gmail_client = GmailClient(
            blob=get_blob_store(),
            credentials_blob_path=settings.gmail_credentials_blob_path,
            sender_address=settings.gmail_sender_address,
        )
    return _gmail_client


def reset_gmail_client_for_tests() -> None:
    global _gmail_client
    _gmail_client = None
