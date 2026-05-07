"""로컬에서 실행 중인 FastMCP 서버에 SSE 로 연결하여
4개 Agent Tool 을 순차 호출해 기업 분석을 끝까지 돌리는 클라이언트 스크립트.

전제:
- 다른 터미널에서 `uv run python -m src.main` 으로 서버가 떠 있어야 한다
- `.env` 의 ANTHROPIC_API_KEY / NAVER_* / AZURE_STORAGE_* 가 채워져 있어야 한다
- 첨부 파일 없이 외부 수집 데이터만으로 분석한다 (--file 옵션으로 첨부 가능)

사용:
    uv run python scripts/run_company_analysis.py --company 삼성전자
    uv run python scripts/run_company_analysis.py --company 삼성전자 --server http://localhost:8000/sse
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from fastmcp import Client


def _print_step(title: str) -> None:
    print(f"\n{'=' * 70}\n>>> {title}\n{'=' * 70}", flush=True)


def _extract_payload(result: Any) -> dict:
    """fastmcp Client.call_tool() 반환의 structured payload 를 dict 로 정규화."""
    structured = getattr(result, "structured_content", None) or getattr(result, "data", None)
    if isinstance(structured, dict):
        return structured

    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    if isinstance(result, dict):
        return result
    raise RuntimeError(f"Tool 응답을 dict 로 해석하지 못했습니다: {result!r}")


async def _call(client: Client, name: str, args: dict) -> dict:
    print(f"  → call_tool({name}, {args})", flush=True)
    raw = await client.call_tool(name, args)
    payload = _extract_payload(raw)
    print(f"  ← {json.dumps(payload, ensure_ascii=False, indent=2)}", flush=True)
    return payload


async def run(company: str, server_url: str, custom_prompt: str | None) -> int:
    async with Client(server_url) as client:
        _print_step(f"Step 1. create_analysis_job  (company={company})")
        create_args: dict[str, Any] = {"company_name": company}
        if custom_prompt:
            create_args["custom_prompt"] = custom_prompt
        created = await _call(client, "create_analysis_job", create_args)
        job_id = created["job_id"]
        print(f"\n  job_id = {job_id}", flush=True)

        _print_step("Step 2. collect_company_data")
        await _call(
            client,
            "collect_company_data",
            {"job_id": job_id, "company_name": company},
        )

        _print_step("Step 3. analyze_financials")
        await _call(client, "analyze_financials", {"job_id": job_id})

        _print_step("Step 4. report_generate")
        report = await _call(client, "report_generate", {"job_id": job_id})

        _print_step("완료")
        md_url = report.get("md_url") or report.get("report_url")
        docx_url = report.get("docx_url")
        if md_url:
            print(f"  보고서(MD)   : {md_url}", flush=True)
        if docx_url:
            print(f"  보고서(DOCX) : {docx_url}", flush=True)
        print(f"  job_id       : {job_id}", flush=True)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FastMCP SSE client — 기업 분석 end-to-end 실행")
    parser.add_argument("--company", required=True, help="분석할 기업명 (예: 삼성전자)")
    parser.add_argument(
        "--server",
        default="http://localhost:8000/sse",
        help="FastMCP SSE 엔드포인트 (default: http://localhost:8000/sse)",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="custom_prompt (선택)",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.company, args.server, args.prompt))
    except KeyboardInterrupt:
        print("\n[중단됨]", flush=True)
        return 130
    except Exception as exc:
        print(f"\n[오류] {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())