from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.common.constants import ReportSection, RiskLevel


class AnalyzeRequest(BaseModel):
    job_id: str = Field(min_length=1, description="create_analysis_job 가 발급한 job_id")


class SectionInsight(BaseModel):
    """보고서 섹션 단위 분석 인사이트.

    report agent 가 해당 섹션의 표 아래 bullet 으로 그대로 사용. financial 이 작성하고
    report 는 렌더만 한다 (Option B 책임 분리).
    """

    section_id: ReportSection
    table_id: str | None = Field(
        default=None,
        description="같은 섹션 내 여러 표 구분용 (예: '재무제표', '자산건전성')",
    )
    bullets: list[str] = Field(min_length=1, max_length=5)
    cited_data_points: list[str] = Field(
        default_factory=list,
        description="bullet 의 근거가 된 수치/사실 (감사 추적용)",
    )


class AnalysisInputSummary(BaseModel):
    news_count: int = 0
    lawsuit_count: int = 0
    uploaded_file_count: int = 0
    financial_years: list[int] = Field(default_factory=list)
    extracted_table_count: int = 0
    extracted_image_count: int = 0


class ClaudeJudgment(BaseModel):
    """Claude LLM 응답에서 직접 파싱하는 구조."""

    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    risk_score_rationale: str = Field(
        default="",
        description="점수 산정 근거 (60점 초과 시 필수)",
    )
    summary: str
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(
        default_factory=list,
        description="key_risk_factors 각 항목에 대한 대안적 해석 또는 완화 요인",
    )
    data_gaps: list[str] = Field(default_factory=list)
    section_insights: list[SectionInsight] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """analyze/result.json 에 저장되는 전체 구조."""

    job_id: str
    company_name: str
    company_id: str | None = None
    analyzed_at: datetime
    model: str
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    risk_score_rationale: str = Field(default="")
    summary: str
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    section_insights: list[SectionInsight] = Field(default_factory=list)
    input_summary: AnalysisInputSummary


class AnalyzeResponse(BaseModel):
    """analyze_financials Tool 의 경량 응답."""

    job_id: str
    status: Literal["analyze_done"] = "analyze_done"
    risk_level: RiskLevel
    risk_score: float
    key_risk_factors: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    section_insights_count: int = 0
    output_blob_path: str
