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
- [x] 0-9. 로컬 서버 (`uv run python -m src.main`) + ngrok 터널 → Claude.ai MCP SSE 연결 동작 확인
  - Docker 대신 uv 직접 실행 (Docker 미설치). Claude.ai 입장에서는 동일 SSE endpoint
  - 4-agent 자율 오케스트레이션 (create → collect → analyze → report) end-to-end 동작 확인 완료
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

- [~] 2-2-1. ~~재무 분석 시스템 프롬프트 작성 (`/prompts/financial_analysis.md`)~~
  → `src/agents/financial/prompts.py` 모듈 상수로 작성 (PoC 단순화). 별도 파일 분리는 후속.
- [~] 2-2-2. ~~주요 재무 지표 자동 계산 로직 구현 (부채비율, 유동비율, 영업이익률, 이자보상배율 등)~~
  → 1-1-4 더미 재무 데이터 보류로 입력 자체가 없음. 더미 적재 후 별도 PR.
- [~] 2-2-3. ~~정형 데이터 분석 프롬프트 (수치 비교 · 추이 서술 템플릿)~~
  → 2-2-2 와 동일 사유 보류. 현재 단일 SYSTEM_PROMPT 에 통합.
- [x] 2-2-4. 비정형 데이터 분석 프롬프트 (뉴스 · 위험 신호 탐지) — `prompts.py` 단일 시스템 프롬프트로 작성
- [x] 2-2-5. 위험 등급 산출 기준 정의 (LOW / MEDIUM / HIGH / CRITICAL + 0~100 점수, `RiskLevel` enum 활용)

### 2-3. FastMCP Tool 등록 — 재무 분석

- [x] 2-3-1. `analyze_financials(job_id: str)` Tool 정의
  - `job_id`만 수신, Blob(`jobs/{job_id}/collect/raw.json`) 에서 직접 데이터 로드
- [x] 2-3-2. Agent 시작 시 `AgentStatus[analyze]` status=running, `AnalysisJobs` status=analyzing 업데이트
- [~] 2-3-3. ~~`FinancialRaw` Table query (PK=job_id) + Blob `raw.json` GET → 분석 입력 구성~~
  → 현재는 `raw.json` GET 만 수행. `FinancialRaw` query 는 1-1-4 더미 데이터 보류로 미수행.
- [~] 2-3-4. ~~재무 지표 계산 및 업종 벤치마크 비교 로직 구현~~
  → 2-2-2 와 동일 사유 보류 (재무 데이터 없음).
- [~] 2-3-5. ~~Azure AI Search 유사 사례 검색 결과 컨텍스트 주입~~
  → 2-1 (Azure AI Search 인덱스 구축) 미진행. 별도 PR.
- [x] 2-3-6. 분석 인사이트 생성 (Claude API 호출, `claude-haiku-4-5-20251001` default)
- [~] 2-3-7. ~~분석 결과 → `FinancialMetrics` Table INSERT (PK=job_id, RK=base_year)~~
  → 재무 지표 계산이 보류라 INSERT 할 데이터가 없음. 더미 재무 데이터 + 지표 계산 후 별도 PR.
- [x] 2-3-8. 분석 결과 전체 → `jobs/{job_id}/analyze/result.json` Blob PUT
- [x] 2-3-9. `AgentStatus[analyze]` status=done, `output_blob_path` 기록
- [x] 2-3-10. 경량 응답 포맷 Pydantic 모델 정의 (`AnalyzeResponse`: job_id, risk_level, risk_score, key_risk_factors, data_gaps, output_blob_path)
- [x] 2-3-11. Tool 단위 테스트 작성 (Anthropic mock + Blob/Table mock, JSON 파싱·프롬프트 빌더 별도 테스트 포함)

---

## 3. 보고서 작성 Agent

> 자료 수집 · 재무 분석 결과 + Azure AI Search + 작성 지침 → 심사 보고서 초안 생성 후 Azure Blob 저장

### 3-1. 보고서 템플릿 및 학습 DB 구성

- [x] 3-1-1. 내부 심사 보고서 표준 양식 분석 및 섹션 정의 (Claude 가 샘플 보고서를 학습해 동일 구조 재현)
- [~] 3-1-2. ~~보고서 작성 예시 문서(더미) Azure AI Search 색인 적재~~
  → 샘플이 3개뿐이라 AI Search 오버킬. **Azure Blob `templates/report_samples/*.docx`** 에 익명화본 업로드 후 startup 1회 로드 → 메모리 캐시.
- [~] 3-1-3. ~~섹션별 Few-shot 예시 구성 (`/prompts/report_sections/`)~~
  → 샘플 보고서 전체를 prompt 에 inject (섹션별 분할 미적용). 샘플이 충분히 늘면 그때 분할.

### 3-2. 보고서 작성 지침(Prompt) 설계

- [~] 3-2-1. ~~보고서 작성 시스템 프롬프트 작성 (`/prompts/report_writing.md`)~~
  → `src/agents/report/prompts.py` 모듈 상수로 작성 (PoC 단순화). 별도 파일 분리는 후속.
- [~] 3-2-2. ~~섹션별 서술 생성 프롬프트 템플릿 작성~~
  → 단일 시스템 프롬프트 + 샘플 inject 방식으로 통합 (섹션별 분리 호출 없음).
- [x] 3-2-3. 근거-인사이트 연결 구조 설계 (`result.json.data_gaps` 를 "추가 확인사항" 섹션에 명시 / 자료 없으면 "자료 미확보" 강제)
- [x] 3-2-4. 보고서 출력 포맷 정의 (Markdown 채택, DOCX 변환은 후속 별도 PR 보류)

### 3-3. FastMCP Tool 등록 — 보고서 작성

- [x] 3-3-1. `report_generate(job_id: str)` Tool 정의
  - `job_id`만 수신, Blob(`collect/raw.json`, `analyze/result.json`) 에서 직접 로드
  - `FinancialMetrics` 조회는 1-1-4 더미 데이터 보류로 미수행
- [~] 3-3-2. ~~섹션별 순차 생성 파이프라인 구현~~
  → 단일 Claude 호출로 보고서 전체를 한 번에 생성 (Sonnet-4.6 + 8K max_tokens 로 충분). 섹션별 호출은 출력 일관성 저하·비용 증가라 미채택.
- [~] 3-3-3. ~~Azure AI Search 유사 보고서 컨텍스트 주입~~
  → 3-1-2 와 동일 사유 — Blob 기반 RAG 로 대체.
- [x] 3-3-4. Markdown 최종 보고서 조립 후 **Azure Blob `jobs/{job_id}/report/report.md` PUT**
- [x] 3-3-5. Blob SAS URL 생성 후 Tool 응답에 포함 (default 7일 만료)
- [~] 3-3-6. ~~(옵션) `python-docx` 활용 DOCX 변환 후 `jobs/{job_id}/report/report.docx` Blob 추가 저장~~
  → 별도 PR 로 분리. python-docx 는 sample 추출용으로만 사용 (생성 X).
- [x] 3-3-7. `AgentStatus[report]` status=done, `output_blob_path` 기록
- [x] 3-3-8. `AnalysisJobs` status=done, `report_blob_path` 기록 + `finished_at` 갱신
- [x] 3-3-9. `AnalysisJobsRef` Table UPDATE (PK=company_id, RK=job_id, risk_level/finished_at 갱신)
- [x] 3-3-10. 경량 응답 포맷 Pydantic 모델 정의 (`ReportResponse`: job_id, risk_level, risk_score, report_url, report_blob_path)
- [x] 3-3-11. Tool 단위 테스트 작성 (Anthropic mock + Blob/Table mock + DOCX 파싱 + 프롬프트 빌더 + 템플릿 캐시 별도 테스트 포함)

---

## 4. 사후관리 모니터링 Agent

> 배치성 Agent — 3개월 주기 / Naver News · 소송 재조회 → 위험 단계 시 Gmail 알림  
> ※ Azure Container Apps 최소 인스턴스 수 **1** (Always-on) 설정 필수

### 4-1. 모니터링 대상 관리 (Azure Table Storage)

- [x] 4-1-1. **Azure Table Storage** 테이블 설계 — Step 1-1-2 에서 4개 테이블 모두 완료 (`MonitoringTargets`, `MonitoringSnapshots`, `AlertHistory`, `SchedulerState`). `MonitoringRunLogs` 는 별도 도입하지 않고 `AgentStatus` + 로그로 대체
- [x] 4-1-2. Azure Table Storage CRUD 헬퍼 — Step 1-1-2 에서 모든 Repo 완성
- [x] 4-1-3. 대상 기업 등록/해제/조회 Tool 구현 — `monitor_register` / `monitor_deregister` / `monitor_list`
  - register 시 Companies 존재 검증, 동일 company_id 재등록은 upsert
  - deregister 는 soft delete (`is_active=False`) — 과거 스냅샷·알림 이력 보존

### 4-2. 배치 스케줄러 구성

- [x] 4-2-1. APScheduler `AsyncIOScheduler` + `CronTrigger.from_crontab(MONITORING_BATCH_CRON)` (default `0 9 1 */3 *`, env override 가능)
- [x] 4-2-2. 컨테이너 재시작 내성 — `needs_catchup` 가 `SchedulerState.last_run_at` 와 `MONITORING_CATCHUP_THRESHOLD_DAYS` 비교 → 초과 시 lifespan 에서 즉시 1회 보상 실행 (`schedule_catchup` date trigger)
- [x] 4-2-3. FastMCP lifespan: `setup_scheduler` 시작 + `shutdown_scheduler` 종료 등록. 시크릿 미설정 시 자동 skip
- [x] 4-2-4. `run_monitoring_batch` — `list_active()` 순회 → 각 target 에 monitor_run_now_service 호출. 한 target 실패해도 다른 target 계속 진행 (개별 try/except + 카운트)
- [~] 4-2-5. ~~배치 실행 시작 · 완료 로그 `MonitoringRunLogs` 테이블 기록~~
  → 별도 Table 신설하지 않고 `monitor.batch.start` / `monitor.batch.done` 구조화 로그 + `SchedulerState.run_count` 누적으로 대체. 배치 이력 별도 조회 필요해지면 후속 도입
- [x] 4-2-6. 수동 트리거 Tool 구현 (`monitor_run_now(company_id)`) — collect+analyze 재실행 + snapshot 저장. 단일 company_id 만 지원 (전체 순회는 4-2-4 PR 4)

### 4-3. 위험 탐지 로직

- [x] 4-3-1. 신규 뉴스 데이터 수집 — 1-2 (Naver) 모듈 재활용. 소송은 1-3 보류로 미수행
- [x] 4-3-2. 이전 스냅샷 대비 변화 감지 — MonitoringTarget.last_risk_level 와 RiskLevel rank 비교 (`alerter.should_alert`). 키워드 다이프 / 점수 임계치는 PoC 범위 외 (보류 — 사용자 의도 "MEDIUM 이상 *상승* 진입 시" 단순 규칙 채택)
- [x] 4-3-3. 위험 등급 상향 판별 → 알림 트리거 — alerter 가 should_alert 통과 시 maybe_send_alert 발송
- [x] 4-3-4. 모니터링 상세 원시 데이터 → `monitoring/{company_id}/{YYYYMMDD}/snapshot.json` Blob PUT
- [x] 4-3-5. 신규 스냅샷 `MonitoringSnapshots` 테이블 저장 (`snapshot_blob_path` + `analysis_job_id` 필드 추가)

### 4-4. Gmail 알림 연동

- [x] 4-4-1. Gmail OAuth2 자격증명 JSON → Azure Blob `credentials/gmail_oauth.json`
  - 런타임에 Blob 에서 로드, google-auth 가 refresh_token 자동 갱신
  - 1회용 consent 스크립트 추가 (`scripts/gmail_oauth_consent.py`) — 사용자가 한 번만 실행해서 Blob 업로드
- [x] 4-4-2. HTML 알림 이메일 템플릿 (`alerter.render_alert_html`) — 위험 등급 색상 / 요약 / 핵심 위험 요인 / 드릴다운 정보 (analysis_job_id, snapshot_blob_path) + plain text fallback
- [x] 4-4-3. `GmailClient.send_html(recipient, subject, html_body, text_body)` 구현 (gmail.send scope 만)
- [x] 4-4-4. AlertHistory dedup — `is_duplicate_alert` 가 3개월(default) 내 SENT 이력 검사. 중복이면 발송 skip + 로그
- [~] 4-4-5. ~~이메일 발송 실패 시 재시도 및 에러 로그 기록~~
  → 현재는 GmailApiError 발생 시 AlertHistory 에 FAILED 기록 + 예외 전파. 자동 재시도는 PoC 범위 외 (운영 시 별도 retry 큐 도입 검토)

### 4-5. FastMCP Tool 등록 — 모니터링

- [x] 4-5-1. `monitor_register` Tool 등록 및 description 작성
- [x] 4-5-2. `monitor_deregister` Tool 등록
- [x] 4-5-3. `monitor_list` Tool 등록
- [x] 4-5-4. `monitor_run_now` Tool 등록 (수동 즉시 실행) — PR 2 완료
- [~] 4-5-5. Tool 단위 테스트 — register/deregister/list/run_now 15개 케이스. Gmail 관련 mock 은 PR 3 에서.

---

## 5. MCP 서버 통합 및 오케스트레이션

### 5-1. 단일 FastMCP 서버 통합

- [x] 5-1-1. 4개 Agent의 모든 Tool을 단일 FastMCP 서버에 등록 — `src/mcp/server.py` `create_mcp_server()` 에서 `register_job_tools` + 4 agent register_*_tools 일괄 등록
- [x] 5-1-2. Tool 네이밍 컨벤션 확정 및 description 최적화 — prefix 규칙 (`collect_*` / `analyze_*` / `report_*` / `monitor_*`) + 모든 description 한국어 작성. Claude.ai 자율 오케스트레이션 검증 완료 (5-2-4)
- [x] 5-1-3. 서버 메타데이터 설정 — `name="simsasukgo"`, `version="0.1.0"`, `instructions=SERVER_INSTRUCTIONS`
- [x] 5-1-4. `instructions` 필드에 오케스트레이션 흐름 가이드 작성 — `SERVER_INSTRUCTIONS` 상수에 4-step 흐름 + monitor_register 옵션 명시
- [ ] 5-1-5. MCP Resource 등록 검토 (보고서 템플릿 · 분석 지침 문서를 Resource로 노출)
  → 현재는 Tool 만으로 충분히 동작. Resource 노출은 후속 PR.

### 5-2. Claude.ai SSE 연동 검증

- [x] 5-2-1. Claude.ai MCP 설정 — SSE URL 기반 connector 등록 절차 README 에 정리
  ```
  Claude.ai → Settings → Connectors → Add custom MCP
  URL: https://<aca-fqdn>/sse
  ```
- [x] 5-2-2. 로컬 ngrok SSE 터널 → Claude.ai 연결 테스트 — 0-9 와 동일 (uv 직접 실행 + ngrok)
- [x] 5-2-3. Azure 배포 후 Claude.ai SSE 연결 E2E 테스트 — ACA `simsasukgo-mcp` FQDN 으로 connector 등록 → 연결 성공 확인
- [x] 5-2-4. 자연어 입력 → 4개 Agent 순차 오케스트레이션 시나리오 검증 — "기업 분석해줘 + 첨부파일" 한 줄로 create → collect → analyze → report 자율 호출 확인
- [~] 5-2-5. ~~오케스트레이션 실패 시 에러 메시지 및 fallback 처리 확인~~
  → `common/exceptions.py` 의 도메인 예외(ExternalApiError/StorageError/...) + `AgentStatus` 의 status=failed 기록은 구현. Tool 응답 fallback 메시지 표준화는 후속 (8-6 에러 케이스 테스트와 묶어 진행).

### 5-3. 보안 및 인증

- [ ] 5-3-1. **Azure Key Vault** 도입 검토 (Storage 연결 문자열 · API 키 중앙 관리)
  → 현재 ACA secret 으로 충분. Key Vault 는 운영 시점 별도 PR.
- [ ] 5-3-2. MCP SSE 엔드포인트 인증 설정 (Bearer Token 또는 Azure AD 기반)
  → PoC 단계는 미구현 (FQDN 비공개 + Service Principal 기반 배포만). 운영 시점 별도 PR.
- [x] 5-3-3. 실 고객정보 · 개인정보 미사용 검증 — CLAUDE.md 보안 원칙으로 강제, 모든 테스트/데모는 더미 기업명 사용
- [~] 5-3-4. ~~MCP Tool 입력값 유효성 검증 (Pydantic strict 모드)~~
  → 모든 schemas.py 가 `Field(min_length=, max_length=, ge=, le=)` 로 필드 단위 검증. `model_config = ConfigDict(strict=True)` 명시 도입은 후속.
- [~] 5-3-5. ~~외부 API 호출 시 TLS 검증 강제 및 타임아웃 설정~~
  → Naver httpx 클라이언트는 명시 timeout (`Timeout(10.0, connect=5.0)`). Anthropic/Gmail 은 SDK 기본 (TLS 검증 + 합리적 timeout 내장). 명시 timeout 일괄 적용은 후속.

---

## 6. Docker 컨테이너화

### 6-1. Dockerfile 작성

- [x] 6-1-1. 베이스 이미지: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` 채택 (uv 빌트인, python:3.12-slim 대신)
- [x] 6-1-2. uv sync 캐시 마운트 + 두 단계 sync (lock → src)로 재빌드 효율화
- [x] 6-1-3. `.dockerignore` 작성 (`.env`, `__pycache__`, `tests/`, `.git`, `.venv` 등 제외)
- [x] 6-1-4. ACA Secrets → env vars 매핑은 `scripts/deploy_azure.sh` 에서 일괄 처리

### 6-2. 로컬 Docker 검증

- [~] 6-2-1. ~~`docker build` 성공 및 이미지 크기 확인~~
  → Docker 미설치. ACR Build (`az acr build`) 로 클라우드 빌드 사용 (6-3-3 참조).
- [~] 6-2-2. ~~`docker-compose up` 로컬 전체 스택 기동 확인~~
  → 6-2-1 과 동일 사유. 로컬 검증은 `uv run python -m src.main` 으로 대체 (0-9 참조).
- [x] 6-2-3. (uv 직접 실행 + ngrok) → Claude.ai SSE 연결 동작 확인 — 0-9 와 동일
- [ ] 6-2-4. 컨테이너 재시작 후 스케줄러 상태 복원 동작 확인 (APScheduler 미구현 상태로 보류)

### 6-3. Azure Container Registry (ACR) 연동

- [x] 6-3-1. ACR 리소스 생성 — `scripts/deploy_azure.sh` 에서 idempotent create
- [x] 6-3-2. 이미지 태깅 규칙: `<acr>.azurecr.io/simsasukgo-mcp:<git-sha>` + `:latest` 동시 태깅
- [x] 6-3-3. `az acr build` 로 클라우드 빌드 + ACR push 일체화 (Docker 로컬 불필요)
- [ ] 6-3-4. ACR 이미지 취약점 스캔 결과 확인 (Defender for Containers — 별도 활성화 필요)

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

- [x] 7-2-1. Container Apps **환경(Environment)** 생성 — `simsasukgo-aca-env` (`scripts/deploy_azure.sh`)
- [x] 7-2-2. Container App 생성 (ACR 이미지 연결) — `simsasukgo-mcp`
  - 초기 리소스: CPU 0.5 / Memory 1Gi (PoC 기준, env로 override 가능)
- [x] 7-2-3. 스케일링 설정 — min 1 (Always-on, 향후 APScheduler 대비) / max 3
- [x] 7-2-4. 환경변수 및 Secrets 등록 — `.env` 의 시크릿 4종(ANTHROPIC_API_KEY, AZURE_STORAGE_CONNECTION_STRING, NAVER_CLIENT_ID/SECRET) 을 ACA secret 으로 + env var 가 secretref 참조. 나머지는 일반 env var.
- [x] 7-2-5. Ingress 설정: External / HTTPS / target-port 8000 / transport auto (SSE 호환)
- [ ] 7-2-6. (선택) 커스텀 도메인 및 관리형 TLS 인증서 설정 — PoC 단계 보류
- [ ] 7-2-7. `GET /health` 엔드포인트로 Container Apps Liveness Probe 설정 — ACA 기본 probe 사용 (별도 설정은 옵션)
- [ ] 7-2-8. 배포 후 로그 스트림 확인 — 사용자 실 배포 후 `az containerapp logs show -n simsasukgo-mcp -g SIMSASUKGO-GR --follow` 로 검증

### 7-3. CI/CD 파이프라인 구성

- [x] 7-3-1. GitHub Actions 워크플로우 (`.github/workflows/ci.yml`) — 단일 파일 2 job
  - `test` (모든 push/PR): uv + Python 3.12 + ruff + pytest
  - `deploy` (push to main 만): az login → az acr build → az containerapp secret set → registry set → update --image + --set-env-vars → /health 6회 retry
- [x] 7-3-2. Service Principal (`simsasukgo-github-cicd`, contributor on RG) + GitHub Secrets 등록 (`AZURE_CREDENTIALS`, `ANTHROPIC_API_KEY`, `AZURE_STORAGE_CONNECTION_STRING`, `NAVER_CLIENT_ID/SECRET`)
- [x] 7-3-3. 자동 smoke test — `/health` curl 6회 × 15s retry. 실패 시 workflow 실패 처리. SSE/Tool 호출 자동 검증은 후속 (8-5)

> Workload Identity (OIDC) 마이그레이션은 운영 시점에 별도 PR. PoC 단계는 SP 로 충분.
> 배포 흐름: develop merge → main PR → main 머지 = 자동 ACR build + ACA rolling update + health check.

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

- [x] 9-1. `README.md` 작성 — 서비스 개요 / 4-Agent 아키텍처 / 8개 Tool 카탈로그 / 디렉터리 구조 / 로컬 실행 / Azure 자동·수동 배포 / Claude.ai connector 등록 / Gmail OAuth / 데모 시나리오 / 기술 스택 / 보류 항목 정리
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
