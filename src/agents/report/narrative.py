"""Narrative Writer 단계 — 섹션별 서술형 paragraph 작성.

Option B 의 두 번째 LLM 호출:
    1. planner (run_planner) — 표/이미지 매핑 JSON 만
    2. ★ narrative writer (run_narrative_writer) — 섹션별 서술형 글

본 모듈은 다음을 담당한다:
- 섹션별로 raw + analysis + plan 을 보고 한국형 여신신청서 톤의 paragraph 생성
- 출력은 {section_id: paragraph_text} 의 단순 dict
- LLM 응답이 깨져도 빈 dict 로 fallback (renderer 가 알아서 narrative 생략)
- planner 와 동일하게 "자료에 없는 숫자/사실 절대 금지" 원칙 강제

PR-1 범위: 동작 추가 (minimal system prompt). 톤/문체 정교화는 PR-2 에서.
"""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.agents.report.planner import ReportPlan
from src.agents.report.template_spec import ReportTemplate
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger

logger = get_logger(__name__)


# 작성 시 LLM 에 보낼 sample 개수/길이 — Sonnet 기준 여유 있게 설정.
_MAX_SAMPLES = 3
_SAMPLE_TRUNCATE_CHARS = 8000
_DOC_TEXT_TRUNCATE_CHARS = 6000  # 작성 근거 텍스트 (raw.extracted_docs)


NARRATIVE_SYSTEM_PROMPT = """당신은 기업 여신 심사 보고서의 본문을 작성하는 한국 금융기관
실무자입니다. 주어진 자료(원자료 텍스트, 표 데이터, 분석 평가)를 바탕으로 각 섹션의
서술형 paragraph 를 작성합니다.

[sample 보고서 활용 원칙 — 가장 먼저 읽으십시오]
- 입력에 '문체 학습용 sample 보고서' 가 있으면 반드시 정독하십시오.
- sample 의 단락 구조, 어조, 어미 패턴을 최우선 기준으로 삼으십시오.
  예: sample 이 "~한 것으로 파악됨" 을 쓰면 동일 어미를 사용합니다.
- 단, sample 의 숫자·회사명·날짜는 현재 분석 기업의 실제 자료로 교체합니다.


규칙:
1. 출력은 JSON 객체 1개. 코드 펜스/머리말 없이 JSON 만.
   {"narratives": {"<section_id>": "<paragraph 텍스트>", ...}}
2. paragraph 는 한국 여신신청서 실무 문체:
   - 시제: 과거형/현재형 혼용 (`기록함`, `보유 중`, `회복세를 보이고 있음`).
   - 숫자는 천원/억원 단위 그대로, 증감액과 증감률을 같이 명시.
   - 원인-결과를 한 문장에 연결 (`이는 ~로 인함`).
   - 첫 문장은 핵심 사실, 이어지는 1~2 문장에 배경/원인/평가.
3. 길이: sample 이 있으면 sample 기준, 없으면 섹션당 1~3 paragraph (각 2~5 문장).
4. 자료에 없는 숫자나 사실은 절대 만들지 않습니다. 추정 표현(`것으로 보임`, `예상됨`)
   은 financial 의 분석 bullet 에 그 근거가 있을 때만 사용.
5. 표/이미지 본문을 그대로 옮기지 않습니다. 표가 보여주는 추세를 한 단락으로 요약.
6. section_id 는 입력으로 주어진 키를 그대로 사용. 모르는 키는 만들지 않습니다.
7. 자료가 부족한 섹션은 빈 문자열("") 로 둡니다 — 거짓 작성보다 공백이 낫습니다.
8. 인과관계 중심 서술: 단순 현상 나열이 아닌 '원인-현상-결과-향후 영향'의 구조로 작성한다.
9. 금융 전문 용어 활용: '현금창출력 제약', '금융비용 점증', '재무안정성 저하',
   '가격전가력 보유' 등 정제된 금융 용어를 사용합니다.
10.보수적 관점 유지: 긍정적 지표 뒤에는 반드시 잠재적 리스크(고정비 부담, 투자 소요 등)를
   검토하여 중립적이고 보수적인 결론을 도출합니다.
11.정량 데이터의 정성적 해석: 숫자를 언급할 때는 그것이 기업의 실질적 상환 능력에
   미치는 '의미'를 반드시 포함합니다.
"""


class NarrativeMap(BaseModel):
    """LLM 출력 schema — section_id → paragraph 텍스트."""

    narratives: dict[str, str] = Field(default_factory=dict)


def _summarize_template_for_writer(template: ReportTemplate) -> dict[str, Any]:
    """writer LLM 에 보낼 템플릿 요약 — 어떤 섹션에 어떤 표가 있는지 (글의 맥락)."""
    summary: dict[str, Any] = {}
    for spec in template.sections:
        summary[spec.section_id.value] = {
            "title": spec.title,
            "table_titles": [t.title for t in spec.tables],
            "image_titles": [img.title for img in spec.images],
        }
    return summary


def _summarize_extracted_docs_for_writer(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """원자료 텍스트 — 작성의 핵심 근거. 너무 길면 truncate."""
    return [
        {
            "source_file": d.get("source_file"),
            "text": (d.get("text") or "")[:_DOC_TEXT_TRUNCATE_CHARS],
        }
        for d in docs
    ]


def _summarize_section_insights_for_writer(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """financial 의 평가 bullet — 글에서 평가 톤 잡을 때 참고."""
    return [
        {
            "section_id": si.get("section_id"),
            "table_id": si.get("table_id"),
            "bullets": si.get("bullets") or [],
        }
        for si in insights
    ]


def _summarize_plan_for_writer(plan: ReportPlan) -> dict[str, Any]:
    """planner 가 매핑한 표 — 글이 표 데이터를 정확히 인용하도록."""
    out: dict[str, Any] = {}
    for section_id, section in plan.sections.items():
        out[section_id] = {
            "tables": {
                tid: {
                    "data_gap": t.data_gap,
                    "columns": t.columns,
                    "rows_preview": t.rows[:5],
                }
                for tid, t in section.tables.items()
            }
        }
    return out


def _select_samples(samples: list[str]) -> list[str]:
    """sample docx 텍스트 — 문체 학습용. PR-1 은 최대 1개, 4000 chars."""
    return [s[:_SAMPLE_TRUNCATE_CHARS] for s in samples[:_MAX_SAMPLES]]


def build_narrative_user_prompt(
    template: ReportTemplate,
    raw: dict[str, Any],
    analysis: dict[str, Any],
    plan: ReportPlan,
    samples: list[str] | None = None,
) -> str:
    template_summary = _summarize_template_for_writer(template)
    docs_summary = _summarize_extracted_docs_for_writer(raw.get("extracted_docs") or [])
    insights_summary = _summarize_section_insights_for_writer(
        analysis.get("section_insights") or []
    )
    plan_summary = _summarize_plan_for_writer(plan)
    sample_blocks = _select_samples(samples or [])

    sample_section = ""
    if sample_blocks:
        sample_section = "\n[문체 학습용 sample 보고서]\n" + "\n---\n".join(sample_blocks) + "\n"

    return f"""[작성할 섹션 목록 (section_id, 표/이미지 제목)]
{json.dumps(template_summary, ensure_ascii=False, indent=2)}

[원자료 텍스트 — 작성의 1차 근거]
{json.dumps(docs_summary, ensure_ascii=False, indent=2)}

[financial 분석 bullets — 평가 톤 / 위험 신호 반영용]
{json.dumps(insights_summary, ensure_ascii=False, indent=2)}

[planner 가 만든 표 매핑 — 글이 인용할 수 있는 데이터 포인트]
{json.dumps(plan_summary, ensure_ascii=False, indent=2)}
{sample_section}
위 자료만 사용해 NARRATIVE_SYSTEM_PROMPT 의 규칙대로 각 섹션의 서술형 paragraph 를
작성하세요. 응답은 {{"narratives": {{"<section_id>": "..."}}}} JSON 객체 1개.
"""


def empty_narrative_map(template: ReportTemplate) -> NarrativeMap:
    """LLM 실패 시 — 모든 섹션 narrative 를 빈 문자열로 (renderer 가 생략)."""
    return NarrativeMap(narratives={spec.section_id.value: "" for spec in template.sections})


def merge_with_empty_fallback(template: ReportTemplate, nmap: NarrativeMap) -> NarrativeMap:
    """누락 섹션은 ""로 채워 모든 spec.section_id 가 키로 존재하도록 보강."""
    merged = dict(nmap.narratives)
    for spec in template.sections:
        merged.setdefault(spec.section_id.value, "")
    return NarrativeMap(narratives=merged)


async def run_narrative_writer(
    template: ReportTemplate,
    raw: dict[str, Any],
    analysis: dict[str, Any],
    plan: ReportPlan,
    anthropic: AnthropicClient,
    samples: list[str] | None = None,
) -> NarrativeMap:
    """LLM 1회 호출 → NarrativeMap. LLM/검증 실패 시 빈 narratives fallback."""
    user_prompt = build_narrative_user_prompt(template, raw, analysis, plan, samples)
    try:
        nmap_dict = await anthropic.complete_json(system=NARRATIVE_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        logger.warning("report.narrative.llm_failed", error=str(exc))
        return empty_narrative_map(template)

    try:
        nmap = NarrativeMap.model_validate(nmap_dict)
    except ValidationError as exc:
        logger.warning("report.narrative.invalid_response", error=str(exc))
        return empty_narrative_map(template)

    return merge_with_empty_fallback(template, nmap)
