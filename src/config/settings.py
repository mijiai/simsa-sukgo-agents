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

    # Anthropic (재무 분석 Agent)
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")
    financial_samples_blob_prefix: str = Field(
        default="templates/financial_samples/",
        description="재무 분석 샘플 (.docx, .pdf) Blob prefix. startup 1회 로드 후 메모리 캐시",
    )

    # 보고서 작성 Agent
    report_model: str = Field(default="claude-sonnet-4-6")
    report_max_tokens: int = Field(default=8192)
    report_sas_expiry_hours: int = Field(default=168)
    report_samples_blob_prefix: str = Field(default="templates/report_samples/")

    # Gmail
    gmail_credentials_blob_path: str = Field(default="credentials/gmail_oauth.json")
    gmail_sender_address: str = Field(
        default="",
        description="From: 표시. 빈 값이면 OAuth 계정 자체 사용",
    )

    # 모니터링 알림 (Step 4)
    alert_min_risk_level: str = Field(
        default="MEDIUM",
        description="이 등급 이상으로 *상승* 진입 시에만 알림. LOW/MEDIUM/HIGH/CRITICAL",
    )
    alert_dedup_days: int = Field(
        default=90,
        description="같은 (company_id, risk_level) 알림이 N일 내 발송됐으면 중복 차단",
    )
    alert_first_run_send: bool = Field(
        default=True,
        description="첫 실행에서 alert_min_risk_level 이상이면 즉시 발송",
    )

    # 모니터링 스케줄러 (Step 4-2)
    monitoring_batch_cron: str = Field(
        default="0 9 1 */3 *",
        description="UTC 기준 cron 표현식. default: 매 3개월 1일 09:00 UTC",
    )
    monitoring_catchup_threshold_days: int = Field(
        default=90,
        description="컨테이너 재시작 시 마지막 배치가 이만큼 지났으면 즉시 보상 실행",
    )
    monitoring_scheduler_enabled: bool = Field(
        default=True,
        description="False 면 lifespan 에서 scheduler 시작 skip (테스트/긴급 정지 용)",
    )

    # MCP 서버
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8000)

    # 로깅
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
