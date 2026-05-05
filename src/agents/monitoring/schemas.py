from datetime import date, datetime
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


class MonitorRunNowRequest(BaseModel):
    company_id: str = Field(min_length=1, description="MonitoringTargets 에 등록된 company_id")


class MonitorGetLatestSnapshotRequest(BaseModel):
    company_id: str = Field(min_length=1, description="MonitoringTargets 에 등록된 company_id")


class MonitorGetLatestSnapshotResponse(BaseModel):
    """모니터링 상세 페이지 mount 시 1회 호출용 — 가장 최근 snapshot.json 풀 반환.

    available=False: target 은 있는데 한 번도 monitor_run_now 가 돌지 않은 경우.
    이때 risk_*, summary, evidence list 들은 모두 None / [] 로 비워둔다.
    """

    company_id: str
    company_name: str
    available: bool = Field(description="해당 company 에 snapshot 이 1건이라도 있는지")

    # 아래는 available=True 일 때만 채워짐
    run_at: datetime | None = None
    run_date: date | None = None
    analysis_job_id: str | None = None
    previous_risk_level: RiskLevel | None = None
    risk_level: RiskLevel | None = None
    risk_score: float | None = None
    risk_changed: bool | None = None
    summary: str | None = None
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    news_count: int = 0
    lawsuit_count: int = 0
    news_top_titles: list[str] = Field(default_factory=list)
    model: str | None = None
    snapshot_blob_path: str | None = None


class MonitorRunNowResponse(BaseModel):
    company_id: str
    company_name: str
    analysis_job_id: str = Field(description="이번 모니터링 실행으로 생성된 새 AnalysisJob.id")
    run_date: date
    risk_level: RiskLevel
    risk_score: float
    previous_risk_level: RiskLevel | None = None
    risk_changed: bool = Field(description="이전 스냅샷 대비 위험 등급 변화 여부")
    snapshot_blob_path: str
    status: Literal["snapshot_done"] = "snapshot_done"
    # 위험 판단의 근거 — snapshot.json 에 저장되는 값을 응답에도 노출 (frontend 가
    # 별도 SAS fetch 없이 사용하기 위함). monitor_run_now 외 다른 모니터링 응답은
    # 영향 X (이 필드는 MonitorRunNowResponse 에만 존재).
    summary: str | None = None
    key_risk_factors: list[str] = Field(
        default_factory=list, description="위험으로 판단한 근거 — 분석 LLM 출력 그대로"
    )
    positive_signals: list[str] = Field(
        default_factory=list, description="긍정 신호 — 등급 완화 요인"
    )
    data_gaps: list[str] = Field(default_factory=list, description="자료 부족 항목")
