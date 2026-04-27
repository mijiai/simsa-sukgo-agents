from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.common.constants import RiskLevel


class AnalyzeRequest(BaseModel):
    job_id: str = Field(min_length=1, description="create_analysis_job 가 발급한 job_id")


class AnalysisInputSummary(BaseModel):
    news_count: int = 0
    lawsuit_count: int = 0
    uploaded_file_count: int = 0
    financial_years: list[int] = Field(default_factory=list)


class ClaudeJudgment(BaseModel):
    """Claude LLM 응답에서 직접 파싱하는 구조."""

    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    summary: str
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """analyze/result.json 에 저장되는 전체 구조."""

    job_id: str
    company_name: str
    company_id: str | None = None
    analyzed_at: datetime
    model: str
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    summary: str
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    input_summary: AnalysisInputSummary


class AnalyzeResponse(BaseModel):
    """analyze_financials Tool 의 경량 응답."""

    job_id: str
    status: Literal["analyze_done"] = "analyze_done"
    risk_level: RiskLevel
    risk_score: float
    key_risk_factors: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    output_blob_path: str
