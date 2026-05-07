"""Planner LLM 단계 — 데이터 매핑 JSON 만 생성 (실제 문장 작성 X).

Option B 의 핵심: report agent 의 LLM 호출 횟수를 1회로 묶어두고, 그 1회는
오직 \"어떤 표/이미지에 어떤 데이터를 넣을지\" 매핑만 한다. 표 본문이나 평가 bullet 은
financial agent 의 section_insights 를 그대로 가져온다.

흐름:
    template_spec + raw + analysis
        → planner LLM (1회 호출)
        → ReportPlan (Pydantic 검증)
        → 검증 실패 시 fallback (모든 슬롯 data_gap=true)
"""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.agents.report.template_spec import ReportTemplate
from src.common.anthropic_client import AnthropicClient
from src.config.logging import get_logger

logger = get_logger(__name__)


PLANNER_SYSTEM_PROMPT = """당신은 여신 심사 보고서 데이터 매핑 플래너입니다.
주어진 raw, analysis, 템플릿 스펙을 보고 각 섹션의 각 표/이미지 슬롯에
어떤 데이터를 넣을지 매핑만 하세요. 실제 평가 문장은 작성하지 않습니다.

규칙:
1. 데이터 매핑 JSON 만 출력. 코드 펜스나 머리말 없이 JSON 객체로만 답변.
2. 자료가 없거나 적합한 표를 찾지 못하면 해당 슬롯에 "data_gap": true 로 표시.
3. 표 데이터는 raw.extracted_tables 에서 가장 적합한 것을 선택해
   columns/rows 를 그대로 인용 (값 변경 금지).
3-1. extracted_tables 가 비었거나 적합한 표가 없으면 raw.extracted_docs[].text
   에서 직접 columns/rows 를 구성하세요. 이 텍스트는
   "라벨 | 값1 | 값2 | ..." 형식으로 시트별로 정리되어 있어 신용보고서 .xls 의
   다단 레이아웃도 거의 그대로 읽을 수 있습니다. 단, 텍스트에 명시된 값만
   사용하고 새 숫자나 라벨을 만들지 마세요. 텍스트가 ` | ` 로 잘려있으면 그
   분리된 셀을 columns/rows 의 셀로 그대로 매핑하세요.
4. 새 숫자나 사실을 만들지 않습니다. 추출되지 않은 수치는 절대 채우지 않습니다.
5. 이미지 슬롯의 blob_path 는 raw.extracted_images[].blob_path 를 그대로 사용.
   suspected_role 이 일치하는 이미지가 없으면 data_gap=true.

응답 JSON 스키마:
{
  "sections": {
    "<section_id>": {
      "tables": {
        "<table_id>": {
          "data_gap": true|false,
          "columns": [...],
          "rows": [[...], ...],
          "source_label_override": null | "..."
        }
      },
      "images": {
        "<image_id>": {
          "data_gap": true|false,
          "blob_path": null | "...",
          "caption": null | "..."
        }
      }
    }
  }
}

section_id, table_id, image_id 는 템플릿 스펙에서 주어진 값을 그대로 사용합니다.
"""


class PlannedTable(BaseModel):
    data_gap: bool = False
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    source_label_override: str | None = None


class PlannedImage(BaseModel):
    data_gap: bool = False
    blob_path: str | None = None
    caption: str | None = None


class PlannedSection(BaseModel):
    tables: dict[str, PlannedTable] = Field(default_factory=dict)
    images: dict[str, PlannedImage] = Field(default_factory=dict)


class ReportPlan(BaseModel):
    sections: dict[str, PlannedSection] = Field(default_factory=dict)


def _summarize_template(template: ReportTemplate) -> dict[str, Any]:
    """LLM 에 보낼 템플릿 요약 — 슬롯 키만 명시 (스펙 본문은 너무 길어 요약)."""
    summary: dict[str, Any] = {}
    for spec in template.sections:
        summary[spec.section_id.value] = {
            "title": spec.title,
            "tables": [
                {
                    "table_id": t.table_id,
                    "title": t.title,
                    "data_source_hint": t.data_source,
                }
                for t in spec.tables
            ],
            "images": [
                {
                    "image_id": img.image_id,
                    "title": img.title,
                    "suspected_role": img.suspected_role,
                }
                for img in spec.images
            ],
        }
    return summary


def _summarize_extracted_tables(extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """planner 에게 보여줄 raw.extracted_tables 요약 — 컬럼명 + 처음 3행만."""
    summarized = []
    for tbl in extracted:
        summarized.append(
            {
                "source_file": tbl.get("source_file"),
                "sheet_name": tbl.get("sheet_name"),
                "columns": tbl.get("columns") or [],
                "rows_preview": (tbl.get("rows") or [])[:3],
                "total_rows": len(tbl.get("rows") or []),
            }
        )
    return summarized


def _summarize_section_insights(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """planner 에게 보여줄 section_insights 요약 — 어느 (section_id, table_id) 가
    bullet 을 갖고 있는지만. bullet 본문은 sections.py 가 직접 매칭하므로 불필요."""
    return [
        {
            "section_id": si.get("section_id"),
            "table_id": si.get("table_id"),
            "bullet_count": len(si.get("bullets") or []),
        }
        for si in insights
    ]


def _summarize_extracted_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """planner 에게 docs 의 텍스트를 그대로 노출 (PDF 인쇄용 .xls 의 fallback 데이터)."""
    return [
        {
            "source_file": d.get("source_file"),
            "text": d.get("text") or "",
        }
        for d in docs
    ]


def build_planner_user_prompt(
    template: ReportTemplate, raw: dict[str, Any], analysis: dict[str, Any]
) -> str:
    template_summary = _summarize_template(template)
    extracted_summary = _summarize_extracted_tables(raw.get("extracted_tables") or [])
    docs_summary = _summarize_extracted_docs(raw.get("extracted_docs") or [])
    images = raw.get("extracted_images") or []
    insights_summary = _summarize_section_insights(analysis.get("section_insights") or [])

    # 사람 읽기 + LLM 파싱 둘 다 좋게 JSON 으로 직렬화
    return f"""[템플릿 스펙 — 채울 슬롯 목록]
{json.dumps(template_summary, ensure_ascii=False, indent=2)}

[raw.extracted_tables — 채울 수 있는 표 데이터]
{json.dumps(extracted_summary, ensure_ascii=False, indent=2)}

[raw.extracted_docs — 표 추출 실패 시 fallback 으로 쓸 시트 텍스트]
{json.dumps(docs_summary, ensure_ascii=False, indent=2)}

[raw.extracted_images — 채울 수 있는 이미지]
{json.dumps(images, ensure_ascii=False, indent=2)}

[result.section_insights — 어느 슬롯에 bullet 이 붙을지 (참고용)]
{json.dumps(insights_summary, ensure_ascii=False, indent=2)}

위 자료를 바탕으로 PLANNER_SYSTEM_PROMPT 의 JSON 스키마에 맞춰 매핑만 출력하세요.
extracted_tables 의 row 는 truncated 되어 보입니다 — 실제 매핑 시에는 raw.extracted_tables[i].rows
전체를 그대로 인용한다고 가정하고, 'columns' 와 'rows' 를 알려주세요. 값 변경 금지.
extracted_tables 가 비었거나 적합한 표가 없으면 extracted_docs 의 text 에서
직접 columns/rows 를 구성하세요 (규칙 3-1 참고).
"""


def fallback_plan(template: ReportTemplate) -> ReportPlan:
    """LLM 응답 검증 실패 시 — 모든 슬롯 data_gap=true 로 채워 안전 fallback."""
    sections: dict[str, PlannedSection] = {}
    for spec in template.sections:
        sections[spec.section_id.value] = PlannedSection(
            tables={t.table_id: PlannedTable(data_gap=True) for t in spec.tables},
            images={img.image_id: PlannedImage(data_gap=True) for img in spec.images},
        )
    return ReportPlan(sections=sections)


def merge_with_fallback(template: ReportTemplate, plan: ReportPlan) -> ReportPlan:
    """LLM 이 일부 슬롯만 채웠을 수 있음 — 누락된 슬롯은 data_gap=true 로 보강."""
    merged_sections: dict[str, PlannedSection] = {}
    for spec in template.sections:
        section_key = spec.section_id.value
        existing = plan.sections.get(section_key)

        tables: dict[str, PlannedTable] = {}
        for t_spec in spec.tables:
            if existing and t_spec.table_id in existing.tables:
                tables[t_spec.table_id] = existing.tables[t_spec.table_id]
            else:
                tables[t_spec.table_id] = PlannedTable(data_gap=True)

        images: dict[str, PlannedImage] = {}
        for img_spec in spec.images:
            if existing and img_spec.image_id in existing.images:
                images[img_spec.image_id] = existing.images[img_spec.image_id]
            else:
                images[img_spec.image_id] = PlannedImage(data_gap=True)

        merged_sections[section_key] = PlannedSection(tables=tables, images=images)
    return ReportPlan(sections=merged_sections)


async def run_planner(
    template: ReportTemplate,
    raw: dict[str, Any],
    analysis: dict[str, Any],
    anthropic: AnthropicClient,
) -> ReportPlan:
    """LLM 1회 호출 → ReportPlan 반환. 검증 실패 시 안전 fallback."""
    user_prompt = build_planner_user_prompt(template, raw, analysis)
    try:
        plan_dict = await anthropic.complete_json(system=PLANNER_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        logger.warning("report.planner.llm_failed", error=str(exc))
        return fallback_plan(template)

    try:
        plan = ReportPlan.model_validate(plan_dict)
    except ValidationError as exc:
        logger.warning("report.planner.invalid_response", error=str(exc))
        return fallback_plan(template)

    # 누락 슬롯은 data_gap=true 로 보강
    return merge_with_fallback(template, plan)
