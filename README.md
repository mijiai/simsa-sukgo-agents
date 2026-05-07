# 심사숙고 (Simsa Sukgo) — 기업 심사 리포트 Multi-Agent MCP 서버

> 기업여신 심사 업무(자료 수집 · 재무 분석 · 보고서 작성 · 사후 모니터링)를
> **4개 Agent + 단일 FastMCP 서버**로 자동화하는 시스템.
> Claude.ai 가 SSE 로 직접 연결해 12개 MCP Tool 을 자율 오케스트레이션한다.

- **스택**: Python 3.12 · FastMCP 3.x · Azure Container Apps · Azure Storage (Blob + Table) · Anthropic Claude (Haiku 4.5 / Sonnet 4.6) · DART OpenAPI
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
       ├─ Job 초기화    create_upload_url / create_analysis_job / list / detail
       ├─ Agent 1 자료 수집    collect_company_data
       │    ├─ Naver News API
       │    ├─ DART OpenAPI (기업개황 · 3개년 재무)
       │    └─ 업로드 파일 추출 (PDF / Excel / 이미지)
       ├─ Agent 2 재무 분석    analyze_financials
       │    └─ Claude Haiku 4.5 → 위험 등급 4단계 + 섹션별 분석
       ├─ Agent 3 보고서    report_generate
       │    └─ Claude Sonnet 4.6 → Planner + Narrative Writer → DOCX + MD
       └─ Agent 4 모니터링    monitor_*
            └─ APScheduler 배치 (3개월 주기) + Gmail 알림
                 │
                 ├─ Azure Blob   (jobs/{job_id}/..., monitoring/{company_id}/...)
                 └─ Azure Table  (10개 — Job / 정형 / 모니터링 / 인프라)
```

**Agent 간 데이터 흐름의 핵심 원칙**: Agent 끼리는 **`job_id` 하나만 전달**한다.
대용량 JSON 은 MCP Tool 응답에 담지 않고, 각 Agent 가 Storage 에서 직접 읽고 쓴다.

```
create_analysis_job  →  job_id
  └─► collect_company_data(job_id)    → Blob/Table 쓰기  (raw.json)
        └─► analyze_financials(job_id)  → Blob/Table 읽기·쓰기  (result.json)
              └─► report_generate(job_id) → DOCX + MD → SAS URL 반환
                    └─► (옵션) monitor_register(origin_job_id=job_id)
```

---

## MCP Tool 카탈로그

| # | Tool | 역할 | 주요 입력 | 출력 (경량) |
|---|---|---|---|---|
| 0 | `create_upload_url` | 대용량 파일 업로드용 SAS URL 발급 | `filename`, `content_type?` | `upload_url`, `blob_path` |
| 1 | `create_analysis_job` | 분석 Job 초기화 | `company_name`, `file_blob_paths[]`, `prompt?` | `job_id` |
| 2 | `list_analysis_jobs` | Job 목록 조회 | `status?`, `limit`, `offset` | `jobs[]`, `total` |
| 3 | `get_analysis_job_detail` | Job 상세 조회 | `job_id` | 메타 + agents + collect/analyze/report 발췌 + SAS URLs |
| 4 | `collect_company_data` | 자료 수집 Agent | `job_id`, `company_name` | `news_count`, `dart_financial_years`, `extracted_table_count` |
| 5 | `analyze_financials` | 재무 분석 Agent | `job_id` | `risk_level`, `risk_score`, `section_insights_count` |
| 6 | `report_generate` | 보고서 작성 Agent | `job_id` | `report_url` (SAS, 7일), `docx_url` |
| 7 | `monitor_register` | 모니터링 등록 | `company_id`, `recipient_email` | 등록 결과 |
| 8 | `monitor_deregister` | 모니터링 해제 | `company_id` | soft delete 결과 |
| 9 | `monitor_list` | 모니터링 목록 조회 | — | 활성 대상 목록 (경량) |
| 10 | `monitor_run_now` | 즉시 재분석 + 알림 판정 | `company_id` | 위험 등급 + 판단 근거 4 필드 |
| 11 | `monitor_get_latest_snapshot` | 최근 모니터링 결과 조회 | `company_id` | snapshot 전체 |

각 Tool 은 `tools.py` 에서 얇게 등록되고 비즈니스 로직은 `service.py` 에 둔다 ([CLAUDE.md](CLAUDE.md) 규칙).

### 대용량 파일 직접 업로드 흐름

`create_analysis_job` 의 inline 방식은 LLM 출력 토큰을 거치므로 50 KB 가 실용 한계.
큰 첨부 파일은 artifact 가 직접 Azure Blob 으로 PUT 하고 경로만 넘긴다:

```js
// 1) 업로드 SAS URL 발급
const { upload_url, blob_path, required_headers } =
  await callMcp("create_upload_url", { filename: file.name, content_type: file.type });

// 2) artifact 가 직접 PUT (Claude 우회 — 출력 토큰 0)
await fetch(upload_url, { method: "PUT", headers: required_headers, body: file });

// 3) 분석 시작 (base64 없음)
await callMcp("create_analysis_job", {
  company_name: "...", file_blob_paths: [blob_path],
});
```

배포 후 1회: `bash scripts/setup_storage_cors.sh` 를 실행해 Storage Account CORS 룰을 등록해야 브라우저 PUT 이 통과한다.
기본 허용 origin: `claude.ai`, `*.claude.ai`, `*.vercel.app`, `http://localhost:3000`.
다른 도메인은 `CORS_ORIGINS` 환경변수로 override (공백 또는 콤마 구분):

```bash
CORS_ORIGINS="https://my-frontend.example.com" bash scripts/setup_storage_cors.sh
```

---

## 디렉터리 구조

```
src/
  main.py                          FastMCP SSE 진입점
  config/
    settings.py                    pydantic-settings 환경변수 로드 + 싱글톤
    logging.py                     structlog JSON 포맷 설정
  mcp/
    server.py                      FastMCP 서버 생성 + lifespan 훅
    job_tools.py                   Job 관련 Tool 4개 등록
  agents/
    collector/
      tools.py                     collect_company_data Tool 등록
      service.py                   수집 비즈니스 로직
      schemas.py                   CollectRequest / CollectResponse 등
      clients.py                   NaverNewsClient
      dart_client.py               DART OpenAPI 클라이언트 (corp_code 캐시 + 재무 조회)
      extractors.py                PDF / Excel / 이미지 파일 추출
      factory.py                   DartClient 등 의존성 팩토리
      internal_db.py               내부 신용 DB 조회
    financial/
      tools.py                     analyze_financials Tool 등록
      service.py                   Claude Haiku 호출 + 위험 등급 산출
      schemas.py
      prompts.py                   시스템 프롬프트 + 입력 빌더
      rules.py                     위험 등급 기준
    report/
      tools.py                     report_generate Tool 등록
      service.py                   Planner → Narrative Writer → 렌더링 파이프라인
      schemas.py
      planner.py                   ReportPlan LLM (Sonnet)
      narrative.py                 섹션별 서술형 글쓰기 LLM (Sonnet)
      renderer.py                  DOCX + Markdown 렌더러
      sections.py                  SectionContent 조립
      templates.py                 보고서 섹션 YAML 템플릿 로더
    monitoring/
      tools.py                     monitor_* Tool 5개 등록
      service.py                   재분석 + 스냅샷 저장 + 알림 판정
      schemas.py
      scheduler.py                 APScheduler 배치 Job (3개월 주기)
      alerter.py                   Gmail 알림 발송 조건 판정
  storage/
    blob_store.py                  Blob upload / download / SAS URL
    table_store.py                 Table 10개 repository
    schemas.py                     Table 엔티티 Pydantic 모델
  common/
    exceptions.py                  공통 예외 클래스
    response.py                    공통 응답 포맷
    constants.py                   RiskLevel 등 공통 상수
    anthropic_client.py            Anthropic SDK 래퍼
scripts/
  deploy_azure.sh                  idempotent ACA 수동 배포
  init_storage.py                  Blob 컨테이너 + Table 10개 생성
  setup_storage_cors.sh            Storage Account CORS 등록
  gmail_oauth_consent.py           Gmail OAuth 1회용 consent → Blob 업로드
tests/
  agents/                          Agent 단위 테스트
  mcp/                             MCP Tool 단위 테스트
ref/
  TODO.md                          개발 TODO (Step 0~9)
  DB_DESIGN.md                     Table 스키마 + Blob prefix 규칙
  GitHub_Rules.md                  브랜치 / 커밋 / PR 컨벤션
.github/workflows/ci.yml           test (모든 push/PR) + deploy (main 만)
```

---

## 로컬 실행

### 1. 의존성 설치

```bash
uv sync
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 에서 아래 항목을 채운다:

| 변수 | 필수 | 설명 |
|---|:---:|---|
| `AZURE_STORAGE_CONNECTION_STRING` | ✅ | Azure Storage Account 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✅ | Claude API 키 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✅ | Naver Search API 자격증명 |
| `DART_API_KEY` | — | DART OpenAPI 키. 없으면 DART 연동 skip (Naver + 업로드 파일로만 분석) |
| `DART_FETCH_YEARS_BACK` | — | 조회 연도 수 (default: 3) |
| `GMAIL_CREDENTIALS_BLOB_PATH` | — | Gmail OAuth token Blob 경로 (모니터링 알림 사용 시) |

> **DART API 키 발급**: [DART OpenAPI](https://opendart.fss.or.kr/intro/main.do) → OpenAPI 신청 (무료)

### 3. Storage 초기화 (최초 1회)

```bash
uv run python scripts/init_storage.py
```

Blob 컨테이너 `simsasukgo` 와 Azure Table 10개를 생성한다.

### 4. 서버 기동

```bash
uv run python -m src.main
```

| 엔드포인트 | 설명 |
|---|---|
| `GET /health` | 헬스체크 |
| `GET /sse` | MCP SSE 엔드포인트 (Claude.ai 연결 대상) |

### 5. Claude.ai 연결 (로컬 개발)

```bash
ngrok http 8000
# Claude.ai → Settings → Connectors → Add custom MCP
# URL: https://<ngrok-domain>/sse
```

---

## Azure 배포

### 자동 배포 (권장)

`main` 으로 push / merge 하면 GitHub Actions 가 자동으로:

1. **test job** — `ruff check` + `ruff format --check` + `pytest`
2. **deploy job** — `az acr build` 클라우드 빌드 → ACA secret / registry / image 갱신 → `/health` 6회 retry

워크플로우: [.github/workflows/ci.yml](.github/workflows/ci.yml)

필요한 GitHub Secrets:

| Secret | 설명 |
|---|---|
| `AZURE_CREDENTIALS` | Service Principal JSON (`az ad sp create-for-rbac --sdk-auth`) |
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage Account 연결 문자열 |
| `NAVER_CLIENT_ID` | Naver Search API Client ID |
| `NAVER_CLIENT_SECRET` | Naver Search API Client Secret |
| `DART_API_KEY` | DART OpenAPI 키 (선택 — 없으면 DART 연동 skip) |

### 수동 배포 (Azure 리소스 최초 생성 시)

```bash
./scripts/deploy_azure.sh
```

스크립트가 Resource Group / ACR / Storage / ACA Environment / Container App 을 idempotent 하게 생성한다.

### Claude.ai 연결 (배포 후)

Claude.ai → Settings → Connectors → Add custom MCP

```
https://<aca-fqdn>/sse
```

---

## Gmail 알림 설정 (모니터링 Agent)

위험 등급이 **MEDIUM 이상으로 *상승 진입* 할 때만** Gmail 발송 (90일 dedup).

```bash
# 1회용 OAuth consent (로컬 브라우저 → token.json → Azure Blob 자동 업로드)
uv run python scripts/gmail_oauth_consent.py
```

- 환경변수 `GMAIL_CREDENTIALS_BLOB_PATH` (default: `credentials/gmail_oauth.json`)
- 서버가 런타임에 Blob 에서 token 을 로드하고, `google-auth` 가 `refresh_token` 으로 자동 갱신
- 알림 트리거 조건: [src/agents/monitoring/alerter.py](src/agents/monitoring/alerter.py) `should_alert` 참고

---

## 데모 시나리오

Claude.ai 에서 자연어 한 줄로 4-Agent 오케스트레이션:

> "카카오 분석해줘. 첨부한 재무제표랑 같이 봐줘."

1. Claude → `create_analysis_job(company_name="카카오", file_blob_paths=[...])` → `job_id`
2. Claude → `collect_company_data(job_id, "카카오")` → Naver 뉴스 + DART 재무 + 파일 추출
3. Claude → `analyze_financials(job_id)` → 위험 등급 (LOW / MEDIUM / HIGH / CRITICAL) + 섹션별 분석
4. Claude → `report_generate(job_id)` → DOCX + Markdown SAS URL (7일)
5. (옵션) Claude → `monitor_register(company_id="...", recipient_email="...")` → 3개월 주기 모니터링 등록

---

## 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| MCP 서버 | FastMCP 3.x (SSE) | Claude.ai 가 SSE 직접 연결 지원 |
| LLM — 분석 | Claude Haiku 4.5 | 빠른 구조화 출력, 비용 효율 |
| LLM — 보고서 | Claude Sonnet 4.6 | 서술형 글쓰기 품질 |
| 재무 데이터 | DART OpenAPI | 공식 전자공시 3개년 재무 자동 수집 (선택적) |
| Storage | Azure Blob + Table | Azure SQL 대신 Table 10개로 통합 (PoC 단순화) |
| 배포 | Azure Container Apps (min=1) | Always-on 스케줄러 + 자동 스케일 |
| CI/CD | GitHub Actions + Service Principal | ruff lint/test + `az acr build` + ACA 배포 |
| 패키지 관리 | uv | lock + sync 속도 |
| 코드 품질 | ruff (lint + format) | E / F / I / B / UP 룰셋 |
| 로깅 | structlog (JSON) | 구조화 로그, Azure Monitor 연동 용이 |

---

## 환경변수 전체 목록

`.env.example` 기준 전체 목록. 필수 항목만 채워도 서버는 기동된다.

```bash
# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=       # ← 필수
AZURE_STORAGE_BLOB_CONTAINER=simsasukgo

# Azure AI Search (현재 미사용 — 향후 확장용)
AZURE_SEARCH_ENDPOINT=
AZURE_SEARCH_API_KEY=
AZURE_SEARCH_INDEX_NAME=

# 외부 API
NAVER_CLIENT_ID=                       # ← 필수
NAVER_CLIENT_SECRET=                   # ← 필수
LAWSUIT_API_KEY=                       # 소송자료 API (미선정 — 예약)

# DART 전자공시 (선택 — 없으면 자동 skip)
DART_API_KEY=
DART_FETCH_YEARS_BACK=3               # 조회 연도 수 (역산, 1~10)
DART_REPRT_CODE=11011                 # 11011=사업보고서 / 11012=반기 / 11013=1분기 / 11014=3분기

# Anthropic
ANTHROPIC_API_KEY=                     # ← 필수
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_MAX_TOKENS=8192
FINANCIAL_SAMPLES_BLOB_PREFIX=templates/financial_samples/

# 보고서 작성 Agent
REPORT_MODEL=claude-sonnet-4-6
REPORT_MAX_TOKENS=8192
REPORT_SAS_EXPIRY_HOURS=168           # 보고서 SAS URL 유효 기간 (시간)
REPORT_SAMPLES_BLOB_PREFIX=templates/report_samples/
REPORT_BASE_DOCX_BLOB_PATH=           # base .docx Blob 경로 (헤더/푸터 스타일 상속, 선택)
APPENDIX_ROW_THRESHOLD=35             # 이 행 수 초과 표는 자동으로 별첨 분리

# 이미지 캡션용 Vision (default off — 비용 발생)
IMAGE_VISION_ENABLED=false
IMAGE_VISION_MODEL=claude-haiku-4-5-20251001

# Artifact 업로드 SAS
UPLOAD_SAS_EXPIRY_MINUTES=15
UPLOAD_BLOB_PREFIX=uploads/

# Gmail 알림
GMAIL_CREDENTIALS_BLOB_PATH=credentials/gmail_oauth.json
GMAIL_SENDER_ADDRESS=                 # From: 표시용 (비어있으면 OAuth 계정 사용)

# 모니터링 알림 트리거
ALERT_MIN_RISK_LEVEL=MEDIUM           # 이 등급 이상 상승 진입 시 발송
ALERT_DEDUP_DAYS=90                   # 같은 (company_id, risk_level) 중복 차단 기간
ALERT_FIRST_RUN_SEND=true             # 첫 실행 시 등급 초과이면 즉시 발송

# 모니터링 스케줄러
MONITORING_BATCH_CRON=0 9 1 */3 *    # UTC 기준 cron (default: 매 3개월 1일 09:00)
MONITORING_CATCHUP_THRESHOLD_DAYS=90  # 컨테이너 재시작 시 보상 실행 임계값
MONITORING_SCHEDULER_ENABLED=true

# MCP 서버
MCP_HOST=0.0.0.0
MCP_PORT=8000

# 로깅
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## 보류 / 후속 항목

PoC 범위에서 의도적으로 빠진 항목:

| 항목 | 상태 |
|---|---|
| 소송자료 API 연동 | 외부 API 미선정 — `LAWSUIT_API_KEY` 예약만 |
| Azure AI Search 인덱스 | 샘플 부족으로 Blob 캐시로 대체 중 |
| MCP SSE 인증 (Bearer / Azure AD) | 운영 단계 적용 예정 |
| Azure Key Vault 마이그레이션 | 현재 ACA Secret 으로 대체 |
| mypy CI 통합 | ruff 만 우선, strict mypy 는 후속 |

---

## 라이선스

Internal use. 별도 라이선스 미설정.
