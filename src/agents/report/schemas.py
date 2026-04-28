from typing import Literal

from pydantic import BaseModel, Field

from src.common.constants import RiskLevel


class ReportRequest(BaseModel):
    job_id: str = Field(min_length=1, description="create_analysis_job 가 발급한 job_id")


class ReportResponse(BaseModel):
    job_id: str
    status: Literal["done"] = "done"
    risk_level: RiskLevel
    risk_score: float
    report_url: str = Field(description="report.md 의 SAS URL (만료 있음)")
    report_blob_path: str
