from src.agents.financial.prompts import SYSTEM_PROMPT, build_user_prompt


def test_system_prompt_specifies_json_only_response() -> None:
    assert "JSON" in SYSTEM_PROMPT
    assert "data_gaps" in SYSTEM_PROMPT
    assert "LOW" in SYSTEM_PROMPT
    assert "CRITICAL" in SYSTEM_PROMPT


def test_user_prompt_includes_company_name() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "ACME" in out


def test_user_prompt_marks_empty_sections() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "(수집된 뉴스 없음)" in out
    assert "(없음)" in out


def test_user_prompt_lists_news_with_date_and_title() -> None:
    raw = {
        "news": [
            {
                "title": "ACME 신사업 발표",
                "description": "본문 요약",
                "published_at": "2026-04-20T09:00:00+00:00",
            }
        ],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
    }
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[2026-04-20]" in out
    assert "ACME 신사업 발표" in out
    assert "본문 요약" in out


def test_user_prompt_truncates_long_news_list() -> None:
    raw = {
        "news": [
            {"title": f"뉴스{i}", "description": "x", "published_at": "2026-04-20T00:00:00+00:00"}
            for i in range(50)
        ],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
    }
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "뉴스0" in out
    assert "뉴스29" in out
    assert "뉴스30" not in out
    assert "최근 50건" in out


def test_user_prompt_includes_uploaded_files() -> None:
    raw = {
        "news": [],
        "lawsuits": [],
        "uploaded_files": ["사업계획서.pdf", "재무제표.xlsx"],
        "financial_years": [],
    }
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "사업계획서.pdf" in out
    assert "재무제표.xlsx" in out
