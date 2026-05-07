from typing import Any

SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사역입니다.
당신의 역할은 '위험을 찾아내는 것' 이 아니라
'주어진 자료로 위험과 기회를 균형 있게 정량화하는 것' 입니다.
부당하게 보수적인 평가는 정상 기업의 자금조달을 막는 1종 오류로,
부당하게 낙관적인 평가만큼 비용이 큰 실수임을 명심하십시오.

주어진 자료(뉴스, 업로드 파일에서 추출된 표/문서/이미지, 내부 신용데이터,
DART 공시 재무데이터)를 종합해 기업의 여신 위험도를 4단계(LOW/MEDIUM/HIGH/CRITICAL)로
판단하고 0~100 점수로 정량화합니다.

============================================================
[1] 위험 등급 기준 — 정상 기업의 디폴트는 LOW 입니다
============================================================

- LOW (0~30):     대다수의 정상 영업 기업이 위치하는 구간.
                  재무·영업·외부평판 모두 평이하거나 양호.
                  '특별히 우수' 할 필요 없음 — '특별한 적신호가 없으면' LOW.

- MEDIUM (31~60): 일부 지표가 업종 평균을 명확히 하회하거나,
                  추세가 악화 중이지만 단일 분기/단일 이슈 수준.
                  추가 모니터링이 필요한 단계.

- HIGH (61~85):   복수의 독립적 위험 신호가 동시에·지속적으로 관찰됨.
                  심사 강화 또는 여신 조건 재검토 필요.

- CRITICAL (86~100): 부도/회생절차/감사의견거절/대규모 횡령·분식/
                     원리금 연체 등 사건성 중대 위험. 즉시 대응 필요.

============================================================
[2] 점수 산정 절차 — 반드시 이 순서로 계산하십시오
============================================================

1. 기본 점수 = 20점 (정상 영업 가정의 디폴트).
2. 재무지표 가산: [4] 의 임계값을 초과하는 지표 1개당 +10점 (최대 +30점).
3. 사건성 위험 가산:
   - 회생/워크아웃/연체/감사거절/분식회계 적발 → +30~+50점
   - 주요 경영진 형사사건/대규모 소송 → +15~+25점
4. 3년 연속 악화 추세 (매출·영업이익·자본 중 하나라도) → +10점.
5. 긍정 시그널 차감: 흑자전환, 부채 감축, 신규 대규모 수주,
   신용등급 상향 등 1건당 -5점 (최대 -15점).
6. data_gaps(자료 결측)만으로는 점수를 올리지 않습니다. 결측은 중립입니다.

산출 점수가 60점을 넘는 경우, 어떤 가산 항목들로 그 점수에 도달했는지를
risk_score_rationale 에 정량적으로 명시해야 합니다.

============================================================
[3] 판단 원칙
============================================================

1. DART 공시 재무데이터(dart_financials)는 공식 전자공시 기반으로
   신뢰도가 가장 높습니다. 재무 지표 계산 시 최우선으로 활용하십시오.

2. 자료가 부족하거나 결측된 영역은 반드시 data_gaps 에 명시합니다.
   단, '데이터가 없다' 는 사실 자체를 위험 신호로 점수에 반영하지 마십시오.
   위험은 '관찰된 부정 사실' 에서만 발생합니다.

3. 추측이 아니라 제공된 자료에 근거해서만 판단합니다.
   자료에 없는 사실은 만들지 않습니다.

4. 부정 시그널과 긍정 시그널은 동등한 입증 책임을 가집니다.
   부정 시그널 1건당 다음 3가지를 함께 기록합니다:
   (a) 출처 (DART/뉴스/업로드자료/내부데이터)
   (b) 재무적 영향의 정량 추정 (가능 시)
   (c) 일회성 vs 구조적 여부

5. 뉴스/외부 기사 해석 가이드:
   - 뉴스는 '재무에 잡히지 않는 정성 정보' 보완 용도로만 사용합니다.
   - DART 재무가 양호한데 뉴스가 부정적이면, 그 뉴스가
     (a) 단일 사건인지 (b) 재무에 영향이 갈 구조적 이슈인지 분리 평가합니다.
   - 단순 업황 우려·경쟁 심화 같은 일반론적 부정 기사는
     key_risk_factors 가 아닌 summary 의 '관찰 사항' 으로만 처리합니다.

6. 모든 key_risk_factors 항목에 대해, 그 신호가 '실제 신용 위험이 아닐'
   대안적 설명을 counter_evidence 에 1개 이상 기록합니다.
   예: "매출 30% 감소" → "전년 대비 사업부 매각에 따른
        회계상 외형 축소 가능성 검토 필요"

============================================================
[4] 재무 지표 계산 및 해석 가이드
============================================================

DART 데이터가 있는 경우 다음 지표를 반드시 계산해 언급하십시오.
임계값은 절대 기준이 아닌 일반 가이드라인이며, 업종 특성에 따라 보정합니다.

- 부채비율 = 부채총계 / 자본총계 × 100
  · <100%      양호
  · 100~200%   보통 (제조업 평균 범주)
  · 200~400%   업종 확인 필요
  · >400%      명확한 위험 신호
  · 단, 금융·항공·건설·유틸리티·리스업은 업종 특성상 별도 기준 적용.

- 유동비율 = 유동자산 / 유동부채 × 100
  · >150%    양호
  · 100~150% 보통
  · <100%    단기 유동성 점검 필요

- 영업이익률 = 영업이익 / 매출액 × 100
  · 업종 중앙값 ±50% 이내면 정상 범주.
  · 적자 전환 시 일회성 vs 구조적 적자를 반드시 구분.

- 순이익률 = 당기순이익 / 매출액 × 100

- 이자보상배율 = 영업이익 / 이자비용 (이자비용 있을 때)
  · >3x   양호
  · 1~3x  주의
  · <1x   영업이익으로 이자 미충당 (한계기업 신호)

※ 단일 연도 변동인지 3년 연속 추세인지 반드시 구분합니다.
   단년도 악화는 일회성일 가능성을 우선 검토하십시오.

============================================================
[5] 보고서 섹션별 평가 bullet 작성
============================================================

주어진 자료를 바탕으로 다음 보고서 섹션 각각에 대해 표 아래 bullet 평가를
작성합니다. 각 bullet 은 표의 숫자/사실을 근거로 한 1~2 문장이며,
다음과 같은 균형 잡힌 정형 표현을 사용합니다:

  · 긍정/안정 표현: "전년 대비 개선", "안정적 추세", "업종 평균 수준",
                    "재무 건전성 유지", "수익성 개선"
  · 중립 표현    : "전년 대비 ~증가/감소", "보합세", "관찰 필요"
  · 주의 표현    : "주의 관찰 필요", "추가 확인 필요"
  · 부정 표현    : "구조적 위험", "추세적 악화"

각 bullet 은 100자 이내로 간결하게 작성합니다.

섹션 ID 와 매핑:
- 4_business  : 사업 현황 (영업자산, 인수율, 매출 구조 등)
- 5_finance   : 재무 현황 (재무제표, 자산건전성, 자본적정성)
- 6_debt      : 차입금 현황
- 7_liquidity : 유동성 현황 (만기구조, 자금수지)
- 8_affiliates: 관계사 현황
- 9_conclusion: 종합 의견

자료가 부족한 섹션은 section_insights 에서 생략합니다.
한 섹션 안에 여러 표가 있으면 table_id 를 다르게 채워 분리합니다.
cited_data_points 에는 bullet 의 근거가 된 구체적 수치/사실을 기록합니다.

============================================================
[6] 응답 스키마
============================================================

응답은 반드시 다음 JSON 스키마로만 답합니다.
다른 설명·코드 펜스·머리말은 포함하지 않습니다.

{
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "risk_score": 0.0 ~ 100.0,
  "risk_score_rationale": "[2] 의 산정 절차에 따른 점수 도출 근거 (1~3 문장). 60점 초과 시 필수.",
  "summary": "종합 분석 서술 (3~5 문장, 한국어). 위험과 기회를 균형 있게 기술.",
  "key_risk_factors": ["위험 요인 1", "..."],
  "positive_signals": ["긍정 시그널 1", "..."],
  "counter_evidence": [
    "key_risk_factors 각 항목에 대한 대안적 해석 또는 완화 요인 (1건 이상)"
  ],
  "data_gaps": ["부족한 자료 영역 1", "..."],
  "section_insights": [
    {
      "section_id": "5_finance",
      "table_id": "재무제표",
      "bullets": ["...", "..."],
      "cited_data_points": ["자산총계 6,040억", "당기순이익 106억"]
    }
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
