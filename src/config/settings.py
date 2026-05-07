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

    # Azure AI Search
    azure_search_endpoint: str = Field(default="")
    azure_search_api_key: str = Field(default="")
    azure_search_index_name: str = Field(default="")

    # 외부 API
    naver_client_id: str = Field(default="")
    naver_client_secret: str = Field(default="")
    lawsuit_api_key: str = Field(default="")

    # DART (전자공시시스템) OpenAPI
    dart_api_key: str = Field(
        default="",
        description="DART OpenAPI 키. 빈 값이면 DART 연동 skip (graceful). "
        "발급: https://opendart.fss.or.kr/intro/main.do → OpenAPI 신청",
    )
    dart_fetch_years_back: int = Field(
        default=3,
        ge=1,
        le=10,
        description="DART 단일회사 주요계정 조회 연도 수 (현재 연도 기준 역산). "
        "예) 3 이면 2023/2024/2025 3개년 사업보고서 조회 (2026년 기준).",
    )
    dart_reprt_code: str = Field(
        default="11011",
        description="조회할 보고서 종류. "
        "11011: 사업보고서(연간, default), 11012: 반기, 11013: 1분기, 11014: 3분기",
    )

    # Anthropic (재무 분석 Agent)
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")
    anthropic_max_tokens: int = Field(
        default=8192,
        description="financial agent max_tokens. section_insights 추가로 응답 길이 ↑",
    )
    financial_samples_blob_prefix: str = Field(
        default="templates/financial_samples/",
        description="재무 분석 샘플 (.docx, .pdf) Blob prefix. startup 1회 로드 후 메모리 캐시",
    )

    # 보고서 작성 Agent
    report_model: str = Field(default="claude-sonnet-4-6")
    report_max_tokens: int = Field(default=8192)
    report_sas_expiry_hours: int = Field(default=168)
    report_samples_blob_prefix: str = Field(default="templates/report_samples/")
    report_base_docx_blob_path: str = Field(default="")
    appendix_row_threshold: int = Field(default=35, ge=1)

    # 이미지 캡션용 Vision (default off — 비용 0)
    image_vision_enabled: bool = Field(default=False)
    image_vision_model: str = Field(default="claude-haiku-4-5-20251001")

    # Artifact upload
    upload_sas_expiry_minutes: int = Field(default=15, ge=1, le=120)
    upload_blob_prefix: str = Field(default="uploads/")

    # Gmail
    gmail_credentials_blob_path: str = Field(default="credentials/gmail_oauth.json")
    gmail_sender_address: str = Field(default="")

    # 모니터링 알림
    alert_min_risk_level: str = Field(default="MEDIUM")
    alert_dedup_days: int = Field(default=90)
    alert_first_run_send: bool = Field(default=True)

    # 모니터링 스케줄러
    monitoring_batch_cron: str = Field(default="0 9 1 */3 *")
    monitoring_catchup_threshold_days: int = Field(default=90)
    monitoring_scheduler_enabled: bool = Field(default=True)

    # MCP 서버
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8000)

    # 로깅
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()