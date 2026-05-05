from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.exceptions import BlobStorageError
from src.storage.blob_store import BlobStore

CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=testacc;"
    "AccountKey=dGVzdGtleQ==;"
    "EndpointSuffix=core.windows.net"
)


def _make_store() -> tuple[BlobStore, MagicMock]:
    with patch("src.storage.blob_store.BlobServiceClient") as bsc_cls:
        service = MagicMock()
        service.close = AsyncMock()
        service.create_container = AsyncMock()
        bsc_cls.from_connection_string.return_value = service
        store = BlobStore(CONNECTION_STRING, "simsasukgo")
    return store, service


def test_constructor_rejects_empty_connection_string() -> None:
    with pytest.raises(BlobStorageError):
        BlobStore("", "simsasukgo")


def test_constructor_rejects_empty_container() -> None:
    with pytest.raises(BlobStorageError):
        BlobStore(CONNECTION_STRING, "")


def test_constructor_parses_account_credentials() -> None:
    store, _ = _make_store()
    assert store._account_name == "testacc"
    assert store._account_key == "dGVzdGtleQ=="
    assert store._endpoint_suffix == "core.windows.net"


async def test_upload_calls_upload_blob() -> None:
    store, service = _make_store()
    blob_client = MagicMock()
    blob_client.upload_blob = AsyncMock()
    blob_client.close = AsyncMock()
    service.get_blob_client.return_value = blob_client

    await store.upload("jobs/abc/input/file.txt", b"hello")

    blob_client.upload_blob.assert_awaited_once()
    args, kwargs = blob_client.upload_blob.call_args
    assert args[0] == b"hello"
    assert kwargs["overwrite"] is True
    blob_client.close.assert_awaited()


async def test_upload_wraps_exceptions() -> None:
    store, service = _make_store()
    blob_client = MagicMock()
    blob_client.upload_blob = AsyncMock(side_effect=RuntimeError("boom"))
    blob_client.close = AsyncMock()
    service.get_blob_client.return_value = blob_client

    with pytest.raises(BlobStorageError, match="upload failed"):
        await store.upload("path", b"x")


async def test_download_returns_bytes() -> None:
    store, service = _make_store()
    stream = AsyncMock()
    stream.readall = AsyncMock(return_value=b"payload")
    blob_client = MagicMock()
    blob_client.download_blob = AsyncMock(return_value=stream)
    blob_client.close = AsyncMock()
    service.get_blob_client.return_value = blob_client

    data = await store.download("path")

    assert data == b"payload"


async def test_initialize_container_swallows_resource_exists() -> None:
    from azure.core.exceptions import ResourceExistsError

    store, service = _make_store()
    service.create_container = AsyncMock(side_effect=ResourceExistsError("exists"))

    await store.initialize_container()  # should not raise


def test_generate_sas_url_format() -> None:
    store, _ = _make_store()
    url = store.generate_sas_url("jobs/abc/report/report.md", timedelta(hours=1))
    assert url.startswith(
        "https://testacc.blob.core.windows.net/simsasukgo/jobs/abc/report/report.md?"
    )
    assert "sig=" in url
    assert "se=" in url


def test_generate_sas_url_requires_account_key() -> None:
    no_key_conn = (
        "DefaultEndpointsProtocol=https;AccountName=testacc;EndpointSuffix=core.windows.net"
    )
    with patch("src.storage.blob_store.BlobServiceClient") as bsc_cls:
        bsc_cls.from_connection_string.return_value = MagicMock()
        store = BlobStore(no_key_conn, "simsasukgo")

    with pytest.raises(BlobStorageError, match="AccountKey"):
        store.generate_sas_url("path", timedelta(minutes=5))


def test_generate_upload_sas_url_returns_url_and_expiry() -> None:
    store, _ = _make_store()
    url, expires_at = store.generate_upload_sas_url(
        "uploads/abc/재무.xls", timedelta(minutes=15)
    )
    assert url.startswith(
        "https://testacc.blob.core.windows.net/simsasukgo/uploads/abc/재무.xls?"
    )
    assert "sig=" in url
    assert "se=" in url
    # write+create permissions present (Azure SAS encodes as sp=cw or sp=wc)
    assert "sp=" in url
    assert any(p in url for p in ("sp=cw", "sp=wc"))
    # expires_at within 16 minutes of now (small slack)
    delta = expires_at - datetime.now(UTC)
    assert timedelta(minutes=14) < delta <= timedelta(minutes=15, seconds=2)


def test_generate_upload_sas_url_constrains_content_type_when_given() -> None:
    store, _ = _make_store()
    url, _ = store.generate_upload_sas_url(
        "uploads/abc/x.xlsx",
        timedelta(minutes=15),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # rsct (response/required content-type) is encoded in the SAS query
    assert "rsct=" in url


def test_generate_upload_sas_url_requires_account_key() -> None:
    no_key_conn = (
        "DefaultEndpointsProtocol=https;AccountName=testacc;EndpointSuffix=core.windows.net"
    )
    with patch("src.storage.blob_store.BlobServiceClient") as bsc_cls:
        bsc_cls.from_connection_string.return_value = MagicMock()
        store = BlobStore(no_key_conn, "simsasukgo")

    with pytest.raises(BlobStorageError, match="AccountKey"):
        store.generate_upload_sas_url("uploads/x", timedelta(minutes=5))
