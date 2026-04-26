from src.common.exceptions import (
    BlobStorageError,
    EntityNotFoundError,
    SimsaSukgoError,
    StorageError,
    TableStorageError,
)


def test_storage_errors_inherit_from_root() -> None:
    assert issubclass(StorageError, SimsaSukgoError)
    assert issubclass(BlobStorageError, StorageError)
    assert issubclass(TableStorageError, StorageError)
    assert issubclass(EntityNotFoundError, TableStorageError)


def test_entity_not_found_carries_keys() -> None:
    exc = EntityNotFoundError("AnalysisJobs", "job", "abc-123")
    assert exc.table == "AnalysisJobs"
    assert exc.partition_key == "job"
    assert exc.row_key == "abc-123"
    assert "AnalysisJobs" in str(exc)
    assert "abc-123" in str(exc)
