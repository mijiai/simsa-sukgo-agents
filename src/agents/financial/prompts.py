from typing import Any

SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사 분석가입니다.
주어진 자료(뉴스, 업로드 파일에서 추출된 표/문서/이미지, 내부 신용데이터)를 종합해
기업의 여신 위험도를 4단계(LOW/MEDIUM/HIGH/CRITICAL)로 판단하고 0~100 점수로 정량화합니다.

위험 등급 기준:
- LOW (0~30):     특별한 위험 신호 없음, 정상 거래 가능
- MEDIUM (30~60): 일부 주의 신호, 추가 확인 필요
- HIGH (60~85):   명확한 위험 신호 다수, 심사 강화 필요
- CRITICAL (85~100): 즉시 대응 필요한 중대한 위험

판단 원칙:
1. 자료가 부족하거나 결측된 영역(재무제표 미확보, 소송 데이터 없음, 내부 신용데이터 없음 등)은
   반드시 data_gaps 에 명시하여 over-claim 을 방지합니다.
2. 추측이 아니라 제공된 자료에 근거해서만 판단합니다. 자료에 없는 사실은 만들어내지 않습니다.
3. 부정 시그널과 긍정 시그널을 균형 있게 평가합니다.
4. 자료가 거의 없는 경우 risk_score 는 보수적으로(중간값 근처) 산출하고
   data_gaps 를 충실히 채웁니다.

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
한 섹션 안에 여러 표가 있으면 table_id 를 다르게 채워 분리합니다 (예: "재무제표", "자산건전성").
cited_data_points 에는 bullet 의 근거가 된 구체적 수치/사실을 기록합니다 (감사 추적용).

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

# extracted_tables 관련 (PR2 신규)
_MAX_TABLES_IN_PROMPT = 8
_MAX_ROWS_PER_TABLE_IN_PROMPT = 20

# extracted_docs 관련 (PR2 신규)
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
    """행을 pipe-joined 문자열 리스트로 변환. 20행 초과 시 truncated count 반환."""
    truncated = max(0, len(rows) - _MAX_ROWS_PER_TABLE_IN_PROMPT)
    visible = rows[:_MAX_ROWS_PER_TABLE_IN_PROMPT]
    formatted = [" | ".join("" if c is None else str(c) for c in row) for row in visible]
    return formatted, truncated


def _format_extracted_tables(tables: list[dict[str, Any]]) -> str:
    """raw.extracted_tables 를 prompt 섹션으로 직렬화.

    상한:
    - 8개 표까지만 (초과 시 첫 8개)
    - 표당 20행까지 (초과 시 "(이하 N행 생략)")
    """
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
        parts.append(f"--- {source_file} (PDF, {doc.get('page_count', 0)}쪽) ---\n{text}")
    return "\n\n".join(parts) if parts else _NO_EXTRACTED_DOCS_NOTICE


def _format_extracted_images(images: list[dict[str, Any]]) -> str:
    """이미지는 메타정보만 (역할 분류 placeholder). 실제 caption 은 PR4 에서."""
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

    return f"""분석 대상 기업: {company_name}

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

[재무 데이터 연도]
{fin_years if fin_years else "(없음)"}

[소송]
{len(lawsuits)}건

[참고용 재무 분석 샘플 — 톤·관점·판단 기준 참고]
{_format_samples(samples or [])}

위 자료를 분석하여 지정된 JSON 스키마로만 응답하세요.
section_insights 에는 추출된 표를 근거로 섹션별 bullet 평가를 작성하세요.
"""
