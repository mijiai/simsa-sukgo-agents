"""report_generate Tool 의 service — 3단계 파이프라인.

흐름:
    1. raw.json + result.json 로드
    2. DART 재무 데이터 → extracted_table 변환 후 raw 에 prepend
       (planner 가 5_finance 섹션에 DART 표를 매핑할 수 있도록)
    3. template_spec 로드 (yaml → 메모리 캐시)
    4. Planner LLM (1회) → ReportPlan (검증/fallback)
    5. financial.section_insights + plan → SectionContent (LLM 호출 X)
    6. Narrative Writer LLM (1회) → 섹션별 서술형 paragraph
    7. docx + md 동시 렌더 → Blob 양쪽 업로드
    8. Status update + jobs_ref UPDATE
    9. ReportResponse 에 docx_url + report_url 둘 다 반환
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from src.agents.financial.schemas import SectionInsight
from src.agents.report.narrative import NarrativeMap, run_narrative_writer
from src.agents.report.planner import PlannedTable, ReportPlan, run_planner
from src.agents.report.renderer import render_report_docx, render_report_markdown
from src.agents.report.schemas import ReportRequest, ReportResponse
from src.agents.report.sections import SectionContent, build_section_content
from src.agents.report.template_spec import ReportTemplate, load_template
from src.agents.report.templates import get_cached_templates
from src.common.anthropic_client import AnthropicClient
from src.common.constants import ReportSection, RiskLevel
from src.config.logging import get_logger
from src.storage.blob_store import BlobStore
from src.storage.schemas import AgentName, JobStatus
from src.storage.table_store import TableStore

logger = get_logger(__name__)

DEFAULT_TEMPLATE_NAME = "loan_application_v1"
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_DART_STATEMENT_ROWS: list[tuple[str, str]] = [
    ("total_assets", "자산총계"),
    ("current_assets", "  유동자산"),
    ("noncurrent_assets", "  비유동자산"),
    ("total_liabilities", "부채총계"),
    ("current_liabilities", "  유동부채"),
    ("noncurrent_liabilities", "  비유동부채"),
    ("total_equity", "자본총계"),
    ("paid_in_capital", "  자본금"),
    ("retained_earnings", "  이익잉여금"),
    ("revenue", "매출액"),
    ("gross_profit", "매출총이익"),
    ("operating_income", "영업이익"),
    ("pretax_income", "법인세차감전순이익"),
    ("net_income", "당기순이익"),
    ("operating_cf", "영업활동현금흐름"),
    ("interest_expense", "이자비용"),
    ("finance_costs", "금융비용"),
]

_CORP_CLS_LABEL: dict[str, str] = {
    "Y": "유가증권시장 상장",
    "K": "코스닥 상장",
    "N": "코넥스 상장",
    "E": "기타 (비상장)",
}

_COMPANY_OVERVIEW_ROWS: list[tuple[str, str]] = [
    ("기업명", "corp_name"),
    ("영문명", "corp_name_eng"),
    ("대표자", "ceo_nm"),
    ("법인구분", "corp_cls"),
    ("종목코드", "stock_code"),
    ("법인등록번호", "jurir_no"),
    ("사업자등록번호", "bizr_no"),
    ("설립일", "est_dt"),
    ("결산월", "acc_mt"),
    ("업종코드", "induty_code"),
    ("본점 주소", "adres"),
    ("홈페이지", "hm_url"),
    ("IR 페이지", "ir_url"),
    ("전화번호", "phn_no"),
    ("팩스번호", "fax_no"),
]


def _format_overview_value(key: str, raw_value) -> str:
    if raw_value is None:
        return ""
    val = str(raw_value).strip()
    if not val:
        return ""
    if key == "corp_cls":
        return _CORP_CLS_LABEL.get(val, val)
    if key == "est_dt" and len(val) == 8 and val.isdigit():
        return f"{val[:4]}.{val[4:6]}.{val[6:8]}"
    if key == "acc_mt" and val.isdigit():
        return f"{int(val)}월"
    if key == "jurir_no" and len(val) == 13 and val.isdigit():
        return f"{val[:6]}-{val[6:]}"
    if key == "bizr_no" and len(val) == 10 and val.isdigit():
        return f"{val[:3]}-{val[3:5]}-{val[5:]}"
    return val


def _build_company_overview_table(company_info: dict) -> PlannedTable:
    if not company_info or not company_info.get("corp_name"):
        return PlannedTable(data_gap=True)

    rows = []
    for label, key in _COMPANY_OVERVIEW_ROWS:
        display = _format_overview_value(key, company_info.get(key))
        if not display:
            continue
        rows.append([label, display])

    if not rows:
        return PlannedTable(data_gap=True)

    return PlannedTable(
        columns=["항목", "내용"],
        rows=rows,
        source_label_override="※ 출처: DART 전자공시 기업개황",
        data_gap=False,
    )

def _format_eok(val: int | None) -> str:
    if val is None:
        return "N/A"
    return f"{val / 1_0000_0000:,.1f}"


def _safe_ratio(numerator, denominator, scale: float = 100.0):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * scale


def _build_dart_kpi_table(dart_financials: list[dict]) -> PlannedTable:
    years_data = sorted(
        [dy for dy in dart_financials if dy.get("has_data") and dy.get("accounts")],
        key=lambda d: d.get("year") or 0,
    )
    if not years_data:
        return PlannedTable(data_gap=True)

    columns = ["지표"] + [f"{dy['year']}년" for dy in years_data]

    def _row(label, getter, fmt):
        cells = [label]
        for dy in years_data:
            acct = dy.get("accounts") or {}
            val = getter(acct)
            cells.append(fmt.format(val) if val is not None else "N/A")
        return cells

    rows = [
        _row("부채비율 (%)",
             lambda a: _safe_ratio(a.get("total_liabilities"), a.get("total_equity")),
             "{:.1f}"),
        _row("유동비율 (%)",
             lambda a: _safe_ratio(a.get("current_assets"), a.get("current_liabilities")),
             "{:.1f}"),
        _row("영업이익률 (%)",
             lambda a: _safe_ratio(a.get("operating_income"), a.get("revenue")),
             "{:.1f}"),
        _row("순이익률 (%)",
             lambda a: _safe_ratio(a.get("net_income"), a.get("revenue")),
             "{:.1f}"),
        _row("이자보상배율 (배)",
             lambda a: _safe_ratio(
                 a.get("operating_income"),
                 a.get("interest_expense") or a.get("finance_costs"),
                 scale=1.0),
             "{:.2f}"),
    ]
    return PlannedTable(
        columns=columns, rows=rows,
        source_label_override="※ 출처: DART 전자공시 사업보고서 (자동 계산)",
        data_gap=False,
    )


def _build_dart_statement_table(dart_financials: list[dict]) -> PlannedTable:
    years_data = sorted(
        [dy for dy in dart_financials if dy.get("has_data") and dy.get("accounts")],
        key=lambda d: d.get("year") or 0,
    )
    if not years_data:
        return PlannedTable(data_gap=True)

    fs_divs = [dy.get("fs_div", "") for dy in years_data]
    fs_note = "연결" if "CFS" in fs_divs else "별도" if "OFS" in fs_divs else "재무제표"

    columns = ["계정과목 (단위: 억원)"] + [f"{dy['year']}년 ({fs_note})" for dy in years_data]
    rows = []
    for key, label in _DART_STATEMENT_ROWS:
        row = [label]
        any_val = False
        for dy in years_data:
            val = (dy.get("accounts") or {}).get(key)
            if val is not None:
                any_val = True
            row.append(_format_eok(val))
        if any_val:
            rows.append(row)

    if not rows:
        return PlannedTable(data_gap=True)

    return PlannedTable(
        columns=columns, rows=rows,
        source_label_override="※ 출처: DART 전자공시 사업보고서",
        data_gap=False,
    )


def _inject_dart_tables(
    plan: ReportPlan,
    dart_financials: list[dict],
    dart_company_info: dict | None = None,
) -> ReportPlan:
    overrides: list[tuple[str, str, PlannedTable]] = []

    company_overview = _build_company_overview_table(dart_company_info or {})
    overrides.append(("1_overview", "기업 개요", company_overview))

    if dart_financials:
        overrides.append(
            ("2_loan_summary", "주요 재무지표", _build_dart_kpi_table(dart_financials))
        )
        overrides.append(
            ("5_finance", "재무제표", _build_dart_statement_table(dart_financials))
        )

    for section_id, table_id, new_table in overrides:
        section = plan.sections.get(section_id)
        if section is None:
            continue
        if new_table.data_gap:
            continue
        section.tables[table_id] = new_table
        logger.info(
            "report.dart_table.injected",
            section_id=section_id,
            table_id=table_id,
            row_count=len(new_table.rows),
        )
    return plan

# DART 계정 키 → 한글 표시명
_DART_ACCOUNT_LABELS: dict[str, str] = {
    "total_assets": "자산총계",
    "total_liabilities": "부채총계",
    "total_equity": "자본총계",
    "current_assets": "유동자산",
    "noncurrent_assets": "비유동자산",
    "current_liabilities": "유동부채",
    "noncurrent_liabilities": "비유동부채",
    "revenue": "매출액",
    "gross_profit": "매출총이익",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "operating_cf": "영업활동현금흐름",
    "interest_expense": "이자비용",
    "finance_costs": "금융비용",
    "paid_in_capital": "자본금",
    "retained_earnings": "이익잉여금",
    "pretax_income": "법인세차감전순이익",
    "total_comprehensive_income": "총포괄손익",
}

# 보고서 표에 넣을 계정 순서 (핵심 계정만 우선)
_DART_REPORT_ACCOUNT_ORDER = [
    "total_assets",
    "total_liabilities",
    "total_equity",
    "current_assets",
    "current_liabilities",
    "revenue",
    "operating_income",
    "net_income",
    "operating_cf",
    "interest_expense",
    "finance_costs",
    "paid_in_capital",
    "retained_earnings",
    "pretax_income",
    "total_comprehensive_income",
]


def _fmt_eok(val: int | None) -> str:
    """원 단위 정수 → 억원 문자열. None 이면 '-'."""
    if val is None:
        return "-"
    eok = val / 1_0000_0000
    return f"{eok:,.1f}억"


def _dart_financials_to_extracted_table(dart_financials: list[dict[str, Any]]) -> dict[str, Any] | None:
    """DART 재무 데이터(dart_financials) → extracted_table 포맷 변환.

    planner 가 5_finance 섹션의 '재무제표' 표 슬롯을 채울 때
    raw.extracted_tables 에서 이 항목을 선택할 수 있도록 한다.

    has_data=True 인 기간만 포함. 데이터가 없으면 None 반환.
    """
    years_data = sorted(
        [dy for dy in dart_financials if dy.get("has_data") and dy.get("accounts")],
        key=lambda d: d["year"],
    )
    if not years_data:
        return None

    years = [dy["year"] for dy in years_data]
    fs_div = years_data[-1].get("fs_div", "")  # 최신 기간 기준
    fs_label = {"CFS": "연결", "OFS": "별도"}.get(fs_div, "")
    sheet_label = f"{fs_label}재무제표" if fs_label else "주요계정"

    columns = ["구분(억원)"] + [f"{y}년" for y in years]
    rows: list[list[str]] = []

    for account_key in _DART_REPORT_ACCOUNT_ORDER:
        label = _DART_ACCOUNT_LABELS.get(account_key, account_key)
        row = [label]
        has_any_value = False
        for dy in years_data:
            val = (dy.get("accounts") or {}).get(account_key)
            row.append(_fmt_eok(val))
            if val is not None:
                has_any_value = True
        if has_any_value:
            rows.append(row)

    if not rows:
        return None

    return {
        "source_file": "DART 전자공시",
        "sheet_name": sheet_label,
        "columns": columns,
        "rows": rows,
        "truncated": False,
    }


def _inject_dart_into_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """raw.dart_financials 를 extracted_table 로 변환해 raw.extracted_tables 선두에 추가.

    planner 가 DART 데이터를 가장 먼저 보도록 prepend.
    extracted_tables 에 이미 같은 출처('DART 전자공시') 가 있으면 중복 주입 방지.
    """
    dart_financials = raw.get("dart_financials") or []
    if not dart_financials:
        return raw  # DART 데이터 없으면 그대로

    dart_table = _dart_financials_to_extracted_table(dart_financials)
    if dart_table is None:
        return raw  # has_data=True 인 기간이 없으면 그대로

    existing_tables = raw.get("extracted_tables") or []
    # 중복 방지 — 이미 DART 테이블이 있으면 skip
    if any(t.get("source_file") == "DART 전자공시" for t in existing_tables):
        return raw

    return {**raw, "extracted_tables": [dart_table] + existing_tables}


def _raw_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/collect/raw.json"


def _result_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/analyze/result.json"


def _report_md_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/report/report.md"


def _report_docx_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/report/report.docx"


def _build_sections(
    template: ReportTemplate,
    plan,
    section_insights: list[SectionInsight],
    narratives: NarrativeMap,
) -> dict[ReportSection, SectionContent]:
    sections: dict[ReportSection, SectionContent] = {}
    for spec in template.sections:
        plan_section = plan.sections.get(spec.section_id.value)
        if plan_section is None:
            from src.agents.report.planner import PlannedSection
            plan_section = PlannedSection()
        narrative = narratives.narratives.get(spec.section_id.value, "")
        sections[spec.section_id] = build_section_content(
            spec, plan_section, section_insights, narrative=narrative
        )
    return sections


async def _maybe_download_base_docx(blob: BlobStore, base_blob_path: str) -> bytes | None:
    if not base_blob_path:
        return None
    try:
        return await blob.download(base_blob_path)
    except Exception as exc:
        logger.warning(
            "report.base_docx.download_failed",
            blob_path=base_blob_path,
            error=str(exc),
        )
        return None


async def report_generate_service(
    request: ReportRequest,
    blob: BlobStore,
    tables: TableStore,
    anthropic: AnthropicClient,
    *,
    sas_expiry_hours: int,
    template_name: str = DEFAULT_TEMPLATE_NAME,
    base_docx_blob_path: str = "",
    appendix_row_threshold: int = 0,
) -> ReportResponse:
    await tables.jobs.update_status(
        request.job_id,
        JobStatus.REPORTING,
        current_agent=AgentName.REPORT,
    )
    await tables.agent_status.update_running(request.job_id, AgentName.REPORT)
    logger.info(
        "report.start",
        job_id=request.job_id,
        model=anthropic.model,
        template=template_name,
    )

    try:
        # 1. 데이터 로드
        raw_bytes = await blob.download(_raw_blob_path(request.job_id))
        raw = json.loads(raw_bytes.decode("utf-8"))
        result_bytes = await blob.download(_result_blob_path(request.job_id))
        analysis = json.loads(result_bytes.decode("utf-8"))

        risk_level = RiskLevel(analysis["risk_level"])
        risk_score = float(analysis["risk_score"])
        company_id = analysis.get("company_id") or raw.get("company_id")

        # 2. DART 재무 데이터 → extracted_table 로 변환해 raw 에 주입
        #    planner 가 5_finance 섹션 표 슬롯을 채울 때 DART 데이터를 우선 사용하도록 prepend
        dart_financials = raw.get("dart_financials") or []
        raw = _inject_dart_into_raw(raw)
        if dart_financials:
            dart_years = [dy["year"] for dy in dart_financials if dy.get("has_data")]
            if dart_years:
                logger.info(
                    "report.dart.injected",
                    job_id=request.job_id,
                    dart_years=dart_years,
                )
            else:
                logger.info("report.dart.no_data_to_inject", job_id=request.job_id)

        # 3. 템플릿 로드
        template = load_template(template_name)

        # 4. Planner LLM (1회 호출)
        plan = await run_planner(template, raw, analysis, anthropic)
        logger.info(
            "report.planner.done",
            job_id=request.job_id,
            section_count=len(plan.sections),
        )

        plan = _inject_dart_tables(
            plan,
            raw.get("dart_financials") or [],
            raw.get("dart_company_info") or {},
        )


        # 5. Narrative Writer LLM (1회 호출)
        narratives = await run_narrative_writer(
            template, raw, analysis, plan, anthropic, samples=get_cached_templates()
        )
        non_empty_narratives = sum(1 for v in narratives.narratives.values() if v.strip())
        logger.info(
            "report.narrative.done",
            job_id=request.job_id,
            section_count=len(narratives.narratives),
            non_empty_count=non_empty_narratives,
        )

        # 6. 섹션 컨텐츠 조립 (LLM 호출 X)
        section_insights = [
            SectionInsight.model_validate(si) for si in (analysis.get("section_insights") or [])
        ]
        sections = _build_sections(template, plan, section_insights, narratives)

        # 7. docx + md 렌더
        base_docx_bytes = await _maybe_download_base_docx(blob, base_docx_blob_path)
        docx_bytes = await render_report_docx(
            template,
            sections,
            blob,
            base_docx_bytes=base_docx_bytes,
            appendix_row_threshold=appendix_row_threshold,
        )
        md_text = render_report_markdown(
            template,
            sections,
            appendix_row_threshold=appendix_row_threshold,
        )

        # 8. Blob 업로드 + SAS URL 발급
        docx_path = _report_docx_blob_path(request.job_id)
        md_path = _report_md_blob_path(request.job_id)
        await blob.upload(docx_path, docx_bytes, content_type=_DOCX_CONTENT_TYPE)
        await blob.upload(
            md_path,
            md_text.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        report_url = blob.generate_sas_url(md_path, timedelta(hours=sas_expiry_hours))
        docx_url = blob.generate_sas_url(docx_path, timedelta(hours=sas_expiry_hours))

        # 9. Status update
        finished_at = datetime.now(UTC)
        await tables.agent_status.update_done(
            request.job_id,
            AgentName.REPORT,
            output_blob_path=docx_path,
        )
        await tables.jobs.update_status(
            request.job_id,
            JobStatus.DONE,
            report_blob_path=docx_path,
            risk_level=risk_level,
            finished_at=finished_at,
        )
        if company_id:
            await tables.jobs_ref.update_after_done(
                company_id=company_id,
                job_id=request.job_id,
                risk_level=risk_level,
                finished_at=finished_at,
            )
        else:
            logger.warning("report.jobs_ref.skipped_no_company_id", job_id=request.job_id)

        logger.info(
            "report.done",
            job_id=request.job_id,
            risk_level=risk_level.value,
            docx_bytes=len(docx_bytes),
            md_chars=len(md_text),
        )

        return ReportResponse(
            job_id=request.job_id,
            risk_level=risk_level,
            risk_score=risk_score,
            report_url=report_url,
            report_blob_path=md_path,
            docx_url=docx_url,
            docx_blob_path=docx_path,
        )

    except Exception as exc:
        logger.error("report.failed", job_id=request.job_id, error=str(exc))
        await tables.agent_status.update_failed(request.job_id, AgentName.REPORT, str(exc))
        await tables.jobs.update_status(request.job_id, JobStatus.FAILED, error_message=str(exc))
        raise