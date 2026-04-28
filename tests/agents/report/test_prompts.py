from src.agents.report.prompts import SYSTEM_PROMPT, build_user_prompt


def test_system_prompt_contains_style_directives() -> None:
    assert "샘플" in SYSTEM_PROMPT
    assert "data_gaps" in SYSTEM_PROMPT
    assert "Markdown" in SYSTEM_PROMPT
    assert "추가 확인사항" in SYSTEM_PROMPT


def test_user_prompt_serializes_raw_and_analysis() -> None:
    raw = {"company_name": "ACME", "news": []}
    analysis = {"risk_level": "MEDIUM", "risk_score": 50.0}
    out = build_user_prompt(raw=raw, analysis=analysis, templates=[])
    assert '"company_name": "ACME"' in out
    assert '"risk_level": "MEDIUM"' in out


def test_user_prompt_inlines_templates_separated_by_marker() -> None:
    out = build_user_prompt(raw={}, analysis={}, templates=["샘플 본문 1", "샘플 본문 2"])
    assert "=== 샘플 1 ===" in out
    assert "샘플 본문 1" in out
    assert "=== 샘플 2 ===" in out
    assert "샘플 본문 2" in out


def test_user_prompt_falls_back_when_no_templates() -> None:
    out = build_user_prompt(raw={}, analysis={}, templates=[])
    assert "샘플이 로드되지 않았습니다" in out
    assert "기업 개요" in out
    assert "리스크 요인" in out


def test_user_prompt_preserves_korean_in_json() -> None:
    raw = {"company_name": "카카오", "news": [{"title": "한글 뉴스"}]}
    out = build_user_prompt(raw=raw, analysis={}, templates=[])
    assert "카카오" in out
    assert "한글 뉴스" in out
