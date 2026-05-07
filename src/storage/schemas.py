from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from src.common.constants import RiskLevel


class JobStatus(StrEnum):
    PENDING = "pending"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    DONE = "done"
    FAILED = "failed"


class AgentName(StrEnum):
    COLLECT = "collect"
    ANALYZE = "analyze"
    REPORT = "report"


class AgentStatusValue(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class AnalysisJob(BaseModel):
    job_id: str
    company_name: str
    company_id: str | None = None
    user_id: str | None = None
    status: JobStatus = JobStatus.PENDING
    current_agent: AgentName | None = None
    custom_prompt: str | None = None
    input_blob_prefix: str | None = None
    report_blob_path: str | None = None
    risk_level: RiskLevel | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class AgentStatus(BaseModel):
    job_id: str
    agent_name: AgentName
    status: AgentStatusValue = AgentStatusValue.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_sec: int | None = None
    output_blob_path: str | None = None
    retry_count: int = 0
    error_detail: str | None = None


class Company(BaseModel):
    company_id: str
    company_name: str
    business_no: str | None = None
    corp_no: str | None = None
    # DART 고유번호 (8자리 zero-padded). company.json 검색 결과를 캐시해 두어
    # 동일 기업 재분석 시 API 호출 1회 절약.
    dart_corp_code: str | None = None
    industry_code: str | None = None
    industry_name: str | None = None
    created_at: datetime
    updated_at: datetime


class FinancialRaw(BaseModel):
    job_id: str
    company_id: str
    fiscal_year: int = Field(ge=1900, le=2999)
    fiscal_type: Literal["annual", "quarter"]
    revenue: int | None = None
    operating_profit: int | None = None
    net_income: int | None = None
    total_assets: int | None = None
    total_liabilities: int | None = None
    total_equity: int | None = None
    current_assets: int | None = None
    current_liabilities: int | None = None
    operating_cf: int | None = None
    data_source: str | None = None
    created_at: datetime


class FinancialMetrics(BaseModel):
    job_id: str
    company_id: str
    base_year: int = Field(ge=1900, le=2999)
    debt_ratio: float | None = None
    current_ratio: float | None = None
    interest_coverage: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    roa: float | None = None
    roe: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    created_at: datetime


class AnalysisJobRef(BaseModel):
    job_id: str
    company_id: str
    company_name: str
    status: JobStatus
    risk_level: RiskLevel | None = None
    created_at: datetime
    finished_at: datetime | None = None


class MonitoringTarget(BaseModel):
    company_id: str
    company_name: str
    recipient_email: str
    origin_job_id: str
    registered_at: datetime
    is_active: bool = True
    last_run_at: datetime | None = None
    last_risk_level: RiskLevel | None = None


class MonitoringSnapshot(BaseModel):
    company_id: str
    run_date: date
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    analysis_job_id: str
    news_count: int = 0
    lawsuit_count: int = 0
    summary: str = ""
    key_signals: str = ""
    snapshot_blob_path: str | None = None


class AlertStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"


class AlertHistory(BaseModel):
    company_id: str
    sent_at: datetime
    risk_level: RiskLevel
    sent_to: str
    status: AlertStatus
    gmail_message_id: str | None = None


class SchedulerState(BaseModel):
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int = 0