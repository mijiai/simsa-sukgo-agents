# CLAUDE.md — 심사숙고 MCP 서버 개발 메인 지침

> 이 파일은 Claude Code가 프로젝트를 시작할 때 가장 먼저 읽어야 하는 지침이다.
> 작업 전 아래 참조 문서를 반드시 확인하고, 모든 코드는 이 지침에 따라 작성한다.

---

## 참조 문서

모든 작업 전 아래 문서를 먼저 읽는다.

| 문서 | 경로 | 용도 |
|---|---|---|
| 개발 TODO | `/ref/TODO.md` | 전체 개발 순서 및 세부 작업 목록. 작업 착수 전 반드시 확인한다. |
| DB 설계서 | `/ref/DB_DESIGN.md` | Azure Storage 스키마, Agent 간 데이터 흐름, Blob 경로 규칙. Storage 관련 코드 작성 전 반드시 확인한다. |
| GitHub 지침 | `/ref/GitHub_Rules.md` | 브랜치 전략, Commit Message 규칙, PR 템플릿, 코드 컨벤션 전체. |

---

## 프로젝트 개요

- **서비스명** : 심사숙고 — 기업 심사 리포트 Agent
- **목적** : 기업여신 심사에서 자료 수집 · 재무 분석 · 보고서 작성 · 사후 모니터링을 Multi-Agent 구조로 자동화
- **서버** : Python + FastMCP (단일 컨테이너, Azure Container Apps 배포)
- **연동** : Claude.ai에 MCP를 추가 → Claude가 4개 Agent Tool을 자율 오케스트레이션

---

## 프로젝트 구조

코드를 생성하거나 파일을 추가할 때 반드시 아래 구조를 따른다.

```
simsa-sookgo/
├── CLAUDE.md                        ← 현재 파일
├── ref/
│   ├── TODO.md                      ← 개발 TODO
│   ├── DB_DESIGN.md                 ← DB/Storage 설계서
│   └── GitHub_Rules.md              ← GitHub 브랜치/커밋/컨벤션 지침
├── .github/
│   └── pull_request_template.md
├── src/
│   ├── main.py                      ← FastMCP 서버 진입점 (SSE transport)
│   ├── config/
│   │   ├── settings.py              ← 환경변수 로드, Azure 클라이언트 싱글톤
│   │   └── logging.py               ← structlog JSON 포맷 설정
│   ├── mcp/
│   │   ├── server.py                ← FastMCP 서버 생성 및 lifespan 훅
│   │   └── tool_registry.py         ← Agent별 Tool 일괄 등록
│   ├── agents/
│   │   ├── collector/               ← 자료 수집 Agent
│   │   │   ├── tools.py             ← MCP Tool 함수 (얇게)
│   │   │   ├── service.py           ← 비즈니스 로직 (두껍게)
│   │   │   ├── schemas.py           ← Pydantic 입출력 모델
│   │   │   └── clients.py           ← 외부 API 클라이언트
│   │   ├── financial/               ← 재무 분석 Agent
│   │   │   ├── tools.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── rules.py             ← 위험 등급 산출 기준
│   │   ├── report/                  ← 보고서 작성 Agent
│   │   │   ├── tools.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── templates.py         ← Markdown 보고서 섹션 템플릿
│   │   └── monitoring/              ← 사후관리 모니터링 Agent
│   │       ├── tools.py
│   │       ├── service.py
│   │       ├── schemas.py
│   │       └── scheduler.py         ← APScheduler 배치 Job
│   ├── storage/
│   │   ├── blob_store.py            ← Azure Blob 업로드/다운로드/SAS URL
│   │   └── table_store.py           ← Azure Table CRUD (10개 테이블 — Job/정형/모니터링/인프라)
│   └── common/
│       ├── exceptions.py            ← 공통 예외 클래스
│       ├── response.py              ← 공통 응답 포맷
│       ├── constants.py             ← RiskLevel 등 공통 상수
│       └── utils.py
├── tests/
│   ├── agents/
│   └── mcp/
├── .env.example
├── .gitignore
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 개발 순서 (반드시 이 순서를 따른다)

세부 항목은 `/ref/TODO.md`를 확인한다. 큰 흐름은 다음과 같다.

```
Step 0. 프로젝트 초기 세팅
  → FastMCP 서버 뼈대, config, logging, /health 엔드포인트, docker-compose

Step 1. Storage 모듈 구현
  → blob_store.py / table_store.py
  → Azure Table 테이블 10개, Blob 컨테이너 구조 (Azure SQL 미사용)
  → (상세 스키마는 /ref/DB_DESIGN.md 참조)

Step 2. create_analysis_job Tool
  → 분석 버튼 클릭 시 가장 먼저 호출
  → job_id 발급, AnalysisJobs INSERT, 파일 Blob 업로드, AgentStatus 초기화

Step 3. 자료 수집 Agent
  → collect_company_data(job_id, company_name)

Step 4. 재무 분석 Agent
  → analyze_financials(job_id)

Step 5. 보고서 작성 Agent
  → report_generate(job_id)

Step 6. 사후관리 모니터링 Agent
  → monitor_register / monitor_run_now / APScheduler 배치

Step 7. 통합 및 배포
  → Docker 빌드, ACR 업로드, Azure Container Apps 배포
```

---

## Agent 간 데이터 흐름 핵심 원칙

> **Agent 간에는 `job_id` 하나만 전달한다.**
> 대용량 JSON을 MCP Tool 응답에 담지 않는다.
> 각 Agent는 Storage에서 직접 읽고 쓴다.

```
create_analysis_job  →  job_id 반환
  └─► collect_company_data(job_id)   → Blob, Table 쓰기 → 경량 응답 반환
        └─► analyze_financials(job_id) → Blob, Table 읽기·쓰기 → 경량 응답 반환
              └─► report_generate(job_id) → Blob, Table 읽기 → SAS URL 반환
```

각 Tool의 경량 응답 형태:

```python
# collect 완료
{ "job_id": "...", "status": "collect_done", "news_count": 47, "financial_years": [...] }

# analyze 완료
{ "job_id": "...", "status": "analyze_done", "risk_level": "HIGH", "risk_score": 72.1 }

# report 완료
{ "job_id": "...", "status": "done", "report_url": "https://...?sas=...", "docx_url": "..." }
```

Blob 경로 규칙 (단일 컨테이너 `simsasukgo`):

```
jobs/{job_id}/input/         ← 업로드 파일 + prompt.txt
jobs/{job_id}/collect/       ← raw.json (Agent 1 출력)
jobs/{job_id}/analyze/       ← result.json (Agent 2 출력)
jobs/{job_id}/report/        ← report.md, report.docx (Agent 3 출력)
monitoring/{company_id}/{YYYYMMDD}/snapshot.json
credentials/gmail_oauth.json
```

---

## 코드 작성 핵심 규칙

### Tool은 얇게, Service는 두껍게

```python
# tools.py — 이렇게 작성한다
@mcp.tool()
async def collect_company_data(job_id: str, company_name: str) -> dict:
    """
    기업 자료를 수집합니다. 반환된 job_id를 analyze_financials에 그대로 전달하세요.

    사용 시점: create_analysis_job 호출 직후
    입력: job_id (create_analysis_job 반환값), company_name (기업명)
    출력: 경량 메타데이터 (job_id, 수집 건수 요약)
    """
    request = CollectRequest(job_id=job_id, company_name=company_name)
    service = CollectorService()
    result = await service.collect(request)
    return result.model_dump()

# service.py — 실제 로직은 여기에
class CollectorService:
    async def collect(self, request: CollectRequest) -> CollectResponse:
        ...
```

### Pydantic 모델 필수

```python
# schemas.py
class CollectRequest(BaseModel):
    job_id: str = Field(..., description="create_analysis_job에서 반환된 job_id")
    company_name: str = Field(..., min_length=1, description="분석할 기업명")

class CollectResponse(BaseModel):
    job_id: str
    status: str
    news_count: int
    lawsuit_count: int
    financial_years: list[int]
```

### 공통 응답 형식

```python
# response.py
{
    "success": True,
    "agent": "collector",
    "tool": "collect_company_data",
    "data": { ... },          # 실제 데이터 (경량)
    "warnings": [],
    "errors": [],
    "metadata": {
        "job_id": "...",
        "created_at": "2026-04-26T09:00:00+09:00"
    }
}
```

### 위험 등급 상수 (공통 사용)

```python
# constants.py
class RiskLevel:
    LOW      = "LOW"       # 정상
    MEDIUM   = "MEDIUM"    # 주의
    HIGH     = "HIGH"      # 경고 → Gmail 알림
    CRITICAL = "CRITICAL"  # 위험 → Gmail 알림 + 우선 확인 표시
```

### 공통 예외 클래스

```python
# exceptions.py
class SimsaSookgoError(Exception): ...
class ExternalApiError(SimsaSookgoError): ...      # 외부 API 실패
class StorageError(SimsaSookgoError): ...           # Azure Storage 실패
class ValidationError(SimsaSookgoError): ...        # 입력값 검증 실패
class ReportGenerationError(SimsaSookgoError): ...  # 보고서 생성 실패
```

### Tool 이름 규칙

```
create_*       → Job 초기화
collect_*      → 자료 수집 Agent
analyze_*      → 재무 분석 Agent
report_*       → 보고서 작성 Agent
monitor_*      → 모니터링 Agent
```

---

## Git 규칙 요약 (전체 규칙은 /ref/GitHub_Rules.md 참조)

### 브랜치 네이밍

```
feature/{agent}-{기능설명}     예) feature/collector-naver-news-api
fix/{대상}-{오류설명}           예) fix/report-null-input-error
refactor/{대상}-{내용}          예) refactor/agent-service-layer
docs/{내용}                    예) docs/db-design-update
test/{대상}-{내용}              예) test/financial-agent-unit-test
chore/{내용}                   예) chore/fastmcp-project-setup
```

### Commit Message 형식

```
{type}: {작업 내용 요약}

feat: Naver News API 수집 Tool 추가
fix: 빈 재무제표 입력 시 분석 중단 오류 수정
refactor: CollectorService 레이어 분리
docs: DB_DESIGN 스키마 설명 추가
test: 부채비율 계산 케이스 추가
chore: ruff 설정 추가
```

### 금지 사항

- `main` 브랜치에 직접 커밋 금지
- `.env` 파일 커밋 금지 (`.env.example`만 허용)
- API Key, DB 자격증명 등 민감정보 커밋 절대 금지
- 실제 고객정보, 개인정보 사용 금지 (더미 데이터만 사용)

---

## 환경변수 관리

코드에서 설정값을 하드코딩하지 않는다.
신규 환경변수 추가 시 `.env.example`도 반드시 함께 업데이트한다.

```env
# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=
AZURE_STORAGE_BLOB_CONTAINER=simsasukgo

# Azure AI Search
AZURE_SEARCH_ENDPOINT=
AZURE_SEARCH_API_KEY=
AZURE_SEARCH_INDEX_NAME=

# 외부 API
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
LAWSUIT_API_KEY=

# Gmail
GMAIL_CREDENTIALS_BLOB_PATH=credentials/gmail_oauth.json

# MCP 서버
MCP_HOST=0.0.0.0
MCP_PORT=8000

# 로깅
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## 보안 원칙

- 실제 고객정보, 개인정보 절대 사용 금지 → 더미/샘플 데이터만 사용
- API Key, Secret, Token 코드 내 하드코딩 금지
- 로그에 민감정보 출력 금지 (company_name은 샘플 데이터 기준만 허용)
- Tool 응답에 내부 스택트레이스 노출 금지
- Claude 프롬프트에 내부 비공개 로직 입력 금지

---

## 작업 시작 전 체크리스트

새 기능을 개발하기 전 다음을 확인한다.

- [ ] `/ref/TODO.md`에서 해당 항목의 선행 작업이 완료되었는지 확인
- [ ] `/ref/DB_DESIGN.md`에서 관련 Storage 스키마 확인
- [ ] `/ref/GitHub_Rules.md`에서 브랜치 타입 및 네이밍 확인
- [ ] `develop` 브랜치 최신화 후 기능 브랜치 생성
- [ ] `.env.example`에 신규 환경변수 반영 여부 확인
