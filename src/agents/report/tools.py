from fastmcp import FastMCP

from src.agents.report.factory import get_report_anthropic_client
from src.agents.report.schemas import ReportRequest
from src.agents.report.service import report_generate_service
from src.config.settings import get_settings
from src.storage.factory import get_blob_store, get_table_store


def register_report_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def report_generate(job_id: str) -> dict:
        """
        주어진 job_id 의 collect/raw.json + analyze/result.json 을 읽고,
        Azure Blob 에 미리 업로드된 과거 심사 보고서 샘플(`templates/report_samples/*.docx`)
        의 톤·구조를 모방하여 Markdown 보고서를 작성한다.
        결과는 jobs/{job_id}/report/report.md 에 저장하고 SAS URL 을 반환한다.

        사용 시점:
        - analyze_financials 호출 직후 (반환된 job_id 사용)

        입력:
        - job_id: analyze_financials 가 사용한 동일 job_id

        출력:
        - job_id, status="done"
        - risk_level: LOW / MEDIUM / HIGH / CRITICAL (analyze 결과 그대로)
        - risk_score: 0~100
        - report_url: report.md 의 SAS URL (만료 있음, 기본 7일)
        - report_blob_path: jobs/{job_id}/report/report.md

        실패 시:
        - AgentStatus[report]=failed, AnalysisJobs.status=failed 표시 후 예외 전파

        주의:
        - 샘플 보고서는 서버 startup 시 1회 로드되어 메모리 캐시에 보관된다.
          새 샘플을 추가했다면 서버 재시작이 필요하다.
        """
        request = ReportRequest(job_id=job_id)
        settings = get_settings()
        response = await report_generate_service(
            request,
            get_blob_store(),
            get_table_store(),
            get_report_anthropic_client(),
            sas_expiry_hours=settings.report_sas_expiry_hours,
        )
        return response.model_dump()
