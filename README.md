# 심사숙고 — 기업 심사 리포트 Agent

기업여신 심사 업무(자료 수집 · 재무 분석 · 보고서 작성 · 사후 모니터링)를 Multi-Agent 구조로 자동화하는 FastMCP 서버.

상세 개발 지침은 [CLAUDE.md](CLAUDE.md), 전체 TODO는 [ref/TODO.md](ref/TODO.md)를 참고한다.

## 로컬 실행

```bash
uv sync
cp .env.example .env  # 값 채우기
uv run python -m src.main
```

기본 포트는 `8000`이며, MCP SSE 엔드포인트는 `/sse`, 헬스체크는 `/health`.

> Step 0 (프로젝트 초기 세팅) 단계. 상세 README는 Step 9에서 보완 예정.
