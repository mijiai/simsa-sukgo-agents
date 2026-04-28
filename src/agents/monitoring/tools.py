from fastmcp import FastMCP

from src.agents.monitoring.schemas import (
    MonitorDeregisterRequest,
    MonitorRegisterRequest,
)
from src.agents.monitoring.service import (
    monitor_deregister_service,
    monitor_list_service,
    monitor_register_service,
)
from src.storage.factory import get_table_store


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
    async def monitor_list() -> dict:
        """
        현재 active 한 모니터링 대상 목록을 조회.

        출력:
        - count: 활성 대상 수
        - targets: 각 항목 {company_id, company_name, recipient_email,
                            origin_job_id, registered_at, last_run_at?,
                            last_risk_level?}
        """
        response = await monitor_list_service(get_table_store())
        return response.model_dump()
