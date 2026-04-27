from src.agents.collector.filters import NEGATIVE_KEYWORDS, find_negative_keywords


def test_negative_keywords_dict_is_non_empty() -> None:
    assert len(NEGATIVE_KEYWORDS) > 10
    assert "소송" in NEGATIVE_KEYWORDS
    assert "부도" in NEGATIVE_KEYWORDS


def test_find_negative_keywords_no_match() -> None:
    assert find_negative_keywords("샘플기업, 신규 사업 진출 발표") == []


def test_find_negative_keywords_empty_text() -> None:
    assert find_negative_keywords("") == []


def test_find_negative_keywords_single_match() -> None:
    matched = find_negative_keywords("샘플기업, 부도설 확산")
    assert matched == ["부도"]


def test_find_negative_keywords_multiple_matches_preserve_order() -> None:
    matched = find_negative_keywords("소송 진행 중이며 횡령 의혹도 제기됨")
    assert matched.index("소송") < matched.index("횡령")
    assert "소송" in matched
    assert "횡령" in matched
