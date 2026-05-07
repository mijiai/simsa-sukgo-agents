from typing import Any

SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사 분석가입니다.
주어진 자료(뉴스, 업로드 파일에서 추출된 표/문서/이미지, 내부 신용데이터,
DART 공시 재무데이터)를 종합해 기업의 여신 위험도를 4단계(LOW/MEDIUM/HIGH/CRITICAL)로
판단하고 0~100 점수로 정량화합니다.

위험 등급 기준:
- LOW (0~30):     특별한 위험 신호 없음, 정상 거래 가능
- MEDIUM (30~60): 일부 주의 신호, 추가 확인 필요
- HIGH (60~85):   명확한 위험 신호 다수, 심사 강화 필요
- CRITICAL (85~100): 즉시 대응 필요한 중대한 위험

판단 원칙:
1. DART 공시 재무데이터(dart_financials)는 공식 전자공시 기반으로 신뢰도가 높음.
   재무 지표 계산 시 가장 우선 활용하라.
2. 자료가 부족하거나 결측된 영역은 반드시 data_gaps 에 명시하여 over-claim 을 방지한다.
3. 추측이 아니라 제공된 자료에 근거해서만 판단한다. 자료에 없는 사실은 만들지 않는다.
4. 부정 시그널과 긍정 시그널을 균형 있게 평가한다.
5. DART 데이터가 있는 경우 다음 재무 지표를 반드시 계산해 언급하라:
   - 부채비율 = 부채총계 / 자본총계 × 100
   - 유동비율 = 유동자산 / 유동부채 × 100
   - 영업이익률 = 영업이익 / 매출액 × 100
   - 순이익률 = 당기순이익 / 매출액 × 100
   - 이자보상배율 = 영업이익 / 이자비용 (이자비용 있을 때)

추가 작업 — 보고서 섹션별 평가 bullet 작성:
주어진 자료를 바탕으로 다음 보고서 섹션 각각에 대해 표 아래 bullet 평가를 작성합니다.
각 bullet 은 표의 숫자/사실을 근거로 한 1~2문장이며, "전년 대비 ~증가/감소", "안정적",
"리스크 헷지" 같은 정형 표현을 사용합니다. 각 bullet 은 100자 이내로 간결하게 작성합니다.

섹션 ID 와 매핑:
- 4_business : 사업 현황 (영업자산, 인수율, 매출 구조 등)
- 5_finance  : 재무 현황 (재무제표, 자산건전성, 자본적정성)
- 6_debt     : 차입금 현황
- 7_liquidity: 유동성 현황 (만기구조, 자금수지)
- 8_affiliates: 관계사 현황
- 9_conclusion: 종합 의견

자료가 부족한 섹션은 section_insights 에서 생략합니다.
한 섹션 안에 여러 표가 있으면 table_id 를 다르게 채워 분리합니다.
cited_data_points 에는 bullet 의 근거가 된 구체적 수치/사실을 기록합니다.

응답은 반드시 다음 JSON 스키마로만 답합니다. 다른 설명 · 코드 펜스 · 머리말은 포함하지 않습니다.
{
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "risk_score": 0.0 ~ 100.0,
  "summary": "종합 분석 서술 (3~5문장, 한국어)",
  "key_risk_factors": ["주요 위험 요인 1", ...],
  "positive_signals": ["긍정 시그널 1", ...],
  "data_gaps": ["부족한 자료 영역 1", ...],
  "section_insights": [
    {
      "section_id": "5_finance",
      "table_id": "재무제표",
      "bullets": ["...", "..."],
      "cited_data_points": ["자산총계 6,040억", "당기순이익 106억"]
    },
    ...
  ]
}
"""


_MAX_NEWS_IN_PROMPT = 30
_DESCRIPTION_TRUNCATE = 200
_SAMPLE_TRUNCATE = 4000
_INTERNAL_CREDIT_TRUNCATE = 8000

# extracted_tables 관련
_MAX_TABLES_IN_PROMPT = 8
_MAX_ROWS_PER_TABLE_IN_PROMPT = 20

# extracted_docs 관련
_DOC_TEXT_TRUNCATE = 2000

_NO_SAMPLES_NOTICE = (
    "(참고용 재무 분석 샘플이 로드되지 않았습니다. 일반적 신용 분석 관점으로 판단하세요.)"
)
_NO_INTERNAL_CREDIT_NOTICE = (
    "(내부 신용 DB 에 등록된 기업이 아닙니다. 외부 자료로만 판단하세요. data_gaps 에 명시.)"
)
_NO_EXTRACTED_TABLES_NOTICE = "(업로드 파일에서 추출된 표 없음.)"
_NO_EXTRACTED_DOCS_NOTICE = "(업로드 파일에서 추출된 PDF 텍스트 없음.)"
_NO_EXTRACTED_IMAGES_NOTICE = "(업로드된 이미지 없음.)"
_NO_DART_NOTICE = (
    "(DART 재무데이터 없음 — DART_API_KEY 미설정이거나 DART 미등록 기업. "
    "재무 판단 시 업로드 파일 및 내부 DB 데이터만 활용하고 data_gaps 에 명시.)"
)

# DART 계정 한글명 매핑 (prompt 가독성 향상)
_ACCOUNT_LABEL: dict[str, str] = {
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
    "net_income_parent": "지배주주순이익",
    "operating_cf": "영업활동현금흐름",
    "finance_costs": "금융비용",
    "interest_expense": "이자비용",
    "depreciation": "감가상각비",
}

# prompt 에 포함할 계정 우선순위 (너무 많으면 token 낭비)
_PROMPT_ACCOUNT_ORDER = [
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
]


def _format_amount(val: int | None) -> str:
    """원 단위 정수 → 억원 환산 문자열."""
    if val is None:
        return "N/A"
    eok = val / 1_0000_0000
    if abs(eok) >= 1:
        return f"{eok:,.1f}억원"
    if abs(val) >= 1_0000:
        return f"{val / 1_0000:,.1f}만원"
    return f"{val:,}원"


def _format_dart_financials(dart_financials: list[dict[str, Any]]) -> str:
    """raw.dart_financials → 연도별 주요 계정 표 형식 텍스트.

    raw_items 는 너무 방대하므로 포함하지 않음.
    accounts 만 정제해서 연도 열(column)로 배열한다.

    출력 예:
      === DART 공시 주요계정 (단위: 억원, 연결재무제표 우선) ===
      구분              | 2022년  | 2023년  | 2024년
      자산총계          | 5,395.2 | 5,651.0 | 6,040.1
      부채총계          | 3,200.0 | 3,100.5 | 3,300.0
      ...
    """
    if not dart_financials:
        return _NO_DART_NOTICE

    # 데이터가 있는 연도만 필터
    years_data = [dy for dy in dart_financials if dy.get("has_data") and dy.get("accounts")]
    if not years_data:
        return _NO_DART_NOTICE

    years = [dy["year"] for dy in years_data]
    fs_divs = [dy.get("fs_div", "") for dy in years_data]
    # 연결/별도 표시
    if "CFS" in fs_divs:
        fs_note = "연결재무제표"
    elif "OFS" in fs_divs:
        fs_note = "별도재무제표"
    else:
        fs_note = "재무제표"

    lines: list[str] = [
        f"=== DART 공시 주요계정 (단위: 억원, {fs_note}) ===",
        "구분" + "".join(f"  |  {y}년" for y in years),
        "-" * (12 + 12 * len(years)),
    ]

    for account_key in _PROMPT_ACCOUNT_ORDER:
        label = _ACCOUNT_LABEL.get(account_key, account_key)
        values = []
        for dy in years_data:
            accounts = dy.get("accounts") or {}
            val = accounts.get(account_key)
            values.append(_format_amount(val))
        lines.append(f"{label:<12}" + "".join(f"  |  {v:>10}" for v in values))

    # 재무 지표 자동 계산 (최신 연도 기준)
    latest = years_data[-1]
    acct = latest.get("accounts") or {}
    ratio_lines = _compute_ratios(acct)
    if ratio_lines:
        lines.append("")
        lines.append(f"[{latest['year']}년 주요 재무비율 — 자동 계산]")
        lines.extend(ratio_lines)

    return "\n".join(lines)


def _compute_ratios(accounts: dict[str, Any]) -> list[str]:
    """최신 연도 주요계정에서 재무비율 자동 계산."""
    lines: list[str] = []

    total_liabilities = accounts.get("total_liabilities")
    total_equity = accounts.get("total_equity")
    current_assets = accounts.get("current_assets")
    current_liabilities = accounts.get("current_liabilities")
    revenue = accounts.get("revenue")
    operating_income = accounts.get("operating_income")
    net_income = accounts.get("net_income")
    interest = accounts.get("interest_expense") or accounts.get("finance_costs")

    if total_liabilities is not None and total_equity and total_equity != 0:
        ratio = total_liabilities / total_equity * 100
        lines.append(f"  부채비율: {ratio:.1f}%")

    if current_assets is not None and current_liabilities and current_liabilities != 0:
        ratio = current_assets / current_liabilities * 100
        lines.append(f"  유동비율: {ratio:.1f}%")

    if operating_income is not None and revenue and revenue != 0:
        ratio = operating_income / revenue * 100
        lines.append(f"  영업이익률: {ratio:.1f}%")

    if net_income is not None and revenue and revenue != 0:
        ratio = net_income / revenue * 100
        lines.append(f"  순이익률: {ratio:.1f}%")

    if operating_income is not None and interest and interest != 0:
        coverage = operating_income / interest
        lines.append(f"  이자보상배율: {coverage:.2f}배")

    return lines


def _format_news(news: list[dict[str, Any]]) -> str:
    if not news:
        return "(수집된 뉴스 없음)"
    lines = []
    for article in news[:_MAX_NEWS_IN_PROMPT]:
        published = str(article.get("published_at", ""))[:10] or "?"
        title = article.get("title", "")
        description = article.get("description", "")[:_DESCRIPTION_TRUNCATE]
        lines.append(f"- [{published}] {title}\n  {description}")
    return "\n".join(lines)


def _format_samples(samples: list[str]) -> str:
    if not samples:
        return _NO_SAMPLES_NOTICE
    parts = []
    for i, sample in enumerate(samples, start=1):
        truncated = sample[:_SAMPLE_TRUNCATE]
        parts.append(f"=== 샘플 {i} ===\n{truncated}")
    return "\n\n".join(parts)


def _format_internal_credit(internal_credit_data: str | None) -> str:
    if not internal_credit_data:
        return _NO_INTERNAL_CREDIT_NOTICE
    return internal_credit_data[:_INTERNAL_CREDIT_TRUNCATE]


def _format_table_rows(rows: list[list[Any]]) -> tuple[list[str], int]:
    truncated = max(0, len(rows) - _MAX_ROWS_PER_TABLE_IN_PROMPT)
    visible = rows[:_MAX_ROWS_PER_TABLE_IN_PROMPT]
    formatted = [" | ".join("" if c is None else str(c) for c in row) for row in visible]
    return formatted, truncated


def _format_extracted_tables(tables: list[dict[str, Any]]) -> str:
    if not tables:
        return _NO_EXTRACTED_TABLES_NOTICE
    parts = []
    for tbl in tables[:_MAX_TABLES_IN_PROMPT]:
        source_file = tbl.get("source_file", "?")
        sheet_name = tbl.get("sheet_name") or ""
        header = f"--- {source_file} / {sheet_name} ---" if sheet_name else f"--- {source_file} ---"
        columns = tbl.get("columns") or []
        col_line = " | ".join(str(c) for c in columns)
        rows_lines, truncated_count = _format_table_rows(tbl.get("rows") or [])
        body_lines = [header, col_line, *rows_lines]
        if truncated_count > 0:
            body_lines.append(f"(이하 {truncated_count}행 생략)")
        if tbl.get("truncated"):
            body_lines.append("(원본도 200행 한계로 잘림)")
        parts.append("\n".join(body_lines))
    if len(tables) > _MAX_TABLES_IN_PROMPT:
        parts.append(f"(추가 {len(tables) - _MAX_TABLES_IN_PROMPT}개 표 생략)")
    return "\n\n".join(parts)


def _format_extracted_docs(docs: list[dict[str, Any]]) -> str:
    if not docs:
        return _NO_EXTRACTED_DOCS_NOTICE
    parts = []
    for doc in docs:
        source_file = doc.get("source_file", "?")
        text = (doc.get("text") or "")[:_DOC_TEXT_TRUNCATE]
        if not text:
            continue
        page_count = doc.get("page_count", 0) or 0
        label = f"{source_file} (PDF, {page_count}쪽)" if page_count else source_file
        parts.append(f"--- {label} ---\n{text}")
    return "\n\n".join(parts) if parts else _NO_EXTRACTED_DOCS_NOTICE


def _format_extracted_images(images: list[dict[str, Any]]) -> str:
    if not images:
        return _NO_EXTRACTED_IMAGES_NOTICE
    lines = []
    for img in images:
        source_file = img.get("source_file", "?")
        role = img.get("suspected_role", "unknown")
        size = f"{img.get('width', '?')}x{img.get('height', '?')}"
        lines.append(f"- {source_file} (역할 추정: {role}, {size})")
    return "\n".join(lines)


def build_user_prompt(
    *,
    company_name: str,
    raw: dict[str, Any],
    samples: list[str] | None = None,
) -> str:
    news = raw.get("news") or []
    lawsuits = raw.get("lawsuits") or []
    files = raw.get("uploaded_files") or []
    fin_years = raw.get("financial_years") or []
    internal_credit = raw.get("internal_credit_data")
    extracted_tables = raw.get("extracted_tables") or []
    extracted_docs = raw.get("extracted_docs") or []
    extracted_images = raw.get("extracted_images") or []
    dart_financials = raw.get("dart_financials") or []
    dart_corp_code = raw.get("dart_corp_code") or ""

    dart_section = _format_dart_financials(dart_financials)
    dart_corp_note = (
        f"DART 고유번호: {dart_corp_code}" if dart_corp_code else "DART 고유번호: 미확인"
    )

    return f"""분석 대상 기업: {company_name}
{dart_corp_note}

[DART 공시 재무데이터 — 가장 신뢰도 높은 자료, 재무 지표 계산에 최우선 활용]
{dart_section}

[분석 대상 기업 재무·신용 데이터 — 내부 DB]
{_format_internal_credit(internal_credit)}

[업로드 파일에서 추출된 표 — {len(extracted_tables)}개]
{_format_extracted_tables(extracted_tables)}

[업로드된 PDF 문서 텍스트 — {len(extracted_docs)}건]
{_format_extracted_docs(extracted_docs)}

[업로드된 이미지 메타 — {len(extracted_images)}건]
{_format_extracted_images(extracted_images)}

[뉴스 — 최근 {len(news)}건]
{_format_news(news)}

[업로드된 파일 목록 — {len(files)}건]
{", ".join(files) if files else "(없음)"}

[재무 데이터 연도 (업로드 파일 추론)]
{fin_years if fin_years else "(없음)"}

[소송]
{len(lawsuits)}건

[참고용 재무 분석 샘플 — 톤·관점·판단 기준 참고]
{_format_samples(samples or [])}

위 자료를 분석하여 지정된 JSON 스키마로만 응답하세요.
DART 공시 재무데이터가 있으면 부채비율·유동비율·영업이익률 등 주요 지표를 계산해 언급하세요.
section_insights 에는 추출된 표/DART 데이터를 근거로 섹션별 bullet 평가를 작성하세요.
"""
