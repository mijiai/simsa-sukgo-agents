# 심사숙고 : 기업 심사 리포트 Agent — MCP 서버 개발 TODO

> **스택** : Python · FastMCP · Docker · Azure Container Apps · Azure Storage  
> **배포 전략** : 단일 Docker 컨테이너 → Azure Portal 배포 / SSE transport로 Claude.ai 연동  
> **아키텍처** : 하나의 FastMCP 서버에 4개 Agent Tool을 모두 등록, Claude.ai가 자율 오케스트레이션

```
Claude.ai
   │  SSE (HTTPS)
   ▼
[Azure Container Apps]
  └─ FastMCP 서버 (단일 컨테이너)
       ├─ Agent 1 : 자료 수집 Tools       (collect_*)
       ├─ Agent 2 : 재무 분석 Tools       (analyze_*)
       ├─ Agent 3 : 보고서 작성 Tools     (report_*)
       └─ Agent 4 : 사후관리 모니터링 Tools (monitor_*)
            │
            ├─ Azure Blob Storage    (문서·보고서 파일)
            ├─ Azure Table Storage   (Job 생명주기·재무 정형 데이터·기업 마스터·모니터링·스케줄러)
            └─ Azure AI Search       (벡터 DB — 유사 사례 검색)
```

---

## 0. 프로젝트 초기 세팅

- [x] 0-1. 레포지토리 구조 설계
  ```
  /agents        # Agent별 Tool 구현
  /tools         # 공통 유틸 (API 클라이언트, 스토리지 헬퍼)
  /storage       # Azure Storage 연동 모듈 (Blob / Table / AI Search)
  /prompts       # 시스템 프롬프트 · Few-shot 템플릿
  /scheduler     # APScheduler 배치 Job
  /tests         # 단위 · 통합 테스트
  Dockerfile
  docker-compose.yml   # 로컬 개발용
  .env.example
  main.py
  config.py
  ```
- [x] 0-2. `pyproject.toml` / `requirements.txt` 작성 및 의존성 버전 고정
  - 핵심 패키지 : `fastmcp`, `httpx`, `apscheduler`,
    `azure-storage-blob`, `azure-data-tables`, `azure-search-documents`,
    `pydantic`, `pydantic-settings`, `structlog`,
    `google-auth`, `google-api-python-client`
- [x] 0-3. `.env.example` 스키마 정의
  ```
  # Azure Storage
  AZURE_STORAGE_CONNECTION_STRING=
  AZURE_STORAGE_BLOB_CONTAINER=
  # Azure AI Search
  AZURE_SEARCH_ENDPOINT=
  AZURE_SEARCH_API_KEY=
  AZURE_SEARCH_INDEX_NAME=
  # 외부 API
  NAVER_CLIENT_ID=
  NAVER_CLIENT_SECRET=
  LAWSUIT_API_KEY=
  # Gmail
  GMAIL_CREDENTIALS_BLOB_PATH=
  # MCP 서버
  MCP_HOST=0.0.0.0
  MCP_PORT=8000
  # 로깅
  LOG_LEVEL=INFO
  LOG_FORMAT=json
  ```
- [x] 0-4. `config.py` 중앙 설정 모듈 작성 (환경변수 로드, Azure 클라이언트 싱글톤)
- [x] 0-5. 로깅 설정 (`structlog` JSON 포맷) — Azure Monitor / Application Insights 연동 고려
- [x] 0-6. FastMCP 서버 엔트리포인트 (`main.py`) 뼈대 작성
  - transport : **SSE** (`mcp.run(transport="sse", host=MCP_HOST, port=MCP_PORT)`)
  - `lifespan` 훅에 Azure Storage 연결 확인 · APScheduler 시작/종료 등록
  - `lifespan` 기동 시 `AgentStatus` 테이블에서 `status=running` 잔존 Job 감지 → 해당 Agent부터 자동 재개 (컨테이너 재시작 복구)
- [x] 0-7. `/health` 엔드포인트 추가 (Azure Container Apps 헬스체크용, `GET /health → 200 OK`)
- [x] 0-8. `docker-compose.yml` 로컬 개발 환경 구성 (`.env` 마운트, 포트 포워딩 8000:8000)
- [ ] 0-9. 로컬 Docker 기동 후 Claude.ai MCP SSE 연결 동작 확인 (ngrok 터널 활용)
- [x] 0-10. **Job 생성 + 파일 업로드 초기화 Tool** 구현 (`create_analysis_job`)
  - 사용자가 분석 버튼을 누를 때 가장 먼저 호출되는 Tool
  - `AnalysisJobs` Table INSERT (status=pending), `AnalysisJobsRef` Table INSERT (PK=company_id)
  - 첨부 파일 → `jobs/{job_id}/input/{filename}` Blob 업로드
  - 커스텀 프롬프트 → `jobs/{job_id}/input/prompt.txt` Blob 저장
  - `AgentStatus` 테이블에 collect / analyze / report 3개 행 초기화 (status=pending)
  - 반환값 : `{ "job_id": "...", "status": "ready" }` — 이후 모든 Agent가 이 `job_id`를 수신하여 사용

---

## 1. 자료 수집 Agent

> 기업명 입력 → 내부 DB(Azure Storage) · Naver News API · 소송자료 API 병렬 호출 → 원시 데이터 반환

### 1-1. Azure Storage 모듈 구현

> Azure SQL / CosmosDB는 사용하지 않는다. 정형 데이터도 모두 Table Storage로 통합.

- [x] 1-1-1. **Azure Blob Storage** 연결 모듈 작성 (`/src/storage/blob_store.py`, `azure-storage-blob.aio`)
  - 파일 업로드 / 다운로드 함수 구현
  - SAS 토큰 생성 함수 (User Delegation Key 우선, fallback Account Key)
  - Container 자동 생성 (idempotent)
- [x] 1-1-2. **Azure Table Storage** 연결 모듈 작성 (`/src/storage/table_store.py`, `azure-data-tables.aio`)
  - 테이블별 typed repository 패턴
  - 운영 테이블 : `AnalysisJobs`, `AgentStatus`
  - 정형 테이블 : `Companies`, `FinancialRaw`, `FinancialMetrics`, `AnalysisJobsRef`
  - 모니터링 테이블 : `MonitoringTargets`, `MonitoringSnapshots`, `AlertHistory`
  - 인프라 테이블 : `SchedulerState`
  - 테이블 자동 생성 (idempotent), PartitionKey / RowKey 규칙은 `/ref/DB_DESIGN.md` §2 준수
- [x] 1-1-3. Pydantic 스키마 정의 (`schemas.py`) — 각 테이블 행 ↔ 모델 매핑
- [ ] 1-1-4. 더미/샘플 데이터셋 적재 스크립트 (`scripts/seed_dummy_data.py`)
  - `Companies` 더미 기업 N개
  - `FinancialRaw` 더미 재무 행 (실 고객정보 미사용)
  - 로컬 검증용

### 1-2. Naver News API 연동

- [x] 1-2-1. Naver Search API 클라이언트 모듈 작성 (`httpx` 비동기)
- [x] 1-2-2. 기업명 기반 뉴스 검색 함수 구현 (최신순, 페이지네이션)
- [x] 1-2-3. 뉴스 결과 파싱 및 정규화 (제목 · 본문 · 날짜 · URL 추출)
- [~] 1-2-4. ~~부정 키워드 필터링 로직 구현 (소송, 횡령, 부도, 적자 등)~~
  → collector는 raw 수집만 담당. 부정/위험 판단은 분석 Agent의 Claude LLM (Step 2-2-4) 에서 처리.
- [x] 1-2-5. API Rate Limit 처리 및 재시도 로직 (exponential backoff)

### 1-3. 소송자료 API 연동

- [ ] 1-3-1. 소송자료 API 클라이언트 모듈 작성 (엔드포인트 · 인증 방식 확인)
- [ ] 1-3-2. 기업 식별자 기반 소송 조회 함수 구현
- [ ] 1-3-3. 소송 결과 정규화 스키마 작성 (사건번호 · 사건명 · 상태 · 금액)
- [ ] 1-3-4. 소송 없음 / 조회 실패 예외 케이스 처리

### 1-4. FastMCP Tool 등록 — 자료 수집

- [x] 1-4-1. `collect_company_data(job_id: str, company_name: str)` Tool 정의 및 description 작성
  - `job_id`는 `create_analysis_job` 반환값을 그대로 수신
- [x] 1-4-2. Agent 시작 시 `AgentStatus[collect]` status=running, `AnalysisJobs` status=collecting 업데이트
- [~] 1-4-3. ~~`jobs/{job_id}/input/*` Blob에서 업로드 파일 다운로드 및 파싱 (PDF/DOCX/XLSX OCR 포함)~~
  → 파일명 메타만 raw.json에 기록. 실제 파싱(PDF/DOCX/XLSX OCR)은 후속 별도 PR로 분리.
- [~] 1-4-4. ~~Companies Table 조회 · Naver News · 소송자료 병렬 호출 (`asyncio.gather`) 구현~~
  → 현재는 Companies + Naver News 만 (직렬 호출). 소송자료는 1-3 보류 상태. 병렬화는 자료 추가 후.
- [x] 1-4-5. `Companies` Table 조회 (find_by_name, UPSERT는 `create_analysis_job`에서 수행)
- [~] 1-4-6. ~~수집 결과 → `FinancialRaw` Table INSERT (PartitionKey=job_id, 연도별 행)~~
  → 1-1-4 더미 재무 데이터 보류 상태로 소스 없음. 더미 적재 후 별도 PR.
- [x] 1-4-7. 수집 결과 전체 → `jobs/{job_id}/collect/raw.json` Blob PUT
- [x] 1-4-8. `AgentStatus[collect]` status=done, `output_blob_path` 기록
- [x] 1-4-9. 경량 응답 포맷 Pydantic 모델 정의 (job_id + 메타 요약만 반환)
- [x] 1-4-10. Tool 단위 테스트 작성 (Azure SDK mock + 외부 API mock)

---

## 2. 재무 분석 Agent

> 자료 수집 결과 + Azure AI Search(벡터 DB) + 분석 지침 → 재무 건전성 · 위험 요인 인사이트 반환

### 2-1. Azure AI Search 기반 학습 DB 구성

- [ ] 2-1-1. Azure AI Search 인덱스 스키마 설계
  - 필드 : `id`, `company_type`, `content`, `embedding`, `source_type`, `year`
  - 업종별 벤치마크 · 재무 분석 예시 · 판례 문서 대상
- [ ] 2-1-2. 임베딩 생성 파이프라인 구현 (Azure OpenAI Embeddings 또는 외부 모델)
- [ ] 2-1-3. 더미 재무 분석 예시 문서 청킹 및 Azure AI Search 색인 적재 스크립트 작성
- [ ] 2-1-4. 유사 사례 검색 함수 구현 (`azure-search-documents` SDK, hybrid search)

### 2-2. 분석 지침(Prompt) 설계

- [ ] 2-2-1. 재무 분석 시스템 프롬프트 작성 (`/prompts/financial_analysis.md`)
- [ ] 2-2-2. 주요 재무 지표 자동 계산 로직 구현 (부채비율, 유동비율, 영업이익률, 이자보상배율 등)
- [ ] 2-2-3. 정형 데이터 분석 프롬프트 (수치 비교 · 추이 서술 템플릿)
- [ ] 2-2-4. 비정형 데이터 분석 프롬프트 (뉴스 · 소송 위험 신호 탐지 템플릿)
- [ ] 2-2-5. 위험 등급 산출 기준 정의 (정상 / 주의 / 경고 / 위험)

### 2-3. FastMCP Tool 등록 — 재무 분석

- [ ] 2-3-1. `analyze_financials(job_id: str)` Tool 정의
  - `job_id`만 수신, Blob(`jobs/{job_id}/collect/raw.json`)과 Table(`FinancialRaw` PK=job_id)에서 직접 데이터 로드
- [ ] 2-3-2. Agent 시작 시 `AgentStatus[analyze]` status=running, `AnalysisJobs` status=analyzing 업데이트
- [ ] 2-3-3. `FinancialRaw` Table query (PK=job_id) + Blob `raw.json` GET → 분석 입력 구성
- [ ] 2-3-4. 재무 지표 계산 및 업종 벤치마크 비교 로직 구현
- [ ] 2-3-5. Azure AI Search 유사 사례 검색 결과 컨텍스트 주입
- [ ] 2-3-6. 분석 인사이트 생성 (Claude API 호출 또는 Tool 내 로직)
- [ ] 2-3-7. 분석 결과 → `FinancialMetrics` Table INSERT (PK=job_id, RK=base_year)
- [ ] 2-3-8. 분석 결과 전체 → `jobs/{job_id}/analyze/result.json` Blob PUT
- [ ] 2-3-9. `AgentStatus[analyze]` status=done, `output_blob_path` 기록
- [ ] 2-3-10. 경량 응답 포맷 Pydantic 모델 정의 (job_id + risk_level + 핵심 요인만 반환)
  ```python
  { "job_id": "...", "status": "analyze_done",
    "risk_level": "주의", "risk_score": 62.4,
    "key_risk_factors": ["소송 2건 진행 중", "영업이익 YoY -30%"] }
  ```
- [ ] 2-3-11. Tool 단위 테스트 작성 (Azure AI Search mock + Table mock 포함)

---

## 3. 보고서 작성 Agent

> 자료 수집 · 재무 분석 결과 + Azure AI Search + 작성 지침 → 심사 보고서 초안 생성 후 Azure Blob 저장

### 3-1. 보고서 템플릿 및 학습 DB 구성

- [ ] 3-1-1. 내부 심사 보고서 표준 양식 분석 및 섹션 정의
  - 기업 개요 / 신청 목적 / 주요 재무 현황 / 리스크 요인 / 추가 확인사항 / 종합 의견
- [ ] 3-1-2. 보고서 작성 예시 문서(더미) Azure AI Search 색인 적재
- [ ] 3-1-3. 섹션별 Few-shot 예시 구성 (`/prompts/report_sections/`)

### 3-2. 보고서 작성 지침(Prompt) 설계

- [ ] 3-2-1. 보고서 작성 시스템 프롬프트 작성 (`/prompts/report_writing.md`)
- [ ] 3-2-2. 섹션별 서술 생성 프롬프트 템플릿 작성
- [ ] 3-2-3. 근거-인사이트 연결 구조 설계 (출처 추적 가능하도록)
- [ ] 3-2-4. 보고서 출력 포맷 정의 (Markdown 우선, DOCX 변환 옵션)

### 3-3. FastMCP Tool 등록 — 보고서 작성

- [ ] 3-3-1. `report_generate(job_id: str)` Tool 정의
  - `job_id`만 수신, Blob(`collect/raw.json`, `analyze/result.json`)과 Table(`FinancialMetrics` PK=job_id)에서 직접 로드
- [ ] 3-3-2. 섹션별 순차 생성 파이프라인 구현
- [ ] 3-3-3. Azure AI Search 유사 보고서 컨텍스트 주입
- [ ] 3-3-4. Markdown 최종 보고서 조립 후 **Azure Blob `jobs/{job_id}/report/report.md` PUT**
- [ ] 3-3-5. Blob SAS URL 생성 후 Tool 응답에 포함 (Claude.ai에서 링크로 확인 가능)
- [ ] 3-3-6. (옵션) `python-docx` 활용 DOCX 변환 후 `jobs/{job_id}/report/report.docx` Blob 추가 저장
- [ ] 3-3-7. `AgentStatus[report]` status=done, `output_blob_path` 기록
- [ ] 3-3-8. `AnalysisJobs` status=done, `report_blob_path` 기록
- [ ] 3-3-9. `AnalysisJobsRef` Table UPDATE (PK=company_id, RK=job_id, risk_level/finished_at 갱신)
- [ ] 3-3-10. Blob SAS URL 생성 후 경량 응답 포맷으로 반환
  ```python
  { "job_id": "...", "status": "done",
    "report_url": "https://.../report.md?sas=...",
    "docx_url":   "https://.../report.docx?sas=..." }
  ```
- [ ] 3-3-11. Tool 단위 테스트 작성 (Blob mock + Table mock 포함)

---

## 4. 사후관리 모니터링 Agent

> 배치성 Agent — 3개월 주기 / Naver News · 소송 재조회 → 위험 단계 시 Gmail 알림  
> ※ Azure Container Apps 최소 인스턴스 수 **1** (Always-on) 설정 필수

### 4-1. 모니터링 대상 관리 (Azure Table Storage)

- [ ] 4-1-1. **Azure Table Storage** 테이블 설계
  - `MonitoringTargets` : PartitionKey=`"company"`, RowKey=기업ID
    → `company_name`, `recipient_email`, `registered_at`, `is_active`
  - `MonitoringSnapshots` : PartitionKey=기업ID, RowKey=실행일자
    → `risk_level`, `news_summary`, `lawsuit_count`, `snapshot_json`
  - `AlertHistory` : PartitionKey=기업ID, RowKey=발송일시
    → `risk_level`, `sent_to`, `status`
  - `SchedulerState` : 스케줄러 마지막 실행 시각 저장 (컨테이너 재시작 복원용)
  - `MonitoringRunLogs` : 배치 실행 이력
- [ ] 4-1-2. Azure Table Storage CRUD 헬퍼 함수 작성 (`/storage/table_store.py`)
- [ ] 4-1-3. 대상 기업 등록 / 해제 Tool 구현
  - `monitor_register(company_name, company_id, recipient_email, origin_job_id)`
    - `origin_job_id` : 최초 보고서를 생성한 job_id → `MonitoringTargets` 테이블의 `origin_job_id` 필드에 저장
  - `monitor_deregister(company_id)`
  - `monitor_list()`

### 4-2. 배치 스케줄러 구성

- [ ] 4-2-1. APScheduler 설정 (3개월 주기 Cron : `0 9 1 */3 *`)
- [ ] 4-2-2. **컨테이너 재시작 내성** 설계
  - 서버 기동 시 `SchedulerState` 테이블에서 마지막 실행 시각 조회
  - 마지막 실행 후 3개월 경과 시 즉시 보상 실행 트리거
- [ ] 4-2-3. FastMCP `lifespan` 훅에 스케줄러 시작/종료 등록
- [ ] 4-2-4. 전체 모니터링 대상 순회 배치 Job 함수 구현
- [ ] 4-2-5. 배치 실행 시작 · 완료 로그 `MonitoringRunLogs` 테이블 기록
- [ ] 4-2-6. 수동 트리거 Tool 구현 (`monitor_run_now(company_id: str | None)`)

### 4-3. 위험 탐지 로직

- [ ] 4-3-1. 신규 뉴스 · 소송 데이터 수집 (1-2, 1-3 모듈 재활용)
- [ ] 4-3-2. `MonitoringSnapshots`에서 이전 스냅샷 조회 및 변화 감지 로직
  - 소송 신규 등록, 부정 뉴스 급증, 위험 키워드 등장 여부 판별
- [ ] 4-3-3. 위험 등급 재산출 및 등급 상향 여부 판별
  - 정상 → 주의 / 경고 / 위험 전환 시 알림 트리거
- [ ] 4-3-4. 모니터링 상세 원시 데이터 → `monitoring/{company_id}/{YYYYMMDD}/snapshot.json` Blob PUT
- [ ] 4-3-5. 신규 스냅샷 `MonitoringSnapshots` 테이블 저장 (`snapshot_blob_path` 필드에 위 Blob 경로 기록)

### 4-4. Gmail 알림 연동

- [ ] 4-4-1. Gmail API OAuth2 자격증명 JSON → **Azure Blob Storage(`credentials/`)에 저장**
  - 런타임에 Blob에서 로드 후 인증 처리
- [ ] 4-4-2. 알림 이메일 HTML 템플릿 작성 (기업명, 위험 등급, 주요 변화 내역, 확인 권고)
- [ ] 4-4-3. `send_alert_email(recipient, company_name, risk_level, summary)` 함수 구현
- [ ] 4-4-4. 발송 이력 `AlertHistory` 테이블 저장 — 중복 발송 방지 (3개월 내 동일 등급 발송 차단)
- [ ] 4-4-5. 이메일 발송 실패 시 재시도 및 에러 로그 기록

### 4-5. FastMCP Tool 등록 — 모니터링

- [ ] 4-5-1. `monitor_register` Tool 등록 및 description 작성
- [ ] 4-5-2. `monitor_deregister` Tool 등록
- [ ] 4-5-3. `monitor_list` Tool 등록
- [ ] 4-5-4. `monitor_run_now` Tool 등록 (수동 즉시 실행)
- [ ] 4-5-5. Tool 단위 테스트 작성 (Azure Table Storage mock + Gmail mock)

---

## 5. MCP 서버 통합 및 오케스트레이션

### 5-1. 단일 FastMCP 서버 통합

- [ ] 5-1-1. 4개 Agent의 모든 Tool을 `main.py` 단일 FastMCP 서버에 등록
- [ ] 5-1-2. Tool 네이밍 컨벤션 확정 및 description 최적화
  - prefix 규칙 : `collect_*` / `analyze_*` / `report_*` / `monitor_*`
  - Claude가 Tool description만 보고 올바르게 라우팅할 수 있도록 한국어 description 작성
- [ ] 5-1-3. 서버 메타데이터 설정 (`name="simsasukgo"`, `version`, `instructions`)
- [ ] 5-1-4. `instructions` 필드에 오케스트레이션 흐름 가이드 작성
  - "기업 분석 요청 시 `create_analysis_job` 먼저 호출해 `job_id` 를 발급받을 것"
  - "이후 `collect_company_data(job_id)` → `analyze_financials(job_id)` → `report_generate(job_id)` 순서로 호출"
  - "각 Tool은 `job_id` 하나만 다음 Tool에 전달하며 대용량 데이터는 직접 주고받지 않는다"
  - "모니터링 등록은 보고서 생성 완료 후 `monitor_register(origin_job_id=job_id)` 호출"
- [ ] 5-1-5. MCP Resource 등록 검토 (보고서 템플릿 · 분석 지침 문서를 Resource로 노출)

### 5-2. Claude.ai SSE 연동 검증

- [ ] 5-2-1. Claude.ai MCP 설정 작성 (SSE URL 기반)
  ```json
  {
    "mcpServers": {
      "simsasukgo": {
        "url": "https://<azure-container-app-domain>/sse"
      }
    }
  }
  ```
- [ ] 5-2-2. 로컬 Docker 기동 후 ngrok SSE 터널 → Claude.ai 연결 테스트
- [ ] 5-2-3. Azure 배포 후 Claude.ai SSE 연결 E2E 테스트
- [ ] 5-2-4. 자연어 입력 → 4개 Agent 순차 오케스트레이션 시나리오 검증
- [ ] 5-2-5. 오케스트레이션 실패 시 에러 메시지 및 fallback 처리 확인

### 5-3. 보안 및 인증

- [ ] 5-3-1. **Azure Key Vault** 도입 검토 (Storage 연결 문자열 · API 키 중앙 관리)
- [ ] 5-3-2. MCP SSE 엔드포인트 인증 설정 (Bearer Token 또는 Azure AD 기반)
- [ ] 5-3-3. 실 고객정보 · 개인정보 미사용 검증 (더미 데이터만 사용)
- [ ] 5-3-4. MCP Tool 입력값 유효성 검증 (Pydantic strict 모드)
- [ ] 5-3-5. 외부 API 호출 시 TLS 검증 강제 및 타임아웃 설정

---

## 6. Docker 컨테이너화

### 6-1. Dockerfile 작성

- [ ] 6-1-1. 베이스 이미지 선정 (`python:3.12-slim`)
- [ ] 6-1-2. 멀티스테이지 빌드 구성 (빌드 스테이지 / 런타임 스테이지 분리)
  ```dockerfile
  FROM python:3.12-slim AS builder
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  FROM python:3.12-slim
  WORKDIR /app
  COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
  COPY . .
  EXPOSE 8000
  HEALTHCHECK --interval=30s --timeout=5s \
    CMD curl -f http://localhost:8000/health || exit 1
  CMD ["python", "main.py"]
  ```
- [ ] 6-1-3. `.dockerignore` 작성 (`.env`, `__pycache__`, `tests/`, `.git` 제외)
- [ ] 6-1-4. 컨테이너 내 환경변수 주입 방식 확인 (Azure Container Apps Secrets 연동)

### 6-2. 로컬 Docker 검증

- [ ] 6-2-1. `docker build` 성공 및 이미지 크기 확인
- [ ] 6-2-2. `docker-compose up` 로컬 전체 스택 기동 확인
- [ ] 6-2-3. 로컬 컨테이너 → Claude.ai SSE 연결 동작 확인 (ngrok 터널 활용)
- [ ] 6-2-4. 컨테이너 재시작 후 스케줄러 상태 복원 동작 확인

### 6-3. Azure Container Registry (ACR) 연동

- [ ] 6-3-1. ACR 리소스 생성 (Azure Portal)
- [ ] 6-3-2. Docker 이미지 태깅 규칙 정의
  - `<acr-name>.azurecr.io/simsasukgo:<version>` (semver 또는 git SHA)
- [ ] 6-3-3. `az acr login` 후 `docker push` → ACR 업로드 확인
- [ ] 6-3-4. ACR 이미지 취약점 스캔 결과 확인 (Defender for Containers)

---

## 7. Azure 배포

### 7-1. Azure Storage 리소스 구성

- [x] 7-1-1. **Azure Storage Account** 생성 (Azure Portal)
  - 종류 : Standard LRS (개발) / ZRS (운영)
- [x] 7-1-2. **Blob 컨테이너** 생성 — 단일 컨테이너(`simsasukgo`) + prefix로 구분
  ```
  simsasukgo/
    jobs/{job_id}/input/        ← 사용자 업로드 파일 + prompt.txt
    jobs/{job_id}/collect/      ← Agent 1 출력 (raw.json)
    jobs/{job_id}/analyze/      ← Agent 2 출력 (result.json)
    jobs/{job_id}/report/       ← Agent 3 출력 (report.md, report.docx)
    monitoring/{company_id}/{YYYYMMDD}/  ← 모니터링 스냅샷
    credentials/                ← Gmail OAuth JSON
  ```
- [x] 7-1-3. **Azure Table Storage** 테이블 생성 (총 10개 — `azure-data-tables`로 코드에서 자동 생성 가능)
  - 운영 :
    - `AnalysisJobs` : Job 생명주기 추적 (PartitionKey=`"job"`, RowKey=job_id)
    - `AgentStatus` : Agent별 실행 상태 및 에러 추적 (PartitionKey=job_id, RowKey=agent명)
  - 정형 :
    - `Companies` : 기업 마스터 (PartitionKey=`"company"`, RowKey=company_id)
    - `FinancialRaw` : Agent 1 수집 재무 원시 (PartitionKey=job_id, RowKey=`{year}-{type}`)
    - `FinancialMetrics` : Agent 2 계산 재무 지표 (PartitionKey=job_id, RowKey=base_year)
    - `AnalysisJobsRef` : 기업별 Job 이력 인덱스 (PartitionKey=company_id, RowKey=job_id)
  - 모니터링 :
    - `MonitoringTargets` : 모니터링 등록 기업 목록
    - `MonitoringSnapshots` : 3개월 주기 위험 상태 스냅샷
    - `AlertHistory` : Gmail 알림 발송 이력
  - 인프라 :
    - `SchedulerState` : 배치 스케줄러 마지막 실행 시각
- [ ] 7-1-4. **Azure AI Search** 리소스 생성 및 인덱스 프로비저닝
  - 인덱스 : `financial-cases`, `report-templates`
  - 더미 데이터 초기 적재 스크립트 실행 확인

### 7-2. Azure Container Apps 배포

- [ ] 7-2-1. Container Apps **환경(Environment)** 생성
- [ ] 7-2-2. Container App 생성 (ACR 이미지 연결)
  - 초기 리소스 : CPU 1 core / Memory 2Gi (부하 테스트 후 조정)
- [ ] 7-2-3. **스케일링 설정**
  - 최솟값 : **1** (Always-on — APScheduler 상시 유지 필수)
  - 최댓값 : 3 (SSE 동시 요청 급증 대응)
- [ ] 7-2-4. **환경변수 및 Secrets 등록** (Azure Portal UI 또는 `az containerapp update`)
  - `.env.example` 전체 항목을 Container App Secrets로 등록
- [ ] 7-2-5. **Ingress 설정** : External / HTTPS / 대상 포트 8000
- [ ] 7-2-6. (선택) 커스텀 도메인 및 관리형 TLS 인증서 설정
- [ ] 7-2-7. `GET /health` 엔드포인트로 Container Apps Liveness Probe 설정
- [ ] 7-2-8. 배포 후 로그 스트림에서 FastMCP SSE 서버 정상 기동 확인

### 7-3. CI/CD 파이프라인 구성 (선택)

- [ ] 7-3-1. GitHub Actions 워크플로우 작성 (`.github/workflows/deploy.yml`)
  ```
  push to main
    → pytest (단위 테스트)
    → docker build & push to ACR
    → az containerapp update (rolling update)
  ```
- [ ] 7-3-2. ACR Service Principal 또는 Workload Identity 설정 (GitHub Secrets 등록)
- [ ] 7-3-3. 배포 후 자동 smoke test (SSE 연결 + `monitor_list` Tool 호출 확인)

### 7-4. 운영 모니터링

- [ ] 7-4-1. **Azure Application Insights** 연결 (컨테이너 로그 · 요청 추적)
- [ ] 7-4-2. 경고 규칙 설정 : 컨테이너 재시작 횟수 임계치 초과 시 이메일 알림
- [ ] 7-4-3. Azure Storage 비용 모니터링 및 Blob 수명 주기 정책 설정
  - 보고서 Blob : 1년 경과 시 Cool tier 이동
- [ ] 7-4-4. Azure Table Storage 쿼리 성능 · Throttling 모니터링 설정

---

## 8. 테스트 및 품질 보증

- [ ] 8-1. Tool별 단위 테스트 전체 커버리지 확인 (`pytest` + `pytest-asyncio`)
  - Azure SDK 호출은 `unittest.mock` / `pytest-mock`으로 격리
- [ ] 8-2. 자료 수집 → 재무 분석 → 보고서 작성 통합 테스트 시나리오 작성
- [ ] 8-3. 모니터링 배치 Job 통합 테스트 (mock 시간 이동 + mock Gmail + mock Azure Table)
- [ ] 8-4. 컨테이너 재시작 후 `AgentStatus` running 잔존 Job 자동 재개 시나리오 테스트
- [ ] 8-4-1. 컨테이너 재시작 후 `SchedulerState` 기반 배치 복원 시나리오 테스트
- [ ] 8-5. Azure 배포 환경에서 Claude.ai 대표 시나리오 E2E 테스트 (3~5개 기업 더미 샘플)
- [ ] 8-6. 에러 케이스 테스트 (API 타임아웃, Azure Storage 연결 실패, 빈 결과 등)
- [ ] 8-7. `ruff` / `mypy` 정적 분석 설정 및 CI 파이프라인 연동

---

## 9. 문서화 및 마무리

- [ ] 9-1. `README.md` 작성 (서비스 개요, 로컬 Docker 실행 방법, Azure 배포 절차)
- [ ] 9-2. Azure 리소스 구성 가이드 작성 (Storage Account · Container Apps · AI Search 설정 순서)
- [ ] 9-3. 환경변수 목록 및 Azure Secrets 등록 방법 가이드
- [ ] 9-4. Claude.ai MCP 연동 가이드 작성 (SSE URL 등록 방법, 연동 확인 절차)
- [ ] 9-5. 각 Agent Tool 명세 문서 작성 (입력 · 출력 · 예시)
- [ ] 9-6. 모니터링 스케줄러 운영 가이드 (로그 확인, 수동 트리거, 스냅샷 조회 방법)
- [ ] 9-7. 향후 고도화 항목 정리
  - 실 내부 DB (KISLINE / CRETOP / DART) 연동
  - HWP 문서 파싱 지원
  - Azure AI Search 인덱스 자동 갱신 파이프라인
  - Azure Key Vault 기반 Secrets 관리 강화
  - Azure AD 기반 MCP SSE 엔드포인트 인증
