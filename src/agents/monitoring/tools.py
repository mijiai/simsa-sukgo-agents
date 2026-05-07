from fastmcp import FastMCP

from src.agents.collector.factory import get_naver_news_client
from src.agents.financial.factory import get_anthropic_client
from src.agents.monitoring.factory import get_gmail_client
from src.agents.monitoring.run_service import monitor_run_now_service
from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorRegisterRequest,
    MonitorRunNowRequest,
)
from src.agents.monitoring.service import (
    monitor_deregister_service,
    monitor_register_service,
)
from src.config.settings import get_settings
from src.storage.factory import get_blob_store, get_table_store


def register_monitoring_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def monitor_register(
        company_id: str,
        company_name: str,
        recipient_email: str,
        origin_job_id: str,
    ) -> dict:
        """
        기업을 사후관리 모니터링 대상으로 등록한다.

        보고서 생성(report_generate) 완료 후 해당 기업을 분기(3개월) 주기로
        재분석하고 위험 등급 상승 시 이메일 알림을 받고 싶을 때 호출.

        사용 시점:
        - report_generate 완료 후 (사용자가 모니터링 받고 싶다고 의사 표시할 때만)

        입력:
        - company_id: 기존 Companies Table 에 존재해야 함
        - company_name: 기업명 (UI 표시용)
        - recipient_email: 알림 수신 이메일
        - origin_job_id: 최초 보고서를 생성한 분석 job_id (이력 추적용)

        출력:
        - company_id, company_name, recipient_email, status="registered"

        주의:
        - 동일 company_id 재등록은 upsert (recipient_email/origin_job_id 갱신)
        - company_id 가 Companies 에 없으면 EntityNotFoundError 발생
        - 실제 분기 모니터링 실행은 후속 PR (monitor_run_now + APScheduler)
        """
        request = MonitorRegisterRequest(
            company_id=company_id,
            company_name=company_name,
            recipient_email=recipient_email,
            origin_job_id=origin_job_id,
        )
        response = await monitor_register_service(request, get_table_store())
        return response.model_dump()

    @mcp.tool()
    async def monitor_deregister(company_id: str) -> dict:
        """
        모니터링 대상에서 해제 (soft delete). is_active=False 로 표시.
        과거 스냅샷·알림 이력은 보존 (감사 추적용).

        입력:
        - company_id

        출력:
        - company_id, status="deregistered"
        """
        request = MonitorDeregisterRequest(company_id=company_id)
        response = await monitor_deregister_service(request, get_table_store())
        return response.model_dump()

    @mcp.tool()
    async def monitor_run_now(company_id: str) -> dict:
        """
        등록된 모니터링 대상에 대해 즉시 재분석 (수동 트리거 / 분기 cron 도 같은 함수 호출).

        동작:
        - MonitoringTargets 에서 active 한 target 인지 검증
        - 새 AnalysisJob 생성 (user_id="system:monitoring")
        - collect_company_data_service → analyze_financials_service 순차 실행 (보고서는 생성 X)
        - analyze 결과 + raw 데이터를 합쳐 monitoring/{company_id}/{YYYYMMDD}/snapshot.json 에 저장
        - MonitoringSnapshots Table 에 lightweight row INSERT (UI 목록 표시용)
        - MonitoringTarget.last_run_at, last_risk_level 갱신
        - 응답에 위험 등급 변동 여부(risk_changed) 포함

        Gmail 알림은 별도 PR 에서 추가 — 이 단계는 snapshot 저장까지만.

        입력:
        - company_id

        출력:
        - company_id, company_name, status="snapshot_done"
        - analysis_job_id: 이번 실행으로 생성된 AnalysisJob.id (드릴다운 용)
        - run_date, risk_level, risk_score
        - previous_risk_level, risk_changed: 이전 스냅샷 대비 변동 여부
        - snapshot_blob_path: 상세 데이터 Blob 경로

        실패 시:
        - 대상 미등록: EntityNotFoundError
        - 대상 비활성: MonitoringTargetInactiveError
        - collect/analyze 실패: 해당 service 가 AnalysisJobs/AgentStatus 를 failed 로
          표시 후 예외 전파
        """
        request = MonitorRunNowRequest(company_id=company_id)
        response = await monitor_run_now_service(
            request,
            get_blob_store(),
            get_table_store(),
            get_naver_news_client(),
            get_anthropic_client(),
            gmail=get_gmail_client(),
            settings=get_settings(),
        )
        return response.model_dump()
