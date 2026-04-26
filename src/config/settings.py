from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Azure Storage
    azure_storage_connection_string: str = Field(default="")
    azure_storage_blob_container: str = Field(default="simsasukgo")

    # Azure SQL / CosmosDB
    azure_db_url: str = Field(default="")

    # Azure AI Search
    azure_search_endpoint: str = Field(default="")
    azure_search_api_key: str = Field(default="")
    azure_search_index_name: str = Field(default="")

    # 외부 API
    naver_client_id: str = Field(default="")
    naver_client_secret: str = Field(default="")
    lawsuit_api_key: str = Field(default="")

    # Gmail
    gmail_credentials_blob_path: str = Field(default="credentials/gmail_oauth.json")

    # MCP 서버
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8000)

    # 로깅
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
