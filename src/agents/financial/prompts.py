from typing import Any

SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사 분석가입니다.
주어진 자료(뉴스, 업로드 파일 목록, 재무 데이터, 소송 데이터, 내부 신용데이터)를 종합해
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

응답은 반드시 다음 JSON 스키마로만 답합니다. 다른 설명 · 코드 펜스 · 머리말은 포함하지 않습니다.
{
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "risk_score": 0.0 ~ 100.0,
  "summary": "종합 분석 서술 (3~5문장, 한국어)",
  "key_risk_factors": ["주요 위험 요인 1", "주요 위험 요인 2", ...],
  "positive_signals": ["긍정 시그널 1", ...],
  "data_gaps": ["부족한 자료 영역 1", ...]
}
"""


_MAX_NEWS_IN_PROMPT = 30
_DESCRIPTION_TRUNCATE = 200
_SAMPLE_TRUNCATE = 4000

_NO_SAMPLES_NOTICE = (
    "(참고용 재무 분석 샘플이 로드되지 않았습니다. 일반적 신용 분석 관점으로 판단하세요.)"
)


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

    return f"""분석 대상 기업: {company_name}

[뉴스 — 최근 {len(news)}건]
{_format_news(news)}

[업로드된 파일 — {len(files)}건]
{", ".join(files) if files else "(없음)"}

[재무 데이터]
연도별 행: {fin_years if fin_years else "(없음)"}

[소송]
{len(lawsuits)}건

[참고용 재무 분석 샘플 — 톤·관점·판단 기준 참고]
{_format_samples(samples or [])}

위 자료를 분석하여 지정된 JSON 스키마로만 응답하세요.
"""
