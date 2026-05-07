from fastmcp import FastMCP

from src.agents.collector.factory import get_dart_client, get_naver_news_client
from src.agents.collector.schemas import CollectRequest
from src.agents.collector.service import collect_company_data_service
from src.agents.financial.factory import get_anthropic_client
from src.config.settings import get_settings
from src.storage.factory import get_blob_store, get_table_store


def register_collector_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def collect_company_data(job_id: str, company_name: str) -> dict:
        """
        주어진 job_id와 기업명으로 외부/업로드 자료를 구조화 추출해
        Blob에 raw.json으로 저장한다. 후속 Tool(analyze_financials) 에는
        이 job_id 만 전달.

        사용 시점:
        - create_analysis_job 호출 직후 (반환된 job_id 사용)

        입력:
        - job_id: create_analysis_job 반환값
        - company_name: 분석할 기업명 (create_analysis_job에 넘긴 값과 동일)

        수행 작업:
        - jobs/{job_id}/input/* 에서 xlsx/pdf/image 추출 → raw.json 의
          extracted_tables/images/docs 필드
        - Companies 테이블에서 회사 매칭 → 사전 등록 신용 데이터 inject
        - Naver News 검색
        - [DART] DART_API_KEY 가 설정된 경우: 단일회사 주요계정 조회 (3개년 사업보고서)
          → dart_corp_code, dart_financials 필드로 raw.json 에 저장
          → 동일 기업 재분석 시 corp_code 캐시 사용 (API 호출 절약)
        - (옵션) IMAGE_VISION_ENABLED=true 시 이미지마다 Vision API 1회 호출 → caption

        출력 (경량 메타데이터만):
        - job_id, status="collect_done"
        - company_name, company_id
        - news_count, uploaded_files
        - extracted_table_count / extracted_image_count / extracted_doc_count
        - financial_years (xlsx 컬럼에서 자동 추론)
        - has_internal_credit_data
        - dart_corp_code: DART 고유번호 (없으면 null — DART 미연동 or 미등록 기업)
        - dart_financial_years: DART 에서 실제 데이터를 수신한 연도 목록
        - output_blob_path

        실패 시:
        - AgentStatus[collect] = failed, AnalysisJobs.status = failed 로 표시 후 예외 전파
        - DART 오류는 전체 collect 를 실패시키지 않음 (dart_financials=[] 로 graceful 처리)
        """
        settings = get_settings()
        vision_anthropic = (
            get_anthropic_client()
            if settings.image_vision_enabled and settings.anthropic_api_key
            else None
        )

        request = CollectRequest(job_id=job_id, company_name=company_name)
        response = await collect_company_data_service(
            request,
            get_blob_store(),
            get_table_store(),
            get_naver_news_client(),
            dart=get_dart_client(),
            vision_anthropic=vision_anthropic,
            vision_model=settings.image_vision_model if vision_anthropic else None,
        )
        return response.model_dump()
