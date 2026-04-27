import json
from typing import Any

SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사역입니다.
주어진 자료(자료 수집 결과 raw.json + 재무·위험 분석 결과 result.json + 과거 심사 보고서 샘플)를
바탕으로 정식 심사 보고서를 Markdown 형식으로 작성합니다.

작성 원칙:
1. 제공된 과거 보고서 샘플의 톤·구조·표현·섹션 순서를 충실히 따르세요.
   새로운 형식을 발명하지 말고, 같은 섹션 헤딩과 같은 어투를 사용하세요.
2. result.json 의 risk_level, risk_score, key_risk_factors, positive_signals 를
   보고서 핵심 근거로 사용하세요.
3. result.json.data_gaps 에 명시된 결측 자료 영역(재무제표 부재, 소송 데이터 없음 등)은
   "추가 확인사항" 섹션에 그대로 명시하여 휴먼 심사역이 후속 조치하도록 합니다.
4. 추측이나 자료에 없는 사실은 만들어내지 마세요.
   자료가 없으면 "자료 미확보" 라고 명시하세요.
5. 출력은 한국어, Markdown 형식. # ## ### 헤딩 사용.
6. 머리말·"여기 보고서입니다" 같은 안내문 없이 곧바로 보고서 본문부터 출력합니다.
"""

_NO_TEMPLATE_NOTICE = (
    "[참고] 과거 보고서 샘플이 로드되지 않았습니다. "
    "은행 심사 보고서의 일반적 구조(기업 개요 / 신청 목적 / 재무 현황 / "
    "리스크 요인 / 추가 확인사항 / 종합 의견)를 따르세요."
)


def _format_templates(templates: list[str]) -> str:
    if not templates:
        return _NO_TEMPLATE_NOTICE
    parts = []
    for i, sample in enumerate(templates, start=1):
        parts.append(f"=== 샘플 {i} ===\n{sample}")
    return "\n\n".join(parts)


def build_user_prompt(
    *,
    raw: dict[str, Any],
    analysis: dict[str, Any],
    templates: list[str],
) -> str:
    raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
    analysis_json = json.dumps(analysis, ensure_ascii=False, indent=2)
    templates_section = _format_templates(templates)

    return f"""[자료 수집 결과 — raw.json]
{raw_json}

[재무·위험 분석 결과 — result.json]
{analysis_json}

[과거 심사 보고서 샘플 — 동일한 양식·톤·섹션 순서로 작성하세요]
{templates_section}

위 자료를 종합해 같은 양식의 심사 보고서를 Markdown 으로 작성하세요.
"""
