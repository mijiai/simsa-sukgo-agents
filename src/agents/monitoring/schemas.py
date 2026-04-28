from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.common.constants import RiskLevel


class MonitorRegisterRequest(BaseModel):
    company_id: str = Field(min_length=1, description="등록 대상 기업의 Companies.RowKey")
    company_name: str = Field(min_length=1, description="기업명 (UI 표시용)")
    recipient_email: str = Field(min_length=3, description="알림 수신 이메일")
    origin_job_id: str = Field(min_length=1, description="최초 보고서를 생성한 분석 job_id")


class MonitorRegisterResponse(BaseModel):
    company_id: str
    company_name: str
    recipient_email: str
    status: Literal["registered"] = "registered"


class MonitorDeregisterRequest(BaseModel):
    company_id: str = Field(min_length=1)


class MonitorDeregisterResponse(BaseModel):
    company_id: str
    status: Literal["deregistered"] = "deregistered"


class MonitorTargetListItem(BaseModel):
    company_id: str
    company_name: str
    recipient_email: str
    origin_job_id: str
    registered_at: datetime
    last_run_at: datetime | None = None
    last_risk_level: RiskLevel | None = None


class MonitorListResponse(BaseModel):
    count: int
    targets: list[MonitorTargetListItem] = Field(default_factory=list)
