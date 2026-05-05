"""Narrative Writer LLM 단계 검증 — 정상 응답 / 검증 실패 fallback / 누락 보강."""

from unittest.mock import AsyncMock, MagicMock

from src.agents.report.narrative import (
    NARRATIVE_SYSTEM_PROMPT,
    NarrativeMap,
    build_narrative_user_prompt,
    empty_narrative_map,
    merge_with_empty_fallback,
    run_narrative_writer,
)
from src.agents.report.planner import PlannedSection, PlannedTable, ReportPlan
from src.agents.report.template_spec import load_template


def setup_module() -> None:
    load_template.cache_clear()


def _template():
    return load_template("loan_application_v1")


# ───────────────────── system prompt ─────────────────────


def test_system_prompt_demands_json_only_response() -> None:
    assert "JSON" in NARRATIVE_SYSTEM_PROMPT
    assert "narratives" in NARRATIVE_SYSTEM_PROMPT


def test_system_prompt_forbids_fabrication() -> None:
    """planner 와 동일 원칙 — 자료에 없는 숫자/사실 절대 금지."""
    assert "절대 만들지 않습니다" in NARRATIVE_SYSTEM_PROMPT


def test_system_prompt_recommends_blank_when_data_absent() -> None:
    """자료 부족 섹션은 빈 문자열로 — 거짓 작성보다 공백."""
    assert '""' in NARRATIVE_SYSTEM_PROMPT


# ───────────────────── empty / merge fallback ─────────────────────


def test_empty_narrative_map_covers_all_template_sections() -> None:
    template = _template()
    nmap = empty_narrative_map(template)
    section_ids = {spec.section_id.value for spec in template.sections}
    assert set(nmap.narratives.keys()) == section_ids
    assert all(v == "" for v in nmap.narratives.values())


def test_merge_with_empty_fallback_fills_only_missing_sections() -> None:
    """LLM 이 일부만 채워도 누락 섹션은 ""로 자동 채워짐."""
    template = _template()
    partial = NarrativeMap(narratives={"1_overview": "차주 개요 본문"})
    merged = merge_with_empty_fallback(template, partial)
    assert merged.narratives["1_overview"] == "차주 개요 본문"
    other_keys = [
        spec.section_id.value for spec in template.sections if spec.section_id.value != "1_overview"
    ]
    for k in other_keys:
        assert merged.narratives[k] == ""


# ───────────────────── user prompt 구조 ─────────────────────


def test_user_prompt_includes_extracted_docs_block() -> None:
    template = _template()
    raw = {
        "extracted_docs": [{"source_file": "credit.xls", "text": "상호 | 회사X\n자산총계 | 1000"}],
    }
    plan = ReportPlan(sections={})
    out = build_narrative_user_prompt(template, raw, {}, plan)
    assert "credit.xls" in out
    assert "상호 | 회사X" in out


def test_user_prompt_includes_section_insights_when_present() -> None:
    template = _template()
    analysis = {
        "section_insights": [
            {"section_id": "5_finance", "table_id": "income", "bullets": ["순이익 증가"]}
        ]
    }
    out = build_narrative_user_prompt(template, {}, analysis, ReportPlan(sections={}))
    assert "5_finance" in out
    assert "순이익 증가" in out


def test_user_prompt_includes_plan_table_data_for_citation() -> None:
    template = _template()
    plan = ReportPlan(
        sections={
            "5_finance": PlannedSection(
                tables={
                    "income": PlannedTable(
                        columns=["구분", "2024"], rows=[["매출", 1000], ["순이익", 100]]
                    )
                }
            )
        }
    )
    out = build_narrative_user_prompt(template, {}, {}, plan)
    assert "income" in out
    assert "매출" in out


def test_user_prompt_truncates_long_doc_text() -> None:
    """원자료 텍스트는 6000 chars 로 truncate (LLM 토큰 통제)."""
    template = _template()
    huge = "X" * 10000 + "TAIL_MARKER"
    raw = {"extracted_docs": [{"source_file": "big.xls", "text": huge}]}
    out = build_narrative_user_prompt(template, raw, {}, ReportPlan(sections={}))
    assert "TAIL_MARKER" not in out
    assert "big.xls" in out


def test_user_prompt_includes_sample_block_when_samples_provided() -> None:
    template = _template()
    out = build_narrative_user_prompt(
        template, {}, {}, ReportPlan(sections={}), samples=["문체 학습용 샘플 SAMPLE_TAG"]
    )
    assert "SAMPLE_TAG" in out
    assert "문체 학습용 sample 보고서" in out


def test_user_prompt_omits_sample_block_when_no_samples() -> None:
    template = _template()
    out = build_narrative_user_prompt(template, {}, {}, ReportPlan(sections={}), samples=[])
    assert "문체 학습용 sample 보고서" not in out


# ───────────────────── run_narrative_writer 동작 ─────────────────────


async def test_run_narrative_writer_propagates_valid_response() -> None:
    template = _template()
    valid = {
        "narratives": {
            "1_overview": "차주 개요 본문",
            "5_finance": "재무 현황 본문",
        }
    }
    anthropic = MagicMock()
    anthropic.complete_json = AsyncMock(return_value=valid)

    result = await run_narrative_writer(template, {}, {}, ReportPlan(sections={}), anthropic)
    assert result.narratives["1_overview"] == "차주 개요 본문"
    assert result.narratives["5_finance"] == "재무 현황 본문"
    # 누락 섹션은 ""로 보강돼 모든 spec.section_id 가 키로 존재
    section_ids = {spec.section_id.value for spec in template.sections}
    assert set(result.narratives.keys()) == section_ids


async def test_run_narrative_writer_falls_back_on_validation_error() -> None:
    """LLM 이 schema 어긋난 응답 (narratives 필드가 dict 아닌 list) 줄 때 빈 fallback."""
    template = _template()
    anthropic = MagicMock()
    # narratives 가 list 라 NarrativeMap 검증 실패
    anthropic.complete_json = AsyncMock(return_value={"narratives": ["wrong shape"]})

    result = await run_narrative_writer(template, {}, {}, ReportPlan(sections={}), anthropic)
    assert all(v == "" for v in result.narratives.values())


async def test_run_narrative_writer_falls_back_on_llm_exception() -> None:
    template = _template()
    anthropic = MagicMock()
    anthropic.complete_json = AsyncMock(side_effect=RuntimeError("LLM down"))

    result = await run_narrative_writer(template, {}, {}, ReportPlan(sections={}), anthropic)
    section_ids = {spec.section_id.value for spec in template.sections}
    assert set(result.narratives.keys()) == section_ids
    assert all(v == "" for v in result.narratives.values())


async def test_run_narrative_writer_called_exactly_once() -> None:
    template = _template()
    anthropic = MagicMock()
    anthropic.complete_json = AsyncMock(return_value={"narratives": {}})
    await run_narrative_writer(template, {}, {}, ReportPlan(sections={}), anthropic)
    anthropic.complete_json.assert_awaited_once()
