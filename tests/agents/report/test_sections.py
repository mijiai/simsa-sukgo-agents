"""sections.build_section_content — plan + financial.section_insights 매칭 검증."""

from src.agents.financial.schemas import SectionInsight
from src.agents.report.planner import PlannedImage, PlannedSection, PlannedTable
from src.agents.report.sections import build_section_content
from src.agents.report.template_spec import load_template
from src.common.constants import ReportSection


def setup_module() -> None:
    load_template.cache_clear()


def _spec(section_id: ReportSection):
    template = load_template("loan_application_v1")
    return next(s for s in template.sections if s.section_id == section_id)


def test_table_with_matching_insight_gets_bullets() -> None:
    spec = _spec(ReportSection.FINANCE)
    plan_section = PlannedSection(
        tables={
            "재무제표": PlannedTable(
                columns=["구분", "2023", "2024"], rows=[["자산총계", 1000, 1200]]
            )
        }
    )
    insights = [
        SectionInsight(
            section_id=ReportSection.FINANCE,
            table_id="재무제표",
            bullets=["자산 +200 (전년 대비)"],
            cited_data_points=["자산총계 1200"],
        )
    ]
    content = build_section_content(spec, plan_section, insights)
    fin_table = next(t for t in content.tables if t.table_id == "재무제표")
    assert fin_table.bullets == ["자산 +200 (전년 대비)"]
    assert fin_table.cited_data_points == ["자산총계 1200"]
    assert fin_table.columns == ["구분", "2023", "2024"]
    assert fin_table.data_gap is False


def test_table_without_matching_insight_has_empty_bullets() -> None:
    spec = _spec(ReportSection.FINANCE)
    plan_section = PlannedSection(tables={"재무제표": PlannedTable(columns=["A"], rows=[["x"]])})
    content = build_section_content(spec, plan_section, [])
    fin_table = next(t for t in content.tables if t.table_id == "재무제표")
    assert fin_table.bullets == []


def test_section_insight_with_no_table_id_collected_into_section_bullets() -> None:
    spec = _spec(ReportSection.CONCLUSION)
    plan_section = PlannedSection()
    insights = [
        SectionInsight(
            section_id=ReportSection.CONCLUSION,
            table_id=None,
            bullets=["종합 의견 1", "종합 의견 2"],
        )
    ]
    content = build_section_content(spec, plan_section, insights)
    assert content.tables == []
    assert content.section_bullets == ["종합 의견 1", "종합 의견 2"]


def test_missing_plan_table_falls_back_to_data_gap() -> None:
    spec = _spec(ReportSection.FINANCE)
    plan_section = PlannedSection()  # tables 비어있음
    content = build_section_content(spec, plan_section, [])
    for tbl in content.tables:
        assert tbl.data_gap is True


def test_source_label_override_takes_precedence_over_spec() -> None:
    spec = _spec(ReportSection.FINANCE)
    plan_section = PlannedSection(
        tables={
            "재무제표": PlannedTable(
                columns=["A"],
                rows=[["x"]],
                source_label_override="※ 수정 출처: planner override",
            )
        }
    )
    content = build_section_content(spec, plan_section, [])
    fin_table = next(t for t in content.tables if t.table_id == "재무제표")
    assert fin_table.source_label == "※ 수정 출처: planner override"


def test_image_slot_with_data_gap_true_propagates() -> None:
    spec = _spec(ReportSection.AFFILIATES)
    plan_section = PlannedSection(images={"지분 구조도": PlannedImage(data_gap=True)})
    content = build_section_content(spec, plan_section, [])
    img = next(i for i in content.images if i.image_id == "지분 구조도")
    assert img.data_gap is True
    assert img.blob_path is None


def test_image_slot_with_blob_path_set() -> None:
    spec = _spec(ReportSection.AFFILIATES)
    plan_section = PlannedSection(
        images={
            "지분 구조도": PlannedImage(
                blob_path="jobs/J/input/ownership.png",
                caption="지분 구조 안내",
            )
        }
    )
    content = build_section_content(spec, plan_section, [])
    img = next(i for i in content.images if i.image_id == "지분 구조도")
    assert img.blob_path == "jobs/J/input/ownership.png"
    assert img.caption == "지분 구조 안내"
    assert img.data_gap is False
