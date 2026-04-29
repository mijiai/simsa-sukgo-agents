# 심사숙고 — Agent 간 데이터 흐름 & Storage 설계서

## 핵심 설계 원칙

> **Agent 간에는 `job_id` 하나만 전달한다.**
> 각 Agent는 `job_id`로 Storage를 직접 읽고 쓴다.
> 대용량 JSON을 MCP Tool 응답에 담지 않는다.

**인프라 채택 결정 (PoC)**:
- Azure SQL / CosmosDB **미사용** — 정형 데이터도 모두 Azure Table Storage 로 통합
- Azure AI Search **미사용** — 보고서/재무 분석 참고 샘플은 Blob `templates/` prefix + startup 1회 메모리 캐시 패턴 (TODO 2-1 / 3-1-2 결정 사유 참조)
- Azure Container Apps min=1 (Always-on) — APScheduler 가 모니터링 배치를 컨테이너 내에서 직접 실행

---

## 1. 전체 데이터 흐름

### 1-1. 분석 요청 흐름 (사용자 트리거)

```
사용자 입력
  - 기업명
  - 첨부 파일 (PDF / DOCX / XLS / XLSX 등)
  - 커스텀 프롬프트
        │
        ▼
┌─────────────────────────────┐
│  [Job 생성]                  │
│  AnalysisJobs INSERT         │
│  AnalysisJobsRef INSERT      │
│  AgentStatus 3행 INSERT      │
│  Blob jobs/{job_id}/input/  │
│  status = "pending"          │
└────────────┬────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 1 : 자료 수집  (collect_company_data)         │
│  READ   → Table: Companies (find_by_name)            │
│           Naver News API                             │
│  WRITE  → Blob: jobs/{job_id}/collect/raw.json      │
│           Table: AgentStatus[collect] = done         │
└────────────┬────────────────────────────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 2 : 재무 분석  (analyze_financials)           │
│  READ   → Blob: jobs/{job_id}/collect/raw.json      │
│           메모리 캐시: financial_samples[]            │
│           Anthropic Claude (Haiku)                   │
│  WRITE  → Blob: jobs/{job_id}/analyze/result.json   │
│           Table: AgentStatus[analyze] = done         │
└────────────┬────────────────────────────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 3 : 보고서 작성  (report_generate)            │
│  READ   → Blob: jobs/{job_id}/collect/raw.json      │
│           Blob: jobs/{job_id}/analyze/result.json    │
│           메모리 캐시: report_samples[]               │
│           Anthropic Claude (Sonnet)                  │
│  WRITE  → Blob: jobs/{job_id}/report/report.md      │
│           Table: AnalysisJobs status = "done"        │
│           Table: AnalysisJobsRef risk_level UPDATE   │
│           Table: AgentStatus[report] = done          │
└────────────┬────────────────────────────────────────┘
             │
             ▼
     SAS URL 반환 → 사용자에게 보고서 링크 제공
             │
             ▼
     (옵션) monitor_register(company_id=..., recipient_email=...)
             → MonitoringTargets INSERT (사후 모니터링 대상 등록)
```

### 1-2. 모니터링 배치 흐름 (APScheduler 트리거)

```
APScheduler  (cron: "0 9 1 */3 *" — 매 3개월 1일 09:00 UTC)
        │
        ▼
┌──────────────────────────────────────────────────────┐
│  run_monitoring_batch                                 │
│  READ   → Table: MonitoringTargets list_active()     │
│  FOR each target (개별 try/except, 한 건 실패도 배치 진행)│
│    └─► monitor_run_now_service(target.company_id)    │
│         (1-1 의 collect + analyze 재실행)            │
│         + Blob monitoring/{cid}/{YYYYMMDD}/snapshot.json │
│         + Table MonitoringSnapshots INSERT           │
│         + Table MonitoringTargets last_run_at UPDATE │
│         + 위험 등급 *상승* 시:                         │
│           - AlertHistory dedup 검사 (90일 내)         │
│           - Gmail send (HTML)                        │
│           - AlertHistory INSERT (sent / failed)      │
│  WRITE  → Table: SchedulerState last_run_at UPSERT  │
└──────────────────────────────────────────────────────┘

컨테이너 재시작 시:
  lifespan startup → SchedulerState.last_run_at 확인
                    → MONITORING_CATCHUP_THRESHOLD_DAYS (default 90) 초과 시
                    → 즉시 1회 보상 실행 (date trigger)
```

### 1-3. Startup 시 메모리 캐시 로딩

```
컨테이너 startup (mcp/server.py lifespan)
  │
  ├─► load_report_samples(blob, "templates/report_samples/")
  │     → Blob 의 .docx 일괄 다운로드 → 텍스트 추출 → 모듈 캐시
  │
  └─► load_financial_samples(blob, "templates/financial_samples/")
        → Blob 의 .docx / .pdf / .xls / .xlsx 일괄 다운로드
        → 형식별 추출기 (python-docx / pypdf / xlrd / openpyxl)
        → 모듈 캐시
```

---

## 2. Azure Table Storage 스키마

> 메타데이터 + 상태 추적 전용. 단일 행 빠른 조회에 최적화.
> 총 **10개 테이블**. 모두 `azure-data-tables.aio` 로 코드에서 자동 생성 (idempotent).
> 상세 Pydantic 모델: [`src/storage/schemas.py`](../src/storage/schemas.py).

| # | 테이블 | 역할 | PK / RK |
|---|---|---|---|
| 1 | `AnalysisJobs` | Job 생명주기 | `"job"` / `job_id` |
| 2 | `AgentStatus` | Agent별 실행 상태 | `job_id` / agent명 |
| 3 | `Companies` | 기업 마스터 | `"company"` / `company_id` |
| 4 | `FinancialRaw` | 수집 재무 원시 (보류) | `job_id` / `{year}-{type}` |
| 5 | `FinancialMetrics` | 계산 재무 지표 (보류) | `job_id` / `str(base_year)` |
| 6 | `AnalysisJobsRef` | 기업별 Job 이력 인덱스 | `company_id` / `job_id` |
| 7 | `MonitoringTargets` | 모니터링 등록 기업 | `"company"` / `company_id` |
| 8 | `MonitoringSnapshots` | 모니터링 스냅샷 | `company_id` / `YYYYMMDD` |
| 9 | `AlertHistory` | Gmail 알림 이력 | `company_id` / `YYYYMMDD-HHmmss` |
| 10 | `SchedulerState` | 배치 마지막 실행 | `"scheduler"` / `"monitoring_batch"` |

> **운영 카테고리**: 1, 2 / **정형**: 3, 4, 5, 6 / **모니터링**: 7, 8, 9 / **인프라**: 10

---

### 테이블 1 : `AnalysisJobs`

**역할** : 분석 요청(Job) 단위의 전체 생명주기 추적

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"job"` 고정 |
| **RowKey** | String | `job_id` — UUID v4 |
| `company_name` | String | 사용자 입력 기업명 |
| `user_id` | String? | 요청 사용자 식별자 (배치 모니터링은 `"system:monitoring"`) |
| `status` | String | `pending` / `collecting` / `analyzing` / `reporting` / `done` / `failed` |
| `current_agent` | String? | 현재 실행 중인 Agent — `collect` / `analyze` / `report` |
| `custom_prompt` | String? | 사용자 지정 프롬프트 |
| `input_blob_prefix` | String? | 업로드 파일 경로 prefix (`jobs/{job_id}/input/`) |
| `report_blob_path` | String? | 완성 보고서 Blob 경로 (완료 후) |
| `error_message` | String? | 실패 시 에러 내용 |
| `created_at` | DateTime | Job 생성 시각 |
| `updated_at` | DateTime | 마지막 상태 변경 시각 |
| `finished_at` | DateTime? | 전체 완료 시각 |

**status 상태 머신**

```
pending
  └─► collecting  (Agent 1 시작)
        └─► analyzing  (Agent 2 시작)
              └─► reporting  (Agent 3 시작)
                    └─► done      (정상 완료)
  (any) ──────────► failed    (에러 발생 시)
```

> 모니터링 배치가 생성하는 Job 은 `report` 단계를 skip 하고 `analyzing` 직후 `done` 으로 마무리 (사후 분석에 보고서 작성 불필요).

---

### 테이블 2 : `AgentStatus`

**역할** : Job 내 Agent별 실행 상태 세분화 추적 + 컨테이너 재시작 시 재개 기준

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | Agent명 — `collect` / `analyze` / `report` |
| `status` | String | `pending` / `running` / `done` / `failed` |
| `started_at` | DateTime? | Agent 실행 시작 시각 |
| `finished_at` | DateTime? | Agent 완료 시각 |
| `duration_sec` | Int? | 소요 시간 (초) — `update_done` 에서 자동 계산 |
| `output_blob_path` | String? | 이 Agent 가 저장한 최종 Blob 경로 |
| `retry_count` | Int | 재시도 횟수 (현재 0 고정, 자동 retry 미구현) |
| `error_detail` | String? | 실패 시 스택트레이스 요약 |

---

### 테이블 3 : `Companies`

**역할** : 기업 마스터 데이터 — 동일 기업 반복 분석 시 재사용

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"company"` 고정 |
| **RowKey** | String | `company_id` — UUID 또는 법인번호 |
| `company_name` | String | 기업명 |
| `business_no` | String? | 사업자번호 |
| `corp_no` | String? | 법인번호 |
| `industry_code` | String? | 업종 코드 |
| `industry_name` | String? | 업종명 |
| `created_at` | DateTime | 마스터 등록 시각 |
| `updated_at` | DateTime | 마지막 갱신 시각 |

> 동일 기업이 여러 차례 분석되어도 마스터는 1개. `create_analysis_job` 에서 UPSERT.
> `find_by_name(company_name)` 으로 조회 가능 (cross-row scan).

---

### 테이블 4 : `FinancialRaw`  *(현재 보류 — TODO 1-1-4 더미 데이터 결정 대기)*

**역할** : Agent 1 이 수집한 재무제표 원시 수치 — 연도별 행

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | `{fiscal_year}-{fiscal_type}` (예: `2023-annual`) |
| `company_id` | String | 기업 식별자 |
| `fiscal_year` | Int | 결산 연도 |
| `fiscal_type` | String | `annual` / `quarter` |
| `revenue` | Int? | 매출액 (원) |
| `operating_profit` | Int? | 영업이익 |
| `net_income` | Int? | 당기순이익 |
| `total_assets` | Int? | 자산총계 |
| `total_liabilities` | Int? | 부채총계 |
| `total_equity` | Int? | 자본총계 |
| `current_assets` | Int? | 유동자산 |
| `current_liabilities` | Int? | 유동부채 |
| `operating_cf` | Int? | 영업활동현금흐름 |
| `data_source` | String? | `DART` / `KISLINE` / `internal` |
| `created_at` | DateTime | 수집 시각 |

> 통화 금액은 KRW 정수(원)로 저장. Agent 2 는 `PartitionKey=job_id` 단일 파티션 스캔으로 모든 연도 행을 빠르게 조회.
> 현재는 더미 데이터 적재 결정이 보류 상태이므로 INSERT 가 일어나지 않음.

---

### 테이블 5 : `FinancialMetrics`  *(현재 보류 — 위와 동일 사유)*

**역할** : Agent 2 가 계산한 재무 지표 — Job·기준연도 단위

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | `str(base_year)` (예: `"2023"`) |
| `company_id` | String | 기업 식별자 |
| `base_year` | Int | 분석 기준 연도 |
| `debt_ratio` | Double? | 부채비율 (%) |
| `current_ratio` | Double? | 유동비율 (%) |
| `interest_coverage` | Double? | 이자보상배율 (배) |
| `operating_margin` | Double? | 영업이익률 (%) |
| `net_margin` | Double? | 순이익률 (%) |
| `roa` | Double? | 총자산이익률 (%) |
| `roe` | Double? | 자기자본이익률 (%) |
| `revenue_growth` | Double? | 매출 성장률 YoY (%) |
| `profit_growth` | Double? | 영업이익 성장률 YoY (%) |
| `risk_level` | String | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `risk_score` | Double | 0~100 위험 점수 |
| `created_at` | DateTime | 계산 시각 |

> 비율·점수는 `Double` 저장. 현재는 입력 데이터 보류로 미사용 — Claude 가 산출한 `risk_level/risk_score` 는 `analyze/result.json` 에 저장됨.

---

### 테이블 6 : `AnalysisJobsRef`

**역할** : 기업별 Job 이력 인덱스 — "이 기업의 과거 분석 목록" 빠른 조회

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | `job_id` |
| `company_name` | String | 기업명 (denormalized — UI 표시용) |
| `status` | String | `AnalysisJobs.status` 와 동일 enum |
| `risk_level` | String? | 최종 위험 등급 (완료 후) |
| `created_at` | DateTime | Job 생성 시각 |
| `finished_at` | DateTime? | 완료 시각 |

> `AnalysisJobs` 가 PartitionKey=`"job"` 이라 "특정 기업의 과거 Job" 조회는 cross-partition scan 필요.
> 이 테이블이 그 보조 인덱스 — `company_id` 로 파티셔닝하여 1회 GET 으로 이력 조회.
> 무결성은 Agent 코드에서 보장 (Table Storage 트랜잭션 없음).

---

### 테이블 7 : `MonitoringTargets`

**역할** : 사후관리 모니터링 등록 기업 목록

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"company"` (Companies 와 동일 파티션 — list 조회 일관성) |
| **RowKey** | String | `company_id` |
| `company_name` | String | 기업명 |
| `recipient_email` | String | 알림 수신 이메일 |
| `origin_job_id` | String | 최초 보고서 생성 job_id (드릴다운 참조용) |
| `registered_at` | DateTime | 등록 시각 |
| `is_active` | Boolean | `False` 면 soft delete (이력 보존 + list 에서 제외) |
| `last_run_at` | DateTime? | 마지막 모니터링 실행 시각 |
| `last_risk_level` | String? | 마지막 위험 등급 (등급 *상승* 알림 판정에 사용) |

> `monitor_deregister` 는 row 삭제가 아닌 `is_active=False` 머지 → 과거 스냅샷·알림 이력 보존.
> `list_active()` 는 `is_active eq true` 필터로 조회.

---

### 테이블 8 : `MonitoringSnapshots`

**역할** : 모니터링 실행마다 위험 상태 저장 → 이전 스냅샷 대비 변화 감지 + UI 시계열 표시

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | 실행 일자 `YYYYMMDD` (`run_date.strftime("%Y%m%d")`) |
| `risk_level` | String | `RiskLevel` enum |
| `risk_score` | Double | 0~100 (analyzer 가 산출한 정량 점수) |
| `analysis_job_id` | String | 이 스냅샷을 생성한 AnalysisJob id (UI 드릴다운) |
| `news_count` | Int | 수집된 뉴스 총 건수 (collector 는 부정 판단 X) |
| `lawsuit_count` | Int | 소송 건수 (TODO 1-3 보류로 현재 0) |
| `summary` | String | 분석 요약 (Claude 생성, UI 목록 표시용) |
| `key_signals` | String | 주요 위험 신호 ` / ` 조인 (UI 한 줄 표시) |
| `snapshot_blob_path` | String? | 상세 원시 데이터 Blob 경로 |

> RowKey 가 날짜라 **같은 날 2회 실행하면 덮어씀** (UPSERT). 동일 일자 멱등성 확보.
> `run_date` 는 `date` 타입이지만 Table Storage 에는 `YYYYMMDD` 문자열로만 저장 (entity 변환 시 exclude).

---

### 테이블 9 : `AlertHistory`

**역할** : Gmail 알림 발송 이력 — 중복 발송 방지 (90일 dedup)

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | 발송 일시 `YYYYMMDD-HHmmss` |
| `risk_level` | String | 발송 당시 위험 등급 |
| `sent_to` | String | 수신자 이메일 |
| `status` | String | `sent` / `failed` |
| `gmail_message_id` | String? | Gmail API 반환 메시지 ID (`failed` 면 None) |

> dedup 검사: `list_recent(company_id, since=now - ALERT_DEDUP_DAYS)` → 같은 `risk_level` 이력이 있으면 skip.
> `failed` 도 기록 (재발송 의사결정용).

---

### 테이블 10 : `SchedulerState`

**역할** : APScheduler 마지막 실행 시각 저장 → 컨테이너 재시작 시 누락 배치 보상 실행 판정

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"scheduler"` 고정 |
| **RowKey** | String | `"monitoring_batch"` 고정 (단일 행) |
| `last_run_at` | DateTime? | 마지막 배치 실행 시각 |
| `next_run_at` | DateTime? | 다음 예정 실행 시각 (현재 미사용 — APScheduler 가 직접 관리) |
| `run_count` | Int | 누적 실행 횟수 |

> startup 시 `needs_catchup(threshold_days=MONITORING_CATCHUP_THRESHOLD_DAYS)` 가 `(now - last_run_at) >= threshold` 검사 → True 면 `schedule_catchup()` 으로 즉시 1회 보상 실행.
> `last_run_at == None` (최초 기동) 은 catchup 안 함 — 데모/배포 직후 폭주 방지.

---

## 3. Azure Blob Storage 구조

> 컨테이너명 : **`simsasukgo`** (단일 컨테이너 + prefix 로 영역 구분)
> Job 데이터는 `jobs/{job_id}/` prefix 아래, 모니터링은 `monitoring/{company_id}/` 아래.

```
simsasukgo/                              ← Blob 컨테이너 (1개)
│
├── jobs/
│   └── {job_id}/
│       ├── input/                       ← 사용자 업로드 파일
│       │   ├── {파일명1.pdf}
│       │   ├── {파일명2.xlsx}
│       │   └── prompt.txt               ← 커스텀 프롬프트 원문
│       │
│       ├── collect/                     ← Agent 1 출력
│       │   └── raw.json
│       │
│       ├── analyze/                     ← Agent 2 출력
│       │   └── result.json
│       │
│       └── report/                      ← Agent 3 출력
│           └── report.md                ← Markdown 보고서
│                                         (DOCX 변환은 TODO 3-3-6 보류)
│
├── monitoring/
│   └── {company_id}/
│       └── {YYYYMMDD}/
│           └── snapshot.json            ← 모니터링 상세 원시 (collect+analyze 결과)
│
├── templates/                           ← Agent 참고 샘플 (startup 캐시)
│   ├── report_samples/                  ← 보고서 Agent 톤·구조 참고
│   │   └── *.docx
│   │
│   └── financial_samples/               ← 재무 Agent 분석 톤·관점 참고
│       └── *.{docx,pdf,xls,xlsx}
│
└── credentials/
    └── gmail_oauth.json                 ← Gmail OAuth 자격증명 (refresh_token)
```

**Blob 경로 명명 규칙**

| 용도 | 경로 패턴 |
|---|---|
| 사용자 업로드 파일 | `jobs/{job_id}/input/{filename}` |
| 커스텀 프롬프트 | `jobs/{job_id}/input/prompt.txt` |
| Agent 1 출력 | `jobs/{job_id}/collect/raw.json` |
| Agent 2 출력 | `jobs/{job_id}/analyze/result.json` |
| 최종 보고서 MD | `jobs/{job_id}/report/report.md` |
| 모니터링 스냅샷 | `monitoring/{company_id}/{YYYYMMDD}/snapshot.json` |
| 보고서 참고 샘플 | `templates/report_samples/*.docx` |
| 재무 참고 샘플 | `templates/financial_samples/*.{docx,pdf,xls,xlsx}` |
| Gmail OAuth | `credentials/gmail_oauth.json` |

**SAS URL 발급 정책**
- `report.md` : User Delegation Key 우선, fallback Account Key. 기본 만료 **168시간 (7일)** — `REPORT_SAS_EXPIRY_HOURS` env 로 조정.
- 그 외 Blob 은 SAS 미생성 (서버 코드에서만 직접 read).

---

## 4. Agent별 Storage Read / Write 매핑

### 4-1. 분석 흐름 (사용자 트리거)

| 시점 | 주체 | Action | Storage |
|---|---|---|---|
| **startup** | server | LOAD `templates/report_samples/*.docx` → 모듈 캐시 | Blob |
| **startup** | server | LOAD `templates/financial_samples/*.{docx,pdf,xls,xlsx}` → 모듈 캐시 | Blob |
| 분석 버튼 클릭 | `create_analysis_job` | INSERT `AnalysisJobs` status=pending | Table |
| 분석 버튼 클릭 | `create_analysis_job` | INSERT `AnalysisJobsRef` (PK=company_id) | Table |
| 분석 버튼 클릭 | `create_analysis_job` | INSERT `AgentStatus` 3행 (collect/analyze/report 모두 pending) | Table |
| 파일 업로드 | `create_analysis_job` | PUT `jobs/{job_id}/input/*` | Blob |
| 커스텀 프롬프트 | `create_analysis_job` | PUT `jobs/{job_id}/input/prompt.txt` | Blob |
| Agent 1 시작 | `collect_company_data` | UPDATE `AnalysisJobs` status=collecting | Table |
| Agent 1 시작 | `collect_company_data` | UPDATE `AgentStatus[collect]` status=running | Table |
| Agent 1 실행 | `collect_company_data` | GET `Companies.find_by_name(name)` | Table |
| Agent 1 실행 | `collect_company_data` | Naver News API 호출 | (외부) |
| Agent 1 완료 | `collect_company_data` | PUT `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 1 완료 | `collect_company_data` | UPDATE `AgentStatus[collect]` status=done, output_blob_path | Table |
| Agent 2 시작 | `analyze_financials` | UPDATE `AnalysisJobs` status=analyzing | Table |
| Agent 2 시작 | `analyze_financials` | UPDATE `AgentStatus[analyze]` status=running | Table |
| Agent 2 실행 | `analyze_financials` | GET `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 2 실행 | `analyze_financials` | READ 모듈 캐시 `financial_samples` (메모리) | (캐시) |
| Agent 2 실행 | `analyze_financials` | Anthropic Claude (Haiku) `complete_json` | (외부) |
| Agent 2 완료 | `analyze_financials` | PUT `jobs/{job_id}/analyze/result.json` | Blob |
| Agent 2 완료 | `analyze_financials` | UPDATE `AgentStatus[analyze]` status=done, output_blob_path | Table |
| Agent 3 시작 | `report_generate` | UPDATE `AnalysisJobs` status=reporting | Table |
| Agent 3 시작 | `report_generate` | UPDATE `AgentStatus[report]` status=running | Table |
| Agent 3 실행 | `report_generate` | GET `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 3 실행 | `report_generate` | GET `jobs/{job_id}/analyze/result.json` | Blob |
| Agent 3 실행 | `report_generate` | READ 모듈 캐시 `report_samples` (메모리) | (캐시) |
| Agent 3 실행 | `report_generate` | Anthropic Claude (Sonnet) `complete_text` | (외부) |
| Agent 3 완료 | `report_generate` | PUT `jobs/{job_id}/report/report.md` | Blob |
| Agent 3 완료 | `report_generate` | GENERATE SAS URL (report.md) | Blob |
| Agent 3 완료 | `report_generate` | UPDATE `AnalysisJobs` status=done, report_blob_path, finished_at | Table |
| Agent 3 완료 | `report_generate` | UPDATE `AgentStatus[report]` status=done | Table |
| Agent 3 완료 | `report_generate` | UPDATE `AnalysisJobsRef` (PK=company_id, RK=job_id) risk_level, finished_at | Table |

### 4-2. 모니터링 흐름

| 시점 | 주체 | Action | Storage |
|---|---|---|---|
| 모니터링 등록 | `monitor_register` | UPSERT `MonitoringTargets` is_active=true | Table |
| 모니터링 해제 | `monitor_deregister` | UPDATE `MonitoringTargets` is_active=false (soft delete) | Table |
| 모니터링 목록 | `monitor_list` | Query `MonitoringTargets` is_active eq true | Table |
| 즉시 실행 | `monitor_run_now` | INSERT 새 `AnalysisJobs` (user_id=`"system:monitoring"`) | Table |
| 즉시 실행 | `monitor_run_now` | collect_company_data + analyze_financials 재실행 (보고서 skip) | Blob, Table |
| 즉시 실행 | `monitor_run_now` | UPDATE `AnalysisJobs` status=done | Table |
| 즉시 실행 | `monitor_run_now` | PUT `monitoring/{cid}/{YYYYMMDD}/snapshot.json` | Blob |
| 즉시 실행 | `monitor_run_now` | INSERT `MonitoringSnapshots` (PK=cid, RK=YYYYMMDD) | Table |
| 즉시 실행 | `monitor_run_now` | UPDATE `MonitoringTargets` last_run_at, last_risk_level | Table |
| 알림 판정 | `alerter.should_alert` | last_risk_level vs new risk_level rank 비교 | (메모리) |
| 알림 dedup | `alerter.is_duplicate_alert` | Query `AlertHistory` recent (90일) | Table |
| Gmail 발송 | `GmailClient.send_html` | Gmail API send (scope: gmail.send) | (외부) |
| 알림 기록 | `alerter.maybe_send_alert` | INSERT `AlertHistory` (sent / failed) | Table |
| 배치 시작 | `run_monitoring_batch` | Query `MonitoringTargets list_active()` | Table |
| 배치 (per target) | `run_monitoring_batch` | 위 monitor_run_now 동일 흐름 (try/except 격리) | (Blob, Table) |
| 배치 완료 | `run_monitoring_batch` | UPSERT `SchedulerState` last_run_at, run_count | Table |

### 4-3. 컨테이너 재시작 보상 실행

| 시점 | 주체 | Action | Storage |
|---|---|---|---|
| startup | `lifespan` | GET `SchedulerState` last_run_at | Table |
| startup | `needs_catchup` | now - last_run_at ≥ MONITORING_CATCHUP_THRESHOLD_DAYS 검사 | (메모리) |
| startup (catchup) | `schedule_catchup` | APScheduler date trigger 1회 추가 → 즉시 `run_monitoring_batch` 실행 | (이후 4-2 와 동일) |

---

## 5. MCP Tool 응답 페이로드

Agent 간 응답은 `job_id` + 경량 메타데이터만. Claude 가 이 응답을 받아 다음 Tool 을 호출.
RiskLevel 은 영문 enum (`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`) — 한글 변환은 UI/이메일에서.

```python
# create_analysis_job 응답
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ready",
  "company_id": "KR-0000123456",
  "input_blob_prefix": "jobs/550e8400-.../input/"
}

# collect_company_data 응답
{
  "job_id": "550e8400-...",
  "status": "collect_done",
  "company_name": "ACME",
  "company_id": "KR-0000123456",
  "news_count": 47,
  "lawsuit_count": 0,                  # TODO 1-3 보류 → 항상 0
  "financial_years": [],                # TODO 1-1-4 보류 → 항상 []
  "uploaded_files": ["사업계획서.pdf"]
}
# Claude → analyze_financials(job_id="550e8400-...")

# analyze_financials 응답
{
  "job_id": "550e8400-...",
  "status": "analyze_done",
  "risk_level": "MEDIUM",
  "risk_score": 62.4,
  "key_risk_factors": ["경쟁사 신제품", "영업이익 감소"],
  "data_gaps": ["재무제표 미확보"],
  "output_blob_path": "jobs/550e8400-.../analyze/result.json"
}
# Claude → report_generate(job_id="550e8400-...")

# report_generate 응답
{
  "job_id": "550e8400-...",
  "status": "done",
  "risk_level": "MEDIUM",
  "risk_score": 62.4,
  "report_url": "https://simsasukgo.blob.core.windows.net/.../report.md?sv=...",
  "report_blob_path": "jobs/550e8400-.../report/report.md"
}

# monitor_register 응답
{
  "company_id": "KR-0000123456",
  "company_name": "ACME",
  "recipient_email": "credit@bank.example.com",
  "is_active": true,
  "registered_at": "2026-04-29T10:00:00+00:00"
}

# monitor_run_now 응답
{
  "company_id": "KR-0000123456",
  "run_date": "2026-04-29",
  "risk_level": "HIGH",
  "previous_risk_level": "MEDIUM",
  "risk_changed": true,                 # 등급 *상승* → 알림 발송 후보
  "snapshot_blob_path": "monitoring/KR-.../20260429/snapshot.json",
  "analysis_job_id": "660e..."
}

# monitor_list 응답
{
  "targets": [
    { "company_id": "...", "company_name": "ACME", "last_risk_level": "HIGH", "last_run_at": "..." },
    ...
  ]
}
```

---

## 6. 에러 복구 전략

| 에러 상황 | 대응 |
|---|---|
| Agent 1/2/3 실패 | `AgentStatus[*]` status=failed + error_detail 기록, `AnalysisJobs` status=failed + error_message. 이전 Agent 출력 Blob 은 보존 → 실패 Agent 부터 재실행 가능 (수동) |
| 컨테이너 재시작 (분석 Job) | TODO — `lifespan` 에서 `AgentStatus.list_running()` 로 잔존 Job 감지 후 재개 (Step-1 미구현) |
| 컨테이너 재시작 (모니터링) | startup `needs_catchup` 검사 → 임계 초과 시 `schedule_catchup` 1회 실행 (구현 완료) |
| Blob 쓰기 실패 | 도메인 예외 (`StorageError` / `BlobError`) 전파 → Job/Agent failed 처리 |
| Anthropic API 실패 | `AnthropicApiError` 전파 → Job/Agent failed |
| Gmail 발송 실패 | `AlertHistory` 에 `status=failed` 기록 + 예외 전파. 자동 재시도 미구현 (TODO 4-4-5 보류) |
| 모니터링 배치 — 한 target 실패 | per-target `try/except` 로 격리. 다른 target 은 계속 진행. `monitor.batch.target_failed` 구조화 로그 |
| ACA 다중 replica 시 중복 fire | min=1 (Always-on) + AlertHistory dedup 으로 사용자 가시 중복은 차단. 분산 lock (Blob lease) 은 운영 시점 도입 |

---

## 7. 구현 모듈 구조

```
src/
├── main.py                         FastMCP SSE 진입점
├── config/
│   ├── settings.py                 pydantic-settings (env 로드)
│   └── logging.py                  structlog JSON
├── mcp/
│   ├── server.py                   FastMCP lifespan (storage/clients/scheduler/캐시 초기화)
│   └── job_tools.py                create_analysis_job
├── agents/
│   ├── collector/                  자료 수집 Agent
│   │   ├── tools.py                collect_company_data
│   │   ├── service.py
│   │   ├── schemas.py
│   │   ├── clients.py              NaverNewsClient (httpx, retry, timeout)
│   │   └── factory.py
│   ├── financial/                  재무 분석 Agent
│   │   ├── tools.py                analyze_financials
│   │   ├── service.py
│   │   ├── schemas.py
│   │   ├── prompts.py              SYSTEM_PROMPT + build_user_prompt(samples=...)
│   │   ├── templates.py            ★ Blob → 메모리 캐시 (.docx/.pdf/.xls/.xlsx)
│   │   └── factory.py
│   ├── report/                     보고서 작성 Agent
│   │   ├── tools.py                report_generate
│   │   ├── service.py
│   │   ├── schemas.py
│   │   ├── prompts.py
│   │   ├── templates.py            ★ Blob → 메모리 캐시 (.docx)
│   │   └── factory.py
│   └── monitoring/                 사후 모니터링 Agent
│       ├── tools.py                monitor_register / _deregister / _list / _run_now
│       ├── service.py              register/deregister/list 로직
│       ├── run_service.py          monitor_run_now 핵심 (collect + analyze 재실행)
│       ├── scheduler.py            APScheduler setup + run_monitoring_batch + needs_catchup
│       ├── alerter.py              should_alert / is_duplicate_alert / maybe_send_alert
│       ├── gmail_client.py         GmailClient.send_html (Blob 에서 OAuth token 로드)
│       ├── schemas.py
│       └── factory.py              GmailClient 싱글톤 + scheduler 핸들
├── storage/
│   ├── blob_store.py               BlobStore: upload / download / list_prefix / SAS
│   ├── table_store.py              TableStore: 10개 Repo (PK/RK/필드 캡슐화)
│   ├── schemas.py                  ↑ §2 모든 테이블 행 ↔ Pydantic 매핑
│   └── factory.py                  blob/table 싱글톤
└── common/
    ├── exceptions.py               SimsaSukgoError 계열
    ├── anthropic_client.py         AnthropicClient: complete_text / complete_json
    ├── constants.py                RiskLevel
    └── response.py                 공통 응답 포맷

scripts/
├── deploy_azure.sh                 idempotent ACA 수동 배포
├── init_storage.py                 Blob 컨테이너 + Table 10개 생성
└── gmail_oauth_consent.py          Gmail OAuth 1회용 consent → Blob 업로드
```

---

## 8. 환경변수 (Storage 관련)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | (필수) | Storage Account 연결 문자열 |
| `AZURE_STORAGE_BLOB_CONTAINER` | `simsasukgo` | Blob 컨테이너명 |
| `REPORT_SAMPLES_BLOB_PREFIX` | `templates/report_samples/` | 보고서 샘플 Blob prefix |
| `FINANCIAL_SAMPLES_BLOB_PREFIX` | `templates/financial_samples/` | 재무 샘플 Blob prefix |
| `REPORT_SAS_EXPIRY_HOURS` | `168` (7일) | report.md SAS URL 만료 |
| `GMAIL_CREDENTIALS_BLOB_PATH` | `credentials/gmail_oauth.json` | Gmail OAuth token Blob 경로 |
| `MONITORING_BATCH_CRON` | `0 9 1 */3 *` | 배치 cron (UTC) |
| `MONITORING_CATCHUP_THRESHOLD_DAYS` | `90` | catchup 임계 일수 |
| `MONITORING_SCHEDULER_ENABLED` | `true` | scheduler 시작 여부 |
| `ALERT_MIN_RISK_LEVEL` | `MEDIUM` | 알림 최소 등급 (이상 *상승* 시 발송) |
| `ALERT_DEDUP_DAYS` | `90` | 같은 등급 중복 발송 방지 기간 |
| `ALERT_FIRST_RUN_SEND` | `true` | 첫 실행 즉시 발송 여부 |
