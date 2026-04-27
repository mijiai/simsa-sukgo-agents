NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "소송",
    "고소",
    "기소",
    "피소",
    "횡령",
    "배임",
    "비리",
    "뇌물",
    "부도",
    "파산",
    "회생",
    "워크아웃",
    "부실",
    "디폴트",
    "적자",
    "손실",
    "감자",
    "감액",
    "구속",
    "체포",
    "수사",
    "압수수색",
    "리콜",
    "결함",
    "불량",
    "분식회계",
    "회계조작",
    "조세포탈",
    "탈세",
    "제재",
    "징계",
    "과징금",
)


def find_negative_keywords(text: str) -> list[str]:
    if not text:
        return []
    return [kw for kw in NEGATIVE_KEYWORDS if kw in text]
