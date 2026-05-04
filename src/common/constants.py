from enum import StrEnum


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReportSection(StrEnum):
    """보고서 정형 섹션 ID. financial.section_insights 와 report.template_spec 가 공유.

    보고서 9개 표준 섹션 + 별첨. 숫자 prefix 는 정렬 안정성을 위함 (StrEnum.value 기준 정렬).
    """

    OVERVIEW = "1_overview"  # 차주 개요
    LOAN_SUMMARY = "2_loan_summary"  # 여신 개요
    LOAN_TERMS = "3_loan_terms"  # 여신 신청 조건
    BUSINESS = "4_business"  # 사업 현황
    FINANCE = "5_finance"  # 재무 현황
    DEBT = "6_debt"  # 차입금 현황
    LIQUIDITY = "7_liquidity"  # 유동성 현황
    AFFILIATES = "8_affiliates"  # 관계사 현황
    CONCLUSION = "9_conclusion"  # 종합 의견
