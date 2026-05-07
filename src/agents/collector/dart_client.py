"""DART(전자공시시스템) API 클라이언트.

회사명 → corp_code 변환:
  DART 는 이름 검색 REST 엔드포인트가 없다.
  공식 방법은 corpCode.xml(ZIP) 전체 목록을 1회 다운로드 후 메모리에서 매칭.
  - 다운로드 URL: /api/corpCode.xml?crtfc_key=...
  - ZIP 내 CORPCODE.xml 에 전체 기업 (corp_code, corp_name, stock_code) 포함
  - DartClient 인스턴스 당 1회 다운로드 후 내부 캐시 (_corp_map) 사용

재무 데이터:
  fnlttSinglAcnt 단일 호출 → 당기/전기/전전기 3개년 한 번에 추출.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from typing import Any
from datetime import UTC, datetime

import httpx

from src.common.exceptions import ExternalApiError
from src.config.logging import get_logger

logger = get_logger(__name__)

REPRT_CODE_ANNUAL = "11011"
REPRT_CODE_HALF = "11012"
REPRT_CODE_Q1 = "11013"
REPRT_CODE_Q3 = "11014"

_PERIOD_COLS = [
    ("thstrm_nm", "thstrm_amount"),
    ("frmtrm_nm", "frmtrm_amount"),
    ("bfefrmtrm_nm", "bfefrmtrm_amount"),
]

_YEAR_RE = re.compile(r"(20\d{2})")

_STATUS_OK = "000"
_STATUS_NO_DATA = "013"


class DartApiError(ExternalApiError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__("dart", message, status_code=status_code)


_ACCOUNT_ID_MAP: dict[str, str] = {
    "ifrs-full_Assets": "total_assets",
    "ifrs_Assets": "total_assets",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs_Liabilities": "total_liabilities",
    "ifrs-full_Equity": "total_equity",
    "ifrs_Equity": "total_equity",
    "ifrs-full_CurrentAssets": "current_assets",
    "ifrs-full_NoncurrentAssets": "noncurrent_assets",
    "ifrs-full_CurrentLiabilities": "current_liabilities",
    "ifrs-full_NoncurrentLiabilities": "noncurrent_liabilities",
    "ifrs-full_Revenue": "revenue",
    "dart_Revenue": "revenue",
    "ifrs-full_GrossProfit": "gross_profit",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_parent",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cf",
    "ifrs-full_FinanceCosts": "finance_costs",
    "ifrs-full_InterestExpense": "interest_expense",
    "ifrs-full_DepreciationAndAmortisationExpense": "depreciation",
}

_ACCOUNT_NM_FALLBACK: dict[str, str] = {
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "유동자산": "current_assets",
    "비유동자산": "noncurrent_assets",
    "유동부채": "current_liabilities",
    "비유동부채": "noncurrent_liabilities",
    "매출액": "revenue",
    "영업수익": "revenue",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "영업활동으로인한현금흐름": "operating_cf",
    "영업활동현금흐름": "operating_cf",
    "이자비용": "interest_expense",
    "감가상각비": "depreciation",
    # 추가 매핑
    "자본금": "paid_in_capital",
    "이익잉여금": "retained_earnings",
    "이익잉여금(결손금)": "retained_earnings",
    "법인세차감전 순이익": "pretax_income",
    "법인세차감전순이익": "pretax_income",
    "법인세차감전 순이익(손실)": "pretax_income",
    "총포괄손익": "total_comprehensive_income",
    "총포괄손익(손실)": "total_comprehensive_income",
}

_VALID_ACCOUNT_FIELDS = frozenset({
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "noncurrent_assets",
    "current_liabilities", "noncurrent_liabilities",
    "revenue", "gross_profit", "operating_income",
    "net_income", "net_income_parent",
    "operating_cf", "finance_costs", "interest_expense", "depreciation",
    # 추가
    "paid_in_capital", "retained_earnings",
    "pretax_income", "total_comprehensive_income",
})

# corp_cls 우선순위 (유가증권 > 코스닥 > 코넥스 > 기타)
_CORP_CLS_PRIORITY = {"Y": 0, "K": 1, "N": 2, "E": 3}


def _parse_amount(val: str | None) -> int | None:
    if not val:
        return None
    cleaned = val.replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_year_from_nm(nm: str, base_year: int, offset: int) -> int:
    match = _YEAR_RE.search(nm)
    if match:
        return int(match.group(1))
    return base_year - offset


def _extract_accounts(items: list[dict[str, Any]], amount_key: str) -> dict[str, int | None]:
    accounts: dict[str, int | None] = {}
    for item in items:
        account_id = item.get("account_id", "")
        account_nm = item.get("account_nm", "").strip()
        key = _ACCOUNT_ID_MAP.get(account_id) or _ACCOUNT_NM_FALLBACK.get(account_nm)
        if not key or key not in _VALID_ACCOUNT_FIELDS or key in accounts:
            continue
        accounts[key] = _parse_amount(item.get(amount_key))
    return accounts


class DartClient:
    """DART OpenAPI 클라이언트."""

    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        if not api_key:
            raise DartApiError("api_key is empty")
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._owns_http = http is None
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        # corp_code 전체 목록 캐시: {corp_name: [(corp_code, corp_cls, stock_code), ...]}
        self._corp_map: dict[str, list[tuple[str, str, str]]] | None = None

    async def __aenter__(self) -> "DartClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ─── 내부 HTTP 헬퍼 ─────────────────────────────────────────────────────

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{endpoint}"
        merged = {"crtfc_key": self._api_key, **params}

        backoff = self._backoff_base
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._http.get(url, params=merged)
            except httpx.RequestError as exc:
                if attempt < self._max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise DartApiError(f"HTTP 요청 실패: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < self._max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise DartApiError(f"HTTP {resp.status_code}", status_code=resp.status_code)

            if resp.status_code != 200:
                raise DartApiError(f"HTTP {resp.status_code}", status_code=resp.status_code)

            data: dict[str, Any] = resp.json()
            status = data.get("status", "")
            if status == _STATUS_OK:
                return data
            if status == _STATUS_NO_DATA:
                return {"status": _STATUS_NO_DATA, "list": []}

            raise DartApiError(
                f"DART API 오류: status={status}, message={data.get('message', '(없음)')}"
            )

        raise DartApiError("재시도 루프 비정상 종료")

    # ─── corp_code 전체 목록 관리 ────────────────────────────────────────────

    async def _load_corp_map(self) -> dict[str, list[tuple[str, str, str]]]:
        """corpCode.xml ZIP 다운로드 → 회사명 기준 dict 구성.

        DART 에는 회사명으로 corp_code 를 검색하는 REST 엔드포인트가 없다.
        corpCode.xml 이 유일한 공식 방법으로, 전체 기업 목록(수만 건)을 ZIP으로 제공한다.

        Returns:
            {corp_name: [(corp_code, corp_cls, stock_code), ...]}
            동명이인 기업이 있을 수 있어 리스트로 저장.
        """
        url = f"{self.BASE_URL}/corpCode.xml"
        logger.info("dart.corp_map.downloading")
        try:
            resp = await self._http.get(url, params={"crtfc_key": self._api_key})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DartApiError(f"corpCode.xml 다운로드 실패: {exc}") from exc

        # ZIP → CORPCODE.xml 파싱
        try:
            with zipfile.ZipFile(BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise DartApiError(f"corpCode.xml ZIP 파싱 실패: {exc}") from exc

        root = ET.fromstring(xml_bytes)
        corp_map: dict[str, list[tuple[str, str, str]]] = {}
        for item in root.findall("list"):
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_cls = "Y" if stock_code else "E"  # stock_code 있으면 상장
            if corp_code and corp_name:
                corp_map.setdefault(corp_name, []).append((corp_code, corp_cls, stock_code))

        logger.info("dart.corp_map.loaded", total_companies=len(corp_map))
        return corp_map
    
    async def get_company_info(self, corp_code: str) -> dict[str, Any]:
        """기업개황 API — corp_code 로 회사 기본정보 조회.

        실패 시 빈 dict 반환 (collector 전체를 실패시키지 않음).
        반환 dict 의 주요 키:
            corp_name, corp_name_eng, ceo_nm, corp_cls, jurir_no, bizr_no,
            adres, hm_url, ir_url, phn_no, fax_no, induty_code,
            est_dt(YYYYMMDD), acc_mt(MM), stock_name, stock_code
        """
        try:
            data = await self._get("company.json", {"corp_code": corp_code})
        except DartApiError as exc:
            logger.warning("dart.company.api_error", corp_code=corp_code, error=str(exc))
            return {}

        if data.get("status") != _STATUS_OK:
            return {}

        # company.json 응답은 list 가 아니라 top-level 에 필드가 직접 들어있다.
        info = {
            k: v for k, v in data.items()
            if k not in {"status", "message"} and v not in (None, "")
        }
        logger.info(
            "dart.company.done",
            corp_code=corp_code,
            corp_name=info.get("corp_name"),
            field_count=len(info),
        )
        return info

    async def get_multi_year_accounts(
        self,
        corp_code: str,
        *,
        base_year: int | None = None,
    ) -> list[dict[str, Any]]:
        """다년도 주요계정 조회 — _fetch_dart_financials 가 호출.

        fnlttSinglAcnt 가 한 번 호출에 thstrm/frmtrm/bfefrmtrm 3개년을 반환한다.
        base_year 미지정 시 현재연도-1 (직전 사업보고서) → -2 순으로 fallback.
        """
        from datetime import datetime as _dt

        candidates = [base_year] if base_year else [_dt.now().year - 1, _dt.now().year - 2]
        for year in candidates:
            results = await self.get_key_accounts(corp_code, year)
            if any(r.get("has_data") for r in results):
                logger.info(
                    "dart.multi_year.done",
                    corp_code=corp_code,
                    base_year=year,
                    years_with_data=[r["year"] for r in results if r["has_data"]],
                )
                return results

        logger.info("dart.multi_year.no_data", corp_code=corp_code, tried=candidates)
        return []
    
    async def _get_corp_map(self) -> dict[str, list[tuple[str, str, str]]]:
        """캐시된 corp_map 반환. 없으면 다운로드."""
        if self._corp_map is None:
            self._corp_map = await self._load_corp_map()
        return self._corp_map

    # ─── 공개 메서드 ─────────────────────────────────────────────────────────

    async def search_corp_code(self, company_name: str) -> str | None:
        """회사명으로 corp_code 검색.

        corpCode.xml 전체 목록에서 정확히 일치하는 이름을 찾는다.
        동명이인이 있으면 유가증권(Y) → 코스닥(K) → 코넥스(N) → 기타 순으로 우선.

        정확히 일치하는 이름이 없으면 None 반환
        (부분 일치 검색은 의도치 않은 기업 선택 위험이 있어 미지원).
        """
        try:
            corp_map = await self._get_corp_map()
        except DartApiError as exc:
            logger.warning("dart.search_corp.map_load_failed", error=str(exc))
            return None

        candidates = corp_map.get(company_name)
        if not candidates:
            logger.info("dart.search_corp.not_found", company=company_name)
            return None

        # corp_cls 우선순위로 정렬
        best = sorted(candidates, key=lambda x: _CORP_CLS_PRIORITY.get(x[1], 99))
        corp_code, corp_cls, stock_code = best[0]

        logger.info(
            "dart.search_corp.found",
            company=company_name,
            corp_code=corp_code,
            corp_cls=corp_cls,
            stock_code=stock_code,
            total_matches=len(candidates),
        )
        return corp_code

    async def get_key_accounts(
        self,
        corp_code: str,
        year: int,
        reprt_code: str = REPRT_CODE_ANNUAL,
    ) -> list[dict[str, Any]]:
        """단일 API 호출로 당기/전기/전전기 3개년 데이터 반환.

        Returns:
            [
                {"year": year,   "period": "thstrm",    "accounts": {...}, "has_data": bool, ...},
                {"year": year-1, "period": "frmtrm",    "accounts": {...}, ...},
                {"year": year-2, "period": "bfefrmtrm", "accounts": {...}, ...},
            ]
        """
        try:
            data = await self._get(
                "fnlttSinglAcnt.json",
                {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code},
            )
        except DartApiError as exc:
            logger.warning(
                "dart.key_accounts.api_error",
                corp_code=corp_code, year=year, error=str(exc),
            )
            return []

        all_items: list[dict] = data.get("list") or []
        if not all_items:
            logger.info("dart.key_accounts.no_items", corp_code=corp_code, year=year)
            return []

        cfs = [i for i in all_items if i.get("fs_div") == "CFS"]
        ofs = [i for i in all_items if i.get("fs_div") == "OFS"]
        priority_items = cfs if cfs else ofs if ofs else all_items

        fs_div = priority_items[0].get("fs_div", "") if priority_items else ""
        currency = priority_items[0].get("currency", "KRW") if priority_items else "KRW"

        results: list[dict[str, Any]] = []
        for offset, (nm_key, amount_key) in enumerate(_PERIOD_COLS):
            period_nm = priority_items[0].get(nm_key, "") if priority_items else ""
            period_year = _parse_year_from_nm(period_nm, year, offset)
            accounts = _extract_accounts(priority_items, amount_key)
            has_data = any(v is not None for v in accounts.values())

            results.append({
                "year": period_year,
                "period": amount_key.split("_")[0],
                "period_nm": period_nm,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
                "currency": currency,
                "accounts": accounts,
                "has_data": has_data,
                "raw_items": priority_items if offset == 0 else [],
            })

        logger.info(
            "dart.key_accounts.done",
            corp_code=corp_code,
            base_year=year,
            years_with_data=[r["year"] for r in results if r["has_data"]],
        )
        return results