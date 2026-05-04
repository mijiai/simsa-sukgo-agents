"""Planner LLM 단계 검증 — 정상 응답 / 검증 실패 fallback / 누락 슬롯 보강."""

from unittest.mock import AsyncMock, MagicMock

from src.agents.report.planner import (
    PlannedImage,
    PlannedSection,
    PlannedTable,
    ReportPlan,
    fallback_plan,
    merge_with_fallback,
    run_planner,
)
from src.agents.report.template_spec import load_template


def setup_module() -> None:
    load_template.cache_clear()


def _template():
    return load_template("loan_application_v1")


# ───────────────────── fallback / merge ─────────────────────


def test_fallback_plan_marks_all_slots_data_gap() -> None:
    template = _template()
    plan = fallback_plan(template)
    assert isinstance(plan, ReportPlan)
    for spec in template.sections:
        section_plan = plan.sections[spec.section_id.value]
        for tbl_spec in spec.tables:
            assert section_plan.tables[tbl_spec.table_id].data_gap is True
        for img_spec in spec.images:
            assert section_plan.images[img_spec.image_id].data_gap is True


def test_merge_with_fallback_fills_missing_slots() -> None:
    template = _template()
    # LLM 이 5_finance/재무제표 만 채웠다고 가정
    partial = ReportPlan(
        sections={
            "5_finance": PlannedSection(
                tables={
                    "재무제표": PlannedTable(data_gap=False, columns=["A"], rows=[["x"]]),
                }
            )
        }
    )
    merged = merge_with_fallback(template, partial)
    finance = merged.sections["5_finance"]
    # 채워진 슬롯은 그대로
    assert finance.tables["재무제표"].columns == ["A"]
    assert finance.tables["재무제표"].data_gap is False
    # 같은 섹션의 다른 슬롯은 data_gap=true
    assert finance.tables["자산건전성"].data_gap is True
    assert finance.tables["자본적정성"].data_gap is True
    # 다른 섹션도 data_gap=true 로 채워짐
    overview = merged.sections["1_overview"]
    assert overview.tables["기업 개요"].data_gap is True


# ───────────────────── run_planner ─────────────────────


def _anthropic_returning(plan_dict: dict) -> MagicMock:
    client = MagicMock()
    client.complete_json = AsyncMock(return_value=plan_dict)
    return client


async def test_run_planner_propagates_valid_plan() -> None:
    template = _template()
    plan_dict = {
        "sections": {
            "5_finance": {
                "tables": {
                    "재무제표": {
                        "data_gap": False,
                        "columns": ["구분", "2023", "2024"],
                        "rows": [["자산총계", 1000, 1200]],
                    }
                },
                "images": {},
            }
        }
    }
    anthropic = _anthropic_returning(plan_dict)
    result = await run_planner(template, raw={}, analysis={}, anthropic=anthropic)
    finance = result.sections["5_finance"]
    assert finance.tables["재무제표"].columns == ["구분", "2023", "2024"]
    assert finance.tables["재무제표"].rows == [["자산총계", 1000, 1200]]
    # 같은 섹션의 다른 표는 누락 → fallback
    assert finance.tables["자산건전성"].data_gap is True


async def test_run_planner_returns_full_fallback_on_validation_error() -> None:
    template = _template()
    # 스키마 위반 (sections 가 dict 가 아니라 list)
    bad_plan = {"sections": ["not", "a", "dict"]}
    anthropic = _anthropic_returning(bad_plan)
    result = await run_planner(template, raw={}, analysis={}, anthropic=anthropic)
    # 모든 슬롯 data_gap=true
    for spec in template.sections:
        sp = result.sections[spec.section_id.value]
        for tbl in spec.tables:
            assert sp.tables[tbl.table_id].data_gap is True


async def test_run_planner_returns_full_fallback_on_llm_exception() -> None:
    template = _template()
    anthropic = MagicMock()
    anthropic.complete_json = AsyncMock(side_effect=RuntimeError("boom"))
    result = await run_planner(template, raw={}, analysis={}, anthropic=anthropic)
    # 모든 슬롯 data_gap=true (LLM 자체가 실패해도 보고서 생성은 계속)
    for spec in template.sections:
        sp = result.sections[spec.section_id.value]
        for tbl in spec.tables:
            assert sp.tables[tbl.table_id].data_gap is True


async def test_run_planner_called_exactly_once_per_invocation() -> None:
    """LLM 호출 횟수 정책 — planner 1회만."""
    template = _template()
    anthropic = _anthropic_returning({"sections": {}})
    await run_planner(template, raw={}, analysis={}, anthropic=anthropic)
    anthropic.complete_json.assert_awaited_once()


def test_planned_table_default_values() -> None:
    pt = PlannedTable()
    assert pt.data_gap is False
    assert pt.columns == []
    assert pt.rows == []
    assert pt.source_label_override is None


def test_planned_image_default_values() -> None:
    pi = PlannedImage()
    assert pi.data_gap is False
    assert pi.blob_path is None
    assert pi.caption is None
