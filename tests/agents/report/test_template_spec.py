"""template_spec yaml 로드 + ReportSection enum 정합성 검증."""

import pytest

from src.agents.report.template_spec import ReportTemplate, load_template
from src.common.constants import ReportSection


def setup_module() -> None:
    load_template.cache_clear()


def test_loan_application_v1_loads_without_error() -> None:
    template = load_template("loan_application_v1")
    assert isinstance(template, ReportTemplate)
    assert template.name == "loan_application_v1"


def test_template_has_all_nine_sections_in_order() -> None:
    template = load_template("loan_application_v1")
    section_ids = [s.section_id for s in template.sections]
    assert section_ids == [
        ReportSection.OVERVIEW,
        ReportSection.LOAN_SUMMARY,
        ReportSection.LOAN_TERMS,
        ReportSection.BUSINESS,
        ReportSection.FINANCE,
        ReportSection.DEBT,
        ReportSection.LIQUIDITY,
        ReportSection.AFFILIATES,
        ReportSection.CONCLUSION,
    ]


def test_every_section_id_is_in_report_section_enum() -> None:
    """yaml 의 section_id 가 enum 에 없으면 ValidationError → 부팅 단계에서 발견."""
    template = load_template("loan_application_v1")
    enum_values = {s.value for s in ReportSection}
    for spec in template.sections:
        assert spec.section_id.value in enum_values


def test_each_table_has_required_metadata() -> None:
    template = load_template("loan_application_v1")
    for spec in template.sections:
        for tbl in spec.tables:
            assert tbl.table_id, f"section {spec.section_id} table missing id"
            assert tbl.title, f"table {tbl.table_id} missing title"


def test_load_template_unknown_name_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_template("does_not_exist_template")


def test_load_template_caches_same_instance() -> None:
    load_template.cache_clear()
    a = load_template("loan_application_v1")
    b = load_template("loan_application_v1")
    assert a is b
