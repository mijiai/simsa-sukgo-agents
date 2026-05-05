# 심사숙고 (Simsa Sukgo) — 기업 심사 리포트 Multi-Agent MCP 서버

> 기업여신 심사 업무(자료 수집 · 재무 분석 · 보고서 작성 · 사후 모니터링)를
> **4개 Agent + 단일 FastMCP 서버**로 자동화하는 PoC.
> Claude.ai 가 SSE 로 직접 연결해 4개 Agent Tool 을 자율 오케스트레이션한다.

- **스택**: Python 3.12 · FastMCP 3.x · Azure Container Apps · Azure Storage (Blob + Table) · Anthropic Claude (Haiku 4.5 / Sonnet 4.6)
- **개발 지침**: [CLAUDE.md](CLAUDE.md) — 코드 작성 규칙, 디렉터리 구조, 응답 포맷
- **개발 TODO**: [ref/TODO.md](ref/TODO.md) — Step 0~9 전체 진행 상태
- **DB 스키마**: [ref/DB_DESIGN.md](ref/DB_DESIGN.md) — Table 10개 + Blob prefix 규칙
- **Git 규칙**: [ref/GitHub_Rules.md](ref/GitHub_Rules.md)

---

## 아키텍처

```
Claude.ai
   │  SSE (HTTPS)
   ▼
[Azure Container Apps] (single replica, Always-on)
  └─ FastMCP 서버 (단일 컨테이너)
       ├─ create_analysis_job          ← Job 초기화
       ├─ Agent 1 자료 수집  collect_*
       ├─ Agent 2 재무 분석  analyze_*
       ├─ Agent 3 보고서     report_*
       └─ Agent 4 모니터링   monitor_*
            │
            ├─ Azure Blob   (jobs/{job_id}/..., monitoring/{company_id}/...)
            ├─ Azure Table  (10개 — Job/정형/모니터링/인프라)
            └─ APScheduler  (3개월 주기 배치 + 재시작 보상 실행)
```

**Agent 간 데이터 흐름의 핵심 원칙**: Agent 끼리는 **`job_id` 하나만 전달**한다.
대용량 JSON 은 MCP Tool 응답에 담지 않고, 각 Agent 가 Storage 에서 직접 읽고 쓴다.

```
create_analysis_job  →  job_id
  └─► collect_company_data(job_id)   → Blob/Table 쓰기
        └─► analyze_financials(job_id) → Blob/Table 읽기·쓰기
              └─► report_generate(job_id) → SAS URL 반환
                    └─► (옵션) monitor_register(origin_job_id=job_id)
```

---

## MCP Tool 카탈로그

| # | Tool | Agent | 입력 | 출력 (경량) |
|---|---|---|---|---|
| 0 | `create_upload_url` | Job 초기화 | `filename`, `content_type?` | `upload_url`, `blob_path` |
| 1 | `create_analysis_job` | Job 초기화 | `company_name`, `files[]`, `file_blob_paths[]`, `prompt` | `job_id` |
| 2 | `collect_company_data` | Collector | `job_id`, `company_name` | `news_count`, `financial_years` |
| 3 | `analyze_financials` | Financial | `job_id` | `risk_level`, `risk_score` |
| 4 | `report_generate` | Report | `job_id` | `report_url` (SAS, 7일) |
| 5 | `monitor_register` | Monitoring | `company_id`, `recipient_email` | 등록 결과 |
| 6 | `monitor_deregister` | Monitoring | `company_id` | soft delete 결과 |
| 7 | `monitor_list` | Monitoring | — | 활성 대상 목록 |
| 8 | `monitor_run_now` | Monitoring | `company_id` | 즉시 1회 재분석 + 알림 판정 |

각 Tool 은 `tools.py` 에서 얇게 등록되고 비즈니스 로직은 `service.py` 에 둔다 (CLAUDE.md 규칙).

### artifact 직접 업로드 흐름 (대용량 파일)

`create_analysis_job(files=[...])` 의 base64 inline 경로는 LLM 출력 토큰을
거치므로 ~50KB 가 실용 한계 (Sonnet 출력 64K 토큰 / base64 1KB ≈ 350 토큰).
큰 첨부는 artifact 가 직접 Azure Blob 으로 PUT 하고 경로만 넘긴다:

```js
// 1) 업로드 SAS URL 발급 (Claude → MCP)
const { upload_url, blob_path, required_headers } =
  await callMcp("create_upload_url", { filename: file.name, content_type: file.type });

// 2) artifact 가 직접 PUT (Claude 우회 — 출력 토큰 0)
await fetch(upload_url, { method: "PUT", headers: required_headers, body: file });

// 3) 분석 시작 (Claude → MCP, base64 없음)
await callMcp("create_analysis_job", {
  company_name: "...", file_blob_paths: [blob_path],
});
```

배포 후 1회: `bash scripts/setup_storage_cors.sh` 실행해 Storage Account
CORS 룰을 등록해야 브라우저 PUT 이 통과한다. default 는 `claude.ai`,
`*.claude.ai`, `simsasukgo-frontend.vercel.app`, `*.vercel.app`. 다른 도메인이
필요하면 `CORS_ORIGINS` 환경변수로 override (공백 또는 콤마 구분):

```bash
CORS_ORIGINS="https://my-frontend.example.com,https://*.preview.example.com" \
  bash scripts/setup_storage_cors.sh
```

---

## 디렉터리 구조

```
src/
  main.py                         FastMCP SSE 진입점
  config/{settings,logging}.py    pydantic-settings + structlog JSON
  mcp/{server,job_tools}.py       FastMCP lifespan + create_analysis_job
  agents/
    collector/                    Naver News + Companies 조회 → raw.json
    financial/                    Claude Haiku 4.5 → result.json
    report/                       Claude Sonnet 4.6 → report.md (SAS URL)
    monitoring/                   register/run_now + APScheduler + Gmail 알림
  storage/
    blob_store.py                 Blob upload/download/SAS
    table_store.py                10개 Table repository
  common/                         예외, 응답, 상수
scripts/
  deploy_azure.sh                 idempotent ACA 수동 배포
  init_storage.py                 Blob 컨테이너 + Table 생성
  gmail_oauth_consent.py          Gmail OAuth 1회용 consent
.github/workflows/ci.yml          test (모든 push/PR) + deploy (main 만)
ref/                              개발 지침 3종 (TODO/DB/GitHub)
tests/                            agents/storage/mcp 단위 테스트
```

---

## 로컬 실행

```bash
# 1) 의존성
uv sync

# 2) 환경변수 (.env.example 참고)
cp .env.example .env
# AZURE_STORAGE_CONNECTION_STRING / NAVER_CLIENT_ID/SECRET / ANTHROPIC_API_KEY 채우기

# 3) Storage 초기화 (1회만 — 컨테이너 + Table 10개)
uv run python scripts/init_storage.py

# 4) 서버 기동
uv run python -m src.main
```

기본 포트는 `8000`, MCP SSE 엔드포인트는 `/sse`, 헬스체크는 `/health`.

### 로컬 → Claude.ai 연결 (개발 중)

```bash
ngrok http 8000
# Claude.ai → Settings → Connectors → Add custom MCP
# URL: https://<ngrok-domain>/sse
```

---

## Azure 배포

### 자동 배포 (권장)

`main` 으로 push/merge 하면 GitHub Actions 가 자동으로:

1. `test` job — ruff lint + format check + pytest
2. `deploy` job — `az acr build` 클라우드 빌드 → ACA secret/registry/image 갱신 → `/health` 6회 retry

워크플로우: [.github/workflows/ci.yml](.github/workflows/ci.yml)

필요한 GitHub Secrets:

| Secret | 설명 |
|---|---|
| `AZURE_CREDENTIALS` | Service Principal JSON (`az ad sp create-for-rbac --sdk-auth`) |
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage Account 연결 문자열 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | Naver Search API |

### 수동 배포 (Azure 리소스 최초 생성 시)

```bash
./scripts/deploy_azure.sh
```

스크립트가 RG / ACR / Storage / ACA Environment / Container App 을 idempotent 하게 생성한다.
환경변수 + secret 매핑은 스크립트 상단 참고.

### Claude.ai 연결 (배포 후)

Claude.ai → Settings → Connectors → Add custom MCP

```
https://<aca-fqdn>/sse
```

---

## Gmail 알림 (모니터링 Agent)

위험 등급이 **MEDIUM 이상으로 *상승 진입* 할 때만** Gmail 발송 (90일 dedup).

```bash
# 1회용 OAuth consent (로컬 브라우저 → token.json → Azure Blob 자동 업로드)
uv run python scripts/gmail_oauth_consent.py
```

요구 환경변수: `GMAIL_CREDENTIALS_BLOB_PATH` (default `credentials/gmail_oauth.json`).
서버는 런타임에 Blob 에서 token 을 로드하고 google-auth 가 refresh_token 으로 자동 갱신한다.

알림 트리거 조건은 [src/agents/monitoring/alerter.py](src/agents/monitoring/alerter.py) `should_alert` 참고.

---

## 데모 시나리오

Claude.ai 에서 자연어 한 줄로 4-Agent 오케스트레이션:

> "카카오 분석해줘. 첨부한 재무제표랑 같이 봐줘."

1. Claude → `create_analysis_job(company_name="카카오", files=[...])` → `job_id`
2. Claude → `collect_company_data(job_id, "카카오")` → 뉴스 수집
3. Claude → `analyze_financials(job_id)` → 위험 등급 산출
4. Claude → `report_generate(job_id)` → 보고서 SAS URL
5. (옵션) Claude → `monitor_register(company_id="...", recipient_email="...")`

---

## 기술 스택 상세

| 영역 | 선택 | 이유 |
|---|---|---|
| MCP 서버 | FastMCP 3.x (SSE) | Claude.ai 가 SSE 직접 연결 지원 |
| LLM | Claude Haiku 4.5 (분석) / Sonnet 4.6 (보고서) | 분석은 빠르게, 보고서는 글빨 |
| Storage | Azure Blob + Table | Azure SQL 대신 Table 10개로 통합 (PoC 단순화) |
| 배포 | Azure Container Apps (min=1) | Always-on 스케줄러 + 자동 스케일 |
| CI/CD | GitHub Actions + Service Principal | OIDC 는 운영 시점 마이그레이션 |
| 빌드 | `az acr build` (클라우드) | 로컬 Docker 불필요 |
| 패키지 | uv | lock + sync 속도 |
| 코드 품질 | ruff (lint + format) | mypy 는 후속 |

---

## 보류 / 후속 항목

PoC 범위에서 의도적으로 빠진 항목은 [ref/TODO.md](ref/TODO.md) 에 `[~]` 로 표시:

- **1-1-4** 더미 재무 데이터 적재 — 사용자 결정 대기 (27테이블 vs 종합 PDF)
- **1-3** 소송자료 API — 외부 API 미선정
- **2-1** Azure AI Search 인덱스 — 샘플 부족으로 Blob 캐시로 대체
- **3-3-6** DOCX 변환 — Markdown 만 우선
- **5-3-1** Azure Key Vault — Secrets 는 ACA secret 로 대체
- **5-3-2** MCP SSE 인증 — Bearer/Azure AD 는 운영 단계
- **8-7** mypy CI 통합 — ruff 만 우선

향후 고도화는 [ref/TODO.md §9-7](ref/TODO.md) 참고.

---

## 라이선스

Agentic Coding Challenge 제출용 PoC. 별도 라이선스 미설정.
