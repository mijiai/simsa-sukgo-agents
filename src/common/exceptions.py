class SimsaSukgoError(Exception):
    pass


class ExternalApiError(SimsaSukgoError):
    def __init__(self, source: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.status_code = status_code


class NaverApiError(ExternalApiError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__("naver_news", message, status_code=status_code)


class AnthropicApiError(ExternalApiError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__("anthropic", message, status_code=status_code)


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
