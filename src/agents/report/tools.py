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
        정형 템플릿(loan_application_v1) 의 9개 표준 섹션에 따라 보고서를
        Markdown + DOCX 두 가지 형식으로 동시에 생성한다.

        파이프라인 (Option B — LLM 은 표를 만들지 않음):
        1. 템플릿 스펙 로드 (yaml)
        2. Planner LLM 1회 호출 — 어떤 표/이미지 슬롯에 어떤 데이터를 넣을지 매핑만
        3. financial 의 section_insights 를 표 아래 bullet 으로 그대로 배치
        4. python-docx 로 .docx 직접 빌드 + GFM markdown 동시 생성
        5. 양쪽 모두 Blob 업로드 + SAS URL 발급

        사용 시점:
        - analyze_financials 호출 직후 (반환된 job_id 사용)

        입력:
        - job_id: analyze_financials 가 사용한 동일 job_id

        출력:
        - job_id, status="done"
        - risk_level / risk_score (analyze 결과 그대로)
        - report_url: report.md 의 SAS URL (호환용)
        - report_blob_path: jobs/{job_id}/report/report.md
        - docx_url: report.docx 의 SAS URL
        - docx_blob_path: jobs/{job_id}/report/report.docx

        실패 시:
        - AgentStatus[report]=failed, AnalysisJobs.status=failed 표시 후 예외 전파
        - LLM 응답이 데이터 매핑 JSON 으로 검증 실패하면 모든 슬롯 data_gap=true
          fallback (보고서는 정상 생성, 표는 "[자료 미확보]" 로 표시).
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
