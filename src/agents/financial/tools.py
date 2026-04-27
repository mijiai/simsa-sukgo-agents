from fastmcp import FastMCP

from src.agents.financial.factory import get_anthropic_client
from src.agents.financial.schemas import AnalyzeRequest
from src.agents.financial.service import analyze_financials_service
from src.storage.factory import get_blob_store, get_table_store


def register_financial_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def analyze_financials(job_id: str) -> dict:
        """
        주어진 job_id 의 collect/raw.json 을 읽고 Claude LLM 이 위험도를 판단한다.
        결과는 jobs/{job_id}/analyze/result.json 에 저장하고 경량 메타만 반환한다.

        사용 시점:
        - collect_company_data 호출 직후 (반환된 job_id 사용)

        입력:
        - job_id: collect_company_data 가 사용한 동일 job_id

        출력 (경량 메타데이터만):
        - job_id, status="analyze_done"
        - risk_level: LOW / MEDIUM / HIGH / CRITICAL
        - risk_score: 0~100
        - key_risk_factors: 주요 위험 요인 목록
        - data_gaps: 분석 시 부족했던 자료 영역 (over-trust 방지용)
        - output_blob_path: jobs/{job_id}/analyze/result.json

        실패 시:
        - AgentStatus[analyze]=failed, AnalysisJobs.status=failed 표시 후 예외 전파
        """
        request = AnalyzeRequest(job_id=job_id)
        response = await analyze_financials_service(
            request,
            get_blob_store(),
            get_table_store(),
            get_anthropic_client(),
        )
        return response.model_dump()
