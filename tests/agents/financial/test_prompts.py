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


def test_user_prompt_inserts_no_samples_notice_when_empty() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    out = build_user_prompt(company_name="ACME", raw=raw, samples=[])
    assert "샘플이 로드되지 않았습니다" in out


def test_user_prompt_injects_samples_with_index_headers() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    samples = ["부채비율 200% — HIGH", "유동비율 80% — MEDIUM"]
    out = build_user_prompt(company_name="ACME", raw=raw, samples=samples)
    assert "=== 샘플 1 ===" in out
    assert "부채비율 200% — HIGH" in out
    assert "=== 샘플 2 ===" in out
    assert "유동비율 80% — MEDIUM" in out


def test_user_prompt_truncates_huge_sample() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    huge = "A" * 5000 + "TAIL_MARKER"
    out = build_user_prompt(company_name="ACME", raw=raw, samples=[huge])
    assert "TAIL_MARKER" not in out
    assert "A" * 100 in out


def test_user_prompt_inserts_no_internal_credit_notice_when_missing() -> None:
    raw = {"news": [], "lawsuits": [], "uploaded_files": [], "financial_years": []}
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[분석 대상 기업 재무·신용 데이터 — 내부 DB]" in out
    assert "내부 신용 DB 에 등록된 기업이 아닙니다" in out


def test_user_prompt_injects_internal_credit_data_when_present() -> None:
    raw = {
        "news": [],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
        "internal_credit_data": "부채비율 | 720.81 | 68.9 | 31.11\n신용등급 | CCC+",
    }
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "[분석 대상 기업 재무·신용 데이터 — 내부 DB]" in out
    assert "부채비율 | 720.81 | 68.9 | 31.11" in out
    assert "신용등급 | CCC+" in out
    assert "내부 신용 DB 에 등록된 기업이 아닙니다" not in out


def test_user_prompt_truncates_huge_internal_credit_data() -> None:
    raw = {
        "news": [],
        "lawsuits": [],
        "uploaded_files": [],
        "financial_years": [],
        "internal_credit_data": "B" * 9000 + "INTERNAL_TAIL",
    }
    out = build_user_prompt(company_name="ACME", raw=raw)
    assert "INTERNAL_TAIL" not in out
    assert "B" * 100 in out
