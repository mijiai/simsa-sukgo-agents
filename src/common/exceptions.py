class SimsaSukgoError(Exception):
    pass


class StorageError(SimsaSukgoError):
    pass


class BlobStorageError(StorageError):
    pass


class TableStorageError(StorageError):
    pass


class EntityNotFoundError(TableStorageError):
    def __init__(self, table: str, partition_key: str, row_key: str) -> None:
        super().__init__(
            f"entity not found: table={table} partition_key={partition_key} row_key={row_key}"
        )
        self.table = table
        self.partition_key = partition_key
        self.row_key = row_key
