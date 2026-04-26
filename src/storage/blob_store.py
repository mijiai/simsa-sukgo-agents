from datetime import UTC, datetime, timedelta

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from src.common.exceptions import BlobStorageError
from src.config.logging import get_logger

logger = get_logger(__name__)


def _parse_connection_string(connection_string: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in connection_string.split(";") if "=" in part)


class BlobStore:
    def __init__(self, connection_string: str, container_name: str) -> None:
        if not connection_string:
            raise BlobStorageError("connection_string is empty")
        if not container_name:
            raise BlobStorageError("container_name is empty")

        self._service_client = BlobServiceClient.from_connection_string(connection_string)
        self._container = container_name

        parts = _parse_connection_string(connection_string)
        self._account_name = parts.get("AccountName", "")
        self._account_key = parts.get("AccountKey", "")
        self._endpoint_suffix = parts.get("EndpointSuffix", "core.windows.net")

    async def __aenter__(self) -> "BlobStore":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._service_client.close()

    async def initialize_container(self) -> None:
        try:
            await self._service_client.create_container(self._container)
            logger.info("blob.container.created", container=self._container)
        except ResourceExistsError:
            logger.debug("blob.container.exists", container=self._container)

    async def upload(
        self,
        blob_path: str,
        data: bytes,
        content_type: str | None = None,
        overwrite: bool = True,
    ) -> None:
        from azure.storage.blob import ContentSettings

        client = self._service_client.get_blob_client(self._container, blob_path)
        try:
            await client.upload_blob(
                data,
                overwrite=overwrite,
                content_settings=ContentSettings(content_type=content_type)
                if content_type
                else None,
            )
        except Exception as exc:
            raise BlobStorageError(f"upload failed: {blob_path}") from exc
        finally:
            await client.close()

    async def download(self, blob_path: str) -> bytes:
        client = self._service_client.get_blob_client(self._container, blob_path)
        try:
            stream = await client.download_blob()
            return await stream.readall()
        except ResourceNotFoundError as exc:
            raise BlobStorageError(f"blob not found: {blob_path}") from exc
        except Exception as exc:
            raise BlobStorageError(f"download failed: {blob_path}") from exc
        finally:
            await client.close()

    async def exists(self, blob_path: str) -> bool:
        client = self._service_client.get_blob_client(self._container, blob_path)
        try:
            return await client.exists()
        finally:
            await client.close()

    async def list_prefix(self, prefix: str) -> list[str]:
        container_client = self._service_client.get_container_client(self._container)
        try:
            return [
                blob.name async for blob in container_client.list_blobs(name_starts_with=prefix)
            ]
        finally:
            await container_client.close()

    async def delete(self, blob_path: str) -> None:
        client = self._service_client.get_blob_client(self._container, blob_path)
        try:
            await client.delete_blob()
        except ResourceNotFoundError:
            pass
        finally:
            await client.close()

    def generate_sas_url(self, blob_path: str, expiry: timedelta) -> str:
        if not self._account_key:
            raise BlobStorageError("AccountKey not available; SAS generation requires a key")

        sas_token = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container,
            blob_name=blob_path,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + expiry,
        )
        return (
            f"https://{self._account_name}.blob.{self._endpoint_suffix}"
            f"/{self._container}/{blob_path}?{sas_token}"
        )
