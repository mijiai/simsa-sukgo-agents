"""PR2 신규 동작 — financial agent 의 section_insights 출력 + extracted_tables 주입."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.agents.financial.prompts import build_user_prompt
from src.agents.financial.schemas import AnalyzeRequest, SectionInsight
from src.agents.financial.service import analyze_financials_service
from src.common.constants import ReportSection


def _now() -> datetime:
    return datetime(2026, 5, 4, 10, 0, tzinfo=UTC)


def _raw_payload(**overrides) -> dict:
    base = {
        "company_name": "ACME",
        "company_id": "c-1",
        "collected_at": _now().isoformat(),
        "news": [],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
        "extracted_tables": [],
        "extracted_images": [],
        "extracted_docs": [],
    }
    base.update(overrides)
    return base


def _judgment(*, section_insights: list[dict] | None = None) -> dict:
    base = {
        "risk_level": "MEDIUM",
        "risk_score": 55.0,
        "summary": "종합 분석",
        "key_risk_factors": [],
        "positive_signals": [],
        "data_gaps": [],
    }
    if section_insights is not None:
        base["section_insights"] = section_insights
    return base


def _make_deps(*, raw: dict | None = None, judgment: dict | None = None):
    raw = raw or _raw_payload()
    blob = MagicMock()
    blob.download = AsyncMock(return_value=json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    blob.upload = AsyncMock()

    tables = MagicMock()
    tables.jobs = MagicMock()
    tables.jobs.update_status = AsyncMock()
    tables.agent_status = MagicMock()
    tables.agent_status.update_running = AsyncMock()
    tables.agent_status.update_done = AsyncMock()
    tables.agent_status.update_failed = AsyncMock()

    anthropic = MagicMock()
    anthropic.model = "claude-haiku-4-5-20251001"
    anthropic.complete_json = AsyncMock(return_value=judgment or _judgment())
    return blob, tables, anthropic


# ---------- schemas ----------


def test_report_section_enum_has_nine_sections() -> None:
    """TODO_upgrade.md §5.2 — 9개 섹션 ID."""
    values = [s.value for s in ReportSection]
    assert values == [
        "1_overview",
        "2_loan_summary",
        "3_loan_terms",
        "4_business",
        "5_finance",
        "6_debt",
        "7_liquidity",
        "8_affiliates",
        "9_conclusion",
    ]


def test_section_insight_requires_at_least_one_bullet() -> None:
    with pytest.raises(ValidationError):
        SectionInsight(section_id=ReportSection.FINANCE, bullets=[])


def test_section_insight_caps_bullets_at_five() -> None:
    with pytest.raises(ValidationError):
        SectionInsight(
            section_id=ReportSection.FINANCE,
            bullets=[f"bullet{i}" for i in range(6)],
        )


def test_section_insight_table_id_optional_and_cited_data_default_empty() -> None:
    si = SectionInsight(section_id=ReportSection.FINANCE, bullets=["b1"])
    assert si.table_id is None
    assert si.cited_data_points == []


# ---------- service: section_insights propagation ----------


async def test_service_propagates_section_insights_to_result_json() -> None:
    judgment = _judgment(
        section_insights=[
            {
                "section_id": "5_finance",
                "table_id": "재무제표",
                "bullets": ["자산 6,040억 (전년비 +389억)", "당기순이익 106억"],
                "cited_data_points": ["자산총계 6,040억"],
            },
            {
                "section_id": "6_debt",
                "bullets": ["차입금 의존도 76% (전년 25%)"],
                "cited_data_points": [],
            },
        ]
    )
    blob, tables, anthropic = _make_deps(judgment=judgment)

    request = AnalyzeRequest(job_id="job-1")
    response = await analyze_financials_service(request, blob, tables, anthropic)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert len(payload["section_insights"]) == 2
    assert payload["section_insights"][0]["section_id"] == "5_finance"
    assert payload["section_insights"][0]["table_id"] == "재무제표"
    assert "자산 6,040억" in payload["section_insights"][0]["bullets"][0]
    assert payload["section_insights"][1]["section_id"] == "6_debt"
    assert payload["section_insights"][1]["table_id"] is None

    # AnalyzeResponse 의 카운트
    assert response.section_insights_count == 2


async def test_service_section_insights_default_empty_for_back_compat() -> None:
    """LLM 이 section_insights 빠진 응답 줘도 기존 분석은 정상 동작 (하위 호환)."""
    judgment = _judgment()  # section_insights 키 없음
    blob, tables, anthropic = _make_deps(judgment=judgment)

    request = AnalyzeRequest(job_id="job-2")
    response = await analyze_financials_service(request, blob, tables, anthropic)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["section_insights"] == []
    assert response.section_insights_count == 0
    # 기존 risk 필드는 변동 없음
    assert payload["risk_level"] == "MEDIUM"


async def test_service_invalid_section_id_marks_failed() -> None:
    """section_id 가 ReportSection enum 에 없는 값이면 ValidationError → failed."""
    judgment = _judgment(
        section_insights=[
            {"section_id": "99_unknown", "bullets": ["x"]},
        ]
    )
    blob, tables, anthropic = _make_deps(judgment=judgment)

    request = AnalyzeRequest(job_id="job-3")
    with pytest.raises(ValidationError):
        await analyze_financials_service(request, blob, tables, anthropic)

    tables.agent_status.update_failed.assert_awaited_once()


async def test_service_propagates_extracted_counts_to_input_summary() -> None:
    raw = _raw_payload(
        extracted_tables=[{"source_file": "a.xlsx"}, {"source_file": "b.xlsx"}],
        extracted_images=[{"source_file": "c.png"}],
    )
    blob, tables, anthropic = _make_deps(raw=raw)

    request = AnalyzeRequest(job_id="job-4")
    await analyze_financials_service(request, blob, tables, anthropic)

    payload = json.loads(blob.upload.call_args.args[1].decode("utf-8"))
    assert payload["input_summary"]["extracted_table_count"] == 2
    assert payload["input_summary"]["extracted_image_count"] == 1


# ---------- prompts: extracted_* injection ----------


def test_user_prompt_renders_extracted_tables_with_pipe_rows() -> None:
    raw = _raw_payload(
        extracted_tables=[
            {
                "source_file": "fin.xlsx",
                "sheet_name": "재무제표",
                "columns": ["구분", "2023", "2024"],
                "rows": [["자산총계", 1000, 1200], ["부채총계", 500, 600]],
                "truncated": False,
            }
        ]
    )
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[업로드 파일에서 추출된 표 — 1개]" in out
    assert "--- fin.xlsx / 재무제표 ---" in out
    assert "구분 | 2023 | 2024" in out
    assert "자산총계 | 1000 | 1200" in out
    assert "부채총계 | 500 | 600" in out


def test_user_prompt_truncates_tables_beyond_20_rows() -> None:
    rows = [["row" + str(i), i] for i in range(35)]
    raw = _raw_payload(
        extracted_tables=[
            {
                "source_file": "x.xlsx",
                "sheet_name": "S",
                "columns": ["A", "B"],
                "rows": rows,
            }
        ]
    )
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "row0 | 0" in out
    assert "row19 | 19" in out
    assert "row20 | 20" not in out
    assert "(이하 15행 생략)" in out


def test_user_prompt_caps_tables_at_eight() -> None:
    tables = [
        {"source_file": f"f{i}.xlsx", "columns": ["A"], "rows": [[f"v{i}"]]} for i in range(12)
    ]
    raw = _raw_payload(extracted_tables=tables)
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "f0.xlsx" in out
    assert "f7.xlsx" in out
    assert "f8.xlsx" not in out
    assert "(추가 4개 표 생략)" in out


def test_user_prompt_renders_extracted_docs_with_truncate() -> None:
    raw = _raw_payload(
        extracted_docs=[
            {"source_file": "report.pdf", "text": "본문 " + "X" * 3000, "page_count": 5}
        ]
    )
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[업로드된 PDF 문서 텍스트 — 1건]" in out
    assert "--- report.pdf (PDF, 5쪽) ---" in out
    assert "본문" in out
    # 2000자 truncate
    assert "X" * 2001 not in out


def test_user_prompt_renders_extracted_images_as_metadata() -> None:
    raw = _raw_payload(
        extracted_images=[
            {
                "source_file": "ownership_chart.png",
                "suspected_role": "ownership_chart",
                "width": 480,
                "height": 273,
            }
        ]
    )
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[업로드된 이미지 메타 — 1건]" in out
    assert "ownership_chart.png" in out
    assert "역할 추정: ownership_chart" in out
    assert "480x273" in out


def test_user_prompt_inserts_no_extracted_notices_when_empty() -> None:
    raw = _raw_payload()
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "추출된 표 없음" in out
    assert "추출된 PDF 텍스트 없음" in out
    assert "업로드된 이미지 없음" in out


def test_system_prompt_mentions_section_insights_and_enum_ids() -> None:
    from src.agents.financial.prompts import SYSTEM_PROMPT

    assert "section_insights" in SYSTEM_PROMPT
    assert "5_finance" in SYSTEM_PROMPT
    assert "6_debt" in SYSTEM_PROMPT
    assert "9_conclusion" in SYSTEM_PROMPT
