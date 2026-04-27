from fastmcp import FastMCP

from src.agents.collector.factory import get_naver_news_client
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service
from src.storage.factory import get_blob_store, get_table_store


def register_collector_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def collect_company_data(job_id: str, company_name: str) -> dict:
        """
        주어진 job_id와 기업명으로 외부 자료(현재 Naver News)를 수집해
        Blob에 raw.json으로 저장한다. 후속 Tool(analyze_financials) 에는
        이 job_id 만 전달.

        사용 시점:
        - create_analysis_job 호출 직후 (반환된 job_id 사용)

        입력:
        - job_id: create_analysis_job 반환값
        - company_name: 분석할 기업명 (create_analysis_job에 넘긴 값과 동일)

        출력 (경량 메타데이터만):
        - job_id, status="collect_done"
        - company_name, company_id
        - news_count: 수집된 뉴스 건수
        - lawsuit_count: 0 (소송 API 미연동)
        - financial_years: [] (재무 데이터 소스 미연동)
        - uploaded_files: 입력 파일 이름 목록
        - output_blob_path: jobs/{job_id}/collect/raw.json

        실패 시:
        - AgentStatus[collect] = failed, AnalysisJobs.status = failed 로 표시 후 예외 전파
        """
        request = CollectRequest(job_id=job_id, company_name=company_name)
        response = await collect_company_data_service(
            request,
            get_blob_store(),
            get_table_store(),
            get_naver_news_client(),
        )
        return response.model_dump()
