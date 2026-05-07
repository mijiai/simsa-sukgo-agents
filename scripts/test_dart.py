"""DART 데이터 수집 로컬 검증 스크립트.

실제 DART API 를 호출해 corp_code 검색 → 주요계정 수집 → 파싱 결과를 출력한다.

사용법:
    uv run python scripts/test_dart.py                        # 기본 테스트 기업 목록
    uv run python scripts/test_dart.py --company 카카오       # 특정 기업 1개
    uv run python scripts/test_dart.py --company 삼성전자 --year 2023
    uv run python scripts/test_dart.py --corp-code 00126380   # corp_code 직접 지정
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (scripts/ 에서 src/ import 가능하게)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.agents.collector.dart_client import (  # noqa: E402
    REPRT_CODE_ANNUAL,
    DartClient,
)
from src.agents.collector.schemas import DartFinancialYear  # noqa: E402
from src.config.settings import get_settings  # noqa: E402

# ─── 출력 헬퍼 ──────────────────────────────────────────────────────────────

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}⚠{RESET}  {msg}")


def err(msg: str) -> None:
    print(f"{RED}✗{RESET} {msg}")


def header(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}")


def fmt_amount(val: int | None, unit: str = "억원") -> str:
    if val is None:
        return f"{RED}N/A{RESET}"
    eok = val / 1_0000_0000
    return f"{GREEN}{eok:>12,.1f} {unit}{RESET}"


# ─── 검증 함수 ──────────────────────────────────────────────────────────────


async def test_search_corp_code(client: DartClient, company_name: str) -> str | None:
    """회사명 → corp_code 검색 검증."""
    header(f"[1] corp_code 검색: {company_name}")
    corp_code = await client.search_corp_code(company_name)
    if corp_code:
        ok(f"corp_code 발견: {BOLD}{corp_code}{RESET}")
    else:
        err("corp_code 를 찾지 못했습니다.")
        print("   힌트: DART 등기부 공식 명칭과 일치해야 합니다.")
        print("         예) '카카오' (O)  /  '카카오톡' (X)")
    return corp_code


async def test_key_accounts(
    client: DartClient,
    corp_code: str,
    base_year: int,
    reprt_code: str = REPRT_CODE_ANNUAL,
) -> list[DartFinancialYear]:
    """주요계정 조회 및 파싱 검증."""
    reprt_label = {
        "11011": "사업보고서(연간)",
        "11012": "반기보고서",
        "11013": "1분기보고서",
        "11014": "3분기보고서",
    }.get(reprt_code, reprt_code)

    header(f"[2] 주요계정 조회: corp_code={corp_code}, base_year={base_year}, {reprt_label}")

    period_results = await client.get_key_accounts(corp_code, base_year, reprt_code)

    if not period_results:
        err("API 응답이 비어있습니다.")
        print("   힌트:")
        print("   - DART 미등록 기업이거나 해당 연도 보고서가 아직 미제출일 수 있습니다.")
        print(f"   - base_year 를 낮춰 보세요: --year {base_year - 1}")
        return []

    dart_years = [DartFinancialYear.from_dart_result(r) for r in period_results]

    for dy in dart_years:
        period_label = {"thstrm": "당기", "frmtrm": "전기", "bfefrmtrm": "전전기"}.get(
            dy.period, dy.period
        )
        fs_label = {"CFS": "연결", "OFS": "별도"}.get(dy.fs_div, dy.fs_div or "?")
        has_label = f"{GREEN}데이터 있음{RESET}" if dy.has_data else f"{RED}데이터 없음{RESET}"

        print(f"\n  {BOLD}{period_label} ({dy.year}년){RESET}  [{fs_label}재무제표]  {has_label}")
        if dy.period_nm:
            print(f"    기간명: {dy.period_nm}")

        if not dy.has_data:
            warn("  이 기간의 금액이 모두 None 입니다.")
            continue

        # 주요 계정 출력
        acct = dy.accounts
        rows = [
            ("자산총계", acct.total_assets),
            ("부채총계", acct.total_liabilities),
            ("자본총계", acct.total_equity),
            ("유동자산", acct.current_assets),
            ("유동부채", acct.current_liabilities),
            ("매출액", acct.revenue),
            ("영업이익", acct.operating_income),
            ("당기순이익", acct.net_income),
            ("영업활동현금흐름", acct.operating_cf),
            ("이자비용", acct.interest_expense or acct.finance_costs),
        ]
        for label, val in rows:
            print(f"    {label:<14}: {fmt_amount(val)}")

        # 재무 비율 계산
        _print_ratios(acct, dy.year)

    return dart_years


def _print_ratios(acct, year: int) -> None:
    """주요 재무비율 자동 계산 출력."""
    ratios: list[tuple[str, str]] = []

    if acct.total_liabilities and acct.total_equity:
        r = acct.total_liabilities / acct.total_equity * 100
        ratios.append(("부채비율", f"{r:.1f}%"))

    if acct.current_assets and acct.current_liabilities:
        r = acct.current_assets / acct.current_liabilities * 100
        ratios.append(("유동비율", f"{r:.1f}%"))

    if acct.operating_income is not None and acct.revenue:
        r = acct.operating_income / acct.revenue * 100
        ratios.append(("영업이익률", f"{r:.1f}%"))

    if acct.net_income is not None and acct.revenue:
        r = acct.net_income / acct.revenue * 100
        ratios.append(("순이익률", f"{r:.1f}%"))

    interest = acct.interest_expense or acct.finance_costs
    if acct.operating_income and interest:
        r = acct.operating_income / interest
        ratios.append(("이자보상배율", f"{r:.2f}배"))

    if ratios:
        print(f"\n    {BOLD}[{year}년 자동 계산 재무비율]{RESET}")
        for label, val in ratios:
            print(f"    {label:<14}: {CYAN}{val}{RESET}")


async def test_raw_item_sample(period_results: list[dict]) -> None:
    """raw_items 원본 항목 샘플 출력 — account_id 매핑 현황 + 전기/전전기 누락 진단."""
    header("[3] raw_items 진단 (매핑 현황 & 전기/전전기 금액 확인)")

    thstrm = next((r for r in period_results if r.get("period") == "thstrm"), None)
    if not thstrm or not thstrm.get("raw_items"):
        warn("raw_items 가 비어있어 진단을 건너뜁니다.")
        return

    from src.agents.collector.dart_client import _ACCOUNT_ID_MAP, _ACCOUNT_NM_FALLBACK

    items = thstrm["raw_items"]
    print(f"  전체 항목 수: {len(items)}개\n")

    # ── 매핑 현황 ──────────────────────────────────────────────────────────
    mapped, unmapped = [], []
    for item in items:
        aid = item.get("account_id", "")
        anm = item.get("account_nm", "").strip()
        key = _ACCOUNT_ID_MAP.get(aid) or _ACCOUNT_NM_FALLBACK.get(anm)
        row = (aid or "(없음)", anm, item, key)
        (mapped if key else unmapped).append(row)

    print(f"  {GREEN}매핑 성공 ({len(mapped)}개){RESET}")
    for aid, anm, item, key in mapped:
        print(f"    {key:<28} nm={anm!r}")

    if unmapped:
        print(f"\n  {YELLOW}매핑 미대응 ({len(unmapped)}개){RESET}")
        for aid, anm, item, _ in unmapped[:10]:
            print(f"    account_id={aid!r}  nm={anm!r}  금액={item.get('thstrm_amount', '')}")
        if len(unmapped) > 10:
            print(f"    ... 외 {len(unmapped) - 10}개")

    # ── 전기/전전기 금액 존재 여부 진단 ────────────────────────────────────
    # 2024/2023 has_data=False 원인: frmtrm/bfefrmtrm 금액이 실제로 비어있는지 확인
    print(f"\n  {BOLD}[전기·전전기 금액 원본값 확인]{RESET}")
    print(
        f"  {'계정명':<20}  {'당기(thstrm)':>20}  {'전기(frmtrm)':>20}  {'전전기(bfefrmtrm)':>20}"
    )
    print(f"  {'-' * 84}")
    for aid, anm, item, key in mapped:
        t = item.get("thstrm_amount") or "-"
        f = item.get("frmtrm_amount") or "-"
        b = item.get("bfefrmtrm_amount") or "-"
        f_color = GREEN if f != "-" else RED
        b_color = GREEN if b != "-" else RED
        print(f"  {anm:<20}  {t:>20}  {f_color}{f:>20}{RESET}  {b_color}{b:>20}{RESET}")

    # 결론 출력
    has_frmtrm = any(
        (item.get("frmtrm_amount") or "").strip() not in ("", "-")
        for _, _, item, key in mapped
        if key
    )
    has_bfefrmtrm = any(
        (item.get("bfefrmtrm_amount") or "").strip() not in ("", "-")
        for _, _, item, key in mapped
        if key
    )
    print()
    if has_frmtrm:
        ok("전기(frmtrm) 금액이 실제로 존재합니다 — 파싱 로직 문제일 수 있음")
    else:
        warn("전기(frmtrm) 금액이 API 응답에 없습니다 — DART 가 해당 연도를 미제공")
    if has_bfefrmtrm:
        ok("전전기(bfefrmtrm) 금액이 실제로 존재합니다 — 파싱 로직 문제일 수 있음")
    else:
        warn("전전기(bfefrmtrm) 금액이 API 응답에 없습니다 — DART 가 해당 연도를 미제공")


# ─── 메인 ───────────────────────────────────────────────────────────────────


def _get_key_for_item(item: dict) -> str:
    from src.agents.collector.dart_client import _ACCOUNT_ID_MAP, _ACCOUNT_NM_FALLBACK

    aid = item.get("account_id", "")
    anm = item.get("account_nm", "").strip()
    return _ACCOUNT_ID_MAP.get(aid) or _ACCOUNT_NM_FALLBACK.get(anm) or ""


async def debug_extract_accounts(period_results: list[dict]) -> None:
    """_extract_accounts 를 직접 호출해 기간별 파싱 결과 출력.

    has_data=False 인데 raw 금액이 있다고 진단됐을 때 파싱 실패 원인 특정.
    """
    header("[4] _extract_accounts 직접 디버그")

    thstrm = next((r for r in period_results if r.get("period") == "thstrm"), None)
    if not thstrm or not thstrm.get("raw_items"):
        warn("raw_items 없음.")
        return

    from src.agents.collector.dart_client import _extract_accounts, _parse_amount

    items = thstrm["raw_items"]

    for period_label, amount_key in [
        ("당기   (thstrm_amount)  ", "thstrm_amount"),
        ("전기   (frmtrm_amount)  ", "frmtrm_amount"),
        ("전전기 (bfefrmtrm_amount)", "bfefrmtrm_amount"),
    ]:
        accounts = _extract_accounts(items, amount_key)
        has_data = any(v is not None for v in accounts.values())
        status = f"{GREEN}has_data=True {RESET}" if has_data else f"{RED}has_data=False{RESET}"
        print(f"\n  {BOLD}{period_label}{RESET}  {status}  (keys={len(accounts)})")

        if not accounts:
            warn("  accounts 비어있음 — 매핑된 계정이 없거나 모두 필터됨")
            continue

        for key, val in accounts.items():
            raw_val = next(
                (item.get(amount_key, "") for item in items if _get_key_for_item(item) == key), "?"
            )
            if val is not None:
                print(f"    {GREEN}✓{RESET} {key:<28}: {val:>20,}  (raw={raw_val!r})")
            else:
                print(f"    {RED}✗{RESET} {key:<28}: {'None':>20}  (raw={raw_val!r})")

    # _parse_amount 단독 테스트
    print(f"\n  {BOLD}[_parse_amount 단독 테스트 — frmtrm 첫 번째 non-empty 값]{RESET}")
    sample = next(
        (item.get("frmtrm_amount") for item in items if item.get("frmtrm_amount")),
        None,
    )
    if sample:
        parsed = _parse_amount(sample)
        if parsed is not None:
            ok(f"_parse_amount({sample!r}) = {parsed:,}")
        else:
            err(f"_parse_amount({sample!r}) = None  ← 여기가 원인")
            # repr 로 숨겨진 특수문자 확인
            print(f"  repr(sample) = {sample!r}")
            print(f"  bytes = {sample.encode()!r}")
    else:
        warn("frmtrm_amount 값이 모두 비어있음")


async def run(
    company_name: str | None,
    corp_code: str | None,
    base_year: int,
    reprt_code: str,
) -> None:
    settings = get_settings()

    if not settings.dart_api_key:
        err("DART_API_KEY 가 .env 에 설정되어 있지 않습니다.")
        sys.exit(1)

    ok(f"DART_API_KEY 확인됨 (앞 8자리: {settings.dart_api_key[:8]}...)")
    print(f"  base_year={base_year}, reprt_code={reprt_code}")

    async with DartClient(api_key=settings.dart_api_key) as client:
        # corp_code 결정
        if corp_code:
            ok(f"corp_code 직접 지정: {corp_code}")
        elif company_name:
            corp_code = await test_search_corp_code(client, company_name)
            if not corp_code:
                sys.exit(1)
        else:
            err("--company 또는 --corp-code 중 하나를 지정하세요.")
            sys.exit(1)

        # 주요계정 조회
        period_results_raw = await client.get_key_accounts(corp_code, base_year, reprt_code)
        dart_years = [DartFinancialYear.from_dart_result(r) for r in period_results_raw]

        # 출력
        await test_key_accounts.__wrapped__ if hasattr(test_key_accounts, "__wrapped__") else None
        # 직접 출력 (test_key_accounts 재호출 대신 결과 재사용)
        header(f"[2] 주요계정 파싱 결과: corp_code={corp_code}")
        if not dart_years:
            err("파싱 결과가 없습니다.")
        else:
            for dy in dart_years:
                period_label = {"thstrm": "당기", "frmtrm": "전기", "bfefrmtrm": "전전기"}.get(
                    dy.period, dy.period
                )
                fs_label = {"CFS": "연결", "OFS": "별도"}.get(dy.fs_div, dy.fs_div or "?")
                has_label = (
                    f"{GREEN}데이터 있음{RESET}" if dy.has_data else f"{RED}데이터 없음{RESET}"
                )
                print(f"\n  {BOLD}{period_label} ({dy.year}년){RESET}  [{fs_label}]  {has_label}")
                if dy.period_nm:
                    print(f"    기간명: {dy.period_nm}")
                if dy.has_data:
                    acct = dy.accounts
                    for label, val in [
                        ("자산총계", acct.total_assets),
                        ("부채총계", acct.total_liabilities),
                        ("자본총계", acct.total_equity),
                        ("유동자산", acct.current_assets),
                        ("유동부채", acct.current_liabilities),
                        ("매출액", acct.revenue),
                        ("영업이익", acct.operating_income),
                        ("당기순이익", acct.net_income),
                        ("영업활동현금흐름", acct.operating_cf),
                        ("이자비용", acct.interest_expense or acct.finance_costs),
                    ]:
                        print(f"    {label:<14}: {fmt_amount(val)}")
                    _print_ratios(acct, dy.year)

        # raw_items 매핑 진단
        await test_raw_item_sample(period_results_raw)
        await debug_extract_accounts(period_results_raw)

    # 최종 요약
    header("요약")
    years_ok = [dy.year for dy in dart_years if dy.has_data]
    years_empty = [dy.year for dy in dart_years if not dy.has_data]
    if years_ok:
        ok(f"데이터 수신 연도: {years_ok}")
    if years_empty:
        warn(f"데이터 없는 연도: {years_empty}  (보고서 미제출 또는 해당 컬럼 없음)")
    if not dart_years:
        err("수집된 데이터가 없습니다. API 키·corp_code·base_year 를 확인하세요.")


def main() -> None:
    from datetime import datetime

    default_year = datetime.now().year - 1  # 직전 연도 (사업보고서 제출 완료 기준)

    parser = argparse.ArgumentParser(description="DART 데이터 수집 로컬 검증")
    parser.add_argument("--company", "-c", help="검색할 기업명 (예: 카카오)")
    parser.add_argument("--corp-code", help="DART 고유번호 직접 지정 (8자리)")
    parser.add_argument(
        "--year",
        "-y",
        type=int,
        default=default_year,
        help=f"기준 사업연도 (default: {default_year})",
    )
    parser.add_argument(
        "--reprt-code",
        "-r",
        default=REPRT_CODE_ANNUAL,
        choices=["11011", "11012", "11013", "11014"],
        help="보고서 종류 (default: 11011 사업보고서)",
    )
    args = parser.parse_args()

    if not args.company and not args.corp_code:
        # 인자 없이 실행 시 기본 예시 기업으로 테스트
        print(f"{BOLD}인자 없이 실행 — 기본 예시 기업(카카오)으로 테스트합니다.{RESET}")
        print("사용법: uv run python scripts/test_dart.py --company 기업명\n")
        args.company = "카카오"

    asyncio.run(
        run(
            company_name=args.company,
            corp_code=args.corp_code,
            base_year=args.year,
            reprt_code=args.reprt_code,
        )
    )


if __name__ == "__main__":
    main()
