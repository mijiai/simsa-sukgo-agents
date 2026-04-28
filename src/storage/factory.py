from src.config.settings import get_settings
from src.storage.blob_store import BlobStore
from src.storage.table_store import TableStore

_blob_store: BlobStore | None = None
_table_store: TableStore | None = None


def get_blob_store() -> BlobStore:
    global _blob_store
    if _blob_store is None:
        settings = get_settings()
        _blob_store = BlobStore(
            settings.azure_storage_connection_string,
            settings.azure_storage_blob_container,
        )
    return _blob_store


def get_table_store() -> TableStore:
    global _table_store
    if _table_store is None:
        settings = get_settings()
        _table_store = TableStore(settings.azure_storage_connection_string)
    return _table_store


async def close_storage() -> None:
    global _blob_store, _table_store
    if _blob_store is not None:
        await _blob_store.close()
        _blob_store = None
    if _table_store is not None:
        await _table_store.close()
        _table_store = None


def reset_storage_for_tests() -> None:
    global _blob_store, _table_store
    _blob_store = None
    _table_store = None
