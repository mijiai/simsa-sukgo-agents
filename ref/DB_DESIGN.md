# 심사숙고 — Agent 간 데이터 흐름 & Storage 설계서

## 핵심 설계 원칙

> **Agent 간에는 `job_id` 하나만 전달한다.**
> 각 Agent는 `job_id`로 Storage를 직접 읽고 쓴다.
> 대용량 JSON을 MCP Tool 응답에 담지 않는다.

---

## 1. 전체 데이터 흐름

```
사용자 입력
  - 기업명
  - 첨부 파일 (PDF / DOCX / XLSX 등)
  - 커스텀 프롬프트
        │
        ▼
┌─────────────────────────────┐
│  [Job 생성]                  │
│  AnalysisJobs 테이블 INSERT  │
│  status = "pending"          │
└────────────┬────────────────┘
             │ job_id
             ▼
┌─────────────────────────────┐
│  [파일 업로드]                │
│  Blob: jobs/{job_id}/input/ │
└────────────┬────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 1 : 자료 수집                                  │
│  READ   → Table: Companies (기업 마스터 조회)         │
│           Naver News, 소송 API                        │
│           Blob: jobs/{job_id}/input/* (업로드 파일)   │
│  WRITE  → Blob: jobs/{job_id}/collect/raw.json       │
│           Table: FinancialRaw (연도별 재무 원시 INSERT)│
│           Table: AgentStatus[collect] = done          │
└────────────┬────────────────────────────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 2 : 재무 분석                                  │
│  READ   → Blob: jobs/{job_id}/collect/raw.json       │
│           Table: FinancialRaw query PK=job_id         │
│           Azure AI Search (유사 사례)                 │
│  WRITE  → Blob: jobs/{job_id}/analyze/result.json    │
│           Table: FinancialMetrics (계산 지표 INSERT)   │
│           Table: AgentStatus[analyze] = done          │
└────────────┬────────────────────────────────────────┘
             │ job_id
             ▼
┌─────────────────────────────────────────────────────┐
│  Agent 3 : 보고서 작성                                │
│  READ   → Blob: jobs/{job_id}/collect/raw.json       │
│           Blob: jobs/{job_id}/analyze/result.json     │
│           Table: FinancialMetrics query PK=job_id     │
│           Azure AI Search (보고서 템플릿)             │
│  WRITE  → Blob: jobs/{job_id}/report/report.md       │
│           Blob: jobs/{job_id}/report/report.docx      │
│           Table: AnalysisJobs status = "done"         │
│           Table: AnalysisJobsRef UPDATE risk_level    │
└─────────────────────────────────────────────────────┘
             │
             ▼
     SAS URL 반환 → 사용자에게 보고서 링크 제공
```

---

## 2. Azure Table Storage 스키마

> 메타데이터와 상태 추적 전용. 단일 행 빠른 조회에 최적화.

---

### 테이블 1 : `AnalysisJobs`

**역할** : 분석 요청(Job) 단위의 전체 생명주기 추적

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"job"` 고정 (또는 `YYYY-MM` 월별 파티셔닝) |
| **RowKey** | String | `job_id` — UUID v4 |
| `company_name` | String | 사용자 입력 기업명 |
| `user_id` | String | 요청 사용자 식별자 |
| `status` | String | `pending` / `collecting` / `analyzing` / `reporting` / `done` / `failed` |
| `current_agent` | String | 현재 실행 중인 Agent 명 |
| `custom_prompt` | String | 사용자 지정 프롬프트 (1000자 이내) |
| `input_blob_prefix` | String | 업로드 파일 경로 prefix `jobs/{job_id}/input/` |
| `report_blob_path` | String | 완성 보고서 Blob 경로 (완료 후 기록) |
| `error_message` | String | 실패 시 에러 내용 |
| `created_at` | DateTime | Job 생성 시각 |
| `updated_at` | DateTime | 마지막 상태 변경 시각 |
| `finished_at` | DateTime | 전체 완료 시각 |

**status 상태 머신**

```
pending
  └─► collecting  (Agent 1 시작)
        └─► analyzing  (Agent 2 시작)
              └─► reporting  (Agent 3 시작)
                    └─► done      (정상 완료)
  (any) ──────────► failed    (에러 발생 시)
```

---

### 테이블 2 : `AgentStatus`

**역할** : Job 내 Agent별 실행 상태 세분화 추적 및 재시작 복원 기준

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | Agent명 — `collect` / `analyze` / `report` |
| `status` | String | `pending` / `running` / `done` / `failed` |
| `started_at` | DateTime | Agent 실행 시작 시각 |
| `finished_at` | DateTime | Agent 완료 시각 |
| `duration_sec` | Int | 소요 시간 (초) |
| `output_blob_path` | String | 이 Agent가 저장한 최종 Blob 경로 |
| `retry_count` | Int | 재시도 횟수 |
| `error_detail` | String | 실패 시 스택트레이스 요약 |

---

### 테이블 3 : `MonitoringTargets`

**역할** : 사후관리 모니터링 등록 기업 목록

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"company"` |
| **RowKey** | String | `company_id` |
| `company_name` | String | 기업명 |
| `recipient_email` | String | 알림 수신 이메일 |
| `origin_job_id` | String | 최초 보고서 생성 job_id (참조용) |
| `registered_at` | DateTime | 등록 시각 |
| `is_active` | Boolean | 활성 여부 |
| `last_run_at` | DateTime | 마지막 모니터링 실행 시각 |
| `last_risk_level` | String | 마지막 위험 등급 |

---

### 테이블 4 : `MonitoringSnapshots`

**역할** : 모니터링 실행마다 위험 상태 저장 → 이전 스냅샷 대비 변화 감지

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | 실행 일자 `YYYYMMDD` |
| `risk_level` | String | `정상` / `주의` / `경고` / `위험` |
| `news_negative_count` | Int | 부정 뉴스 건수 |
| `lawsuit_count` | Int | 소송 건수 |
| `key_signals` | String | 주요 위험 신호 요약 (JSON string) |
| `snapshot_blob_path` | String | 상세 원시 데이터 Blob 경로 |

---

### 테이블 5 : `AlertHistory`

**역할** : Gmail 알림 발송 이력 저장 — 중복 발송 방지

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | 발송 일시 `YYYYMMDD-HHmmss` |
| `risk_level` | String | 발송 당시 위험 등급 |
| `sent_to` | String | 수신자 이메일 |
| `status` | String | `sent` / `failed` |
| `gmail_message_id` | String | Gmail API 반환 메시지 ID |

---

### 테이블 6 : `SchedulerState`

**역할** : APScheduler 마지막 실행 시각 저장 → 컨테이너 재시작 시 누락 배치 복원

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"scheduler"` |
| **RowKey** | String | `"monitoring_batch"` |
| `last_run_at` | DateTime | 마지막 배치 실행 시각 |
| `next_run_at` | DateTime | 다음 예정 실행 시각 |
| `run_count` | Int | 누적 실행 횟수 |

---

### 테이블 7 : `Companies`

**역할** : 기업 마스터 데이터 — 동일 기업 반복 분석 시 재사용

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `"company"` 고정 |
| **RowKey** | String | `company_id` — UUID 또는 법인번호 |
| `company_name` | String | 기업명 |
| `business_no` | String | 사업자번호 |
| `corp_no` | String | 법인번호 |
| `industry_code` | String | 업종 코드 |
| `industry_name` | String | 업종명 |
| `created_at` | DateTime | 마스터 등록 시각 |
| `updated_at` | DateTime | 마지막 갱신 시각 |

> 동일 기업이 여러 차례 분석되어도 마스터는 1개. Agent 1이 UPSERT로 관리.

---

### 테이블 8 : `FinancialRaw`

**역할** : Agent 1이 수집한 재무제표 원시 수치 — 연도별 행

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | `{fiscal_year}-{fiscal_type}` (예: `2023-annual`) |
| `company_id` | String | 기업 식별자 (Companies 참조) |
| `fiscal_year` | Int | 결산 연도 (예: 2023) |
| `fiscal_type` | String | `annual` / `quarter` |
| `revenue` | Int64 | 매출액 (원) |
| `operating_profit` | Int64 | 영업이익 |
| `net_income` | Int64 | 당기순이익 |
| `total_assets` | Int64 | 자산총계 |
| `total_liabilities` | Int64 | 부채총계 |
| `total_equity` | Int64 | 자본총계 |
| `current_assets` | Int64 | 유동자산 |
| `current_liabilities` | Int64 | 유동부채 |
| `operating_cf` | Int64 | 영업활동현금흐름 |
| `data_source` | String | `DART` / `KISLINE` / `internal` |
| `created_at` | DateTime | 수집 시각 |

> 통화 금액은 KRW 정수(원)로 저장 — `Int64` 사용 (±9.2e18까지).
> Agent 2는 `PartitionKey=job_id` 단일 파티션 스캔으로 모든 연도 행을 빠르게 조회.

---

### 테이블 9 : `FinancialMetrics`

**역할** : Agent 2가 계산한 재무 지표 — Job·기준연도 단위

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `job_id` |
| **RowKey** | String | `str(base_year)` (예: `"2023"`) |
| `company_id` | String | 기업 식별자 |
| `base_year` | Int | 분석 기준 연도 |
| `debt_ratio` | Double | 부채비율 (%) |
| `current_ratio` | Double | 유동비율 (%) |
| `interest_coverage` | Double | 이자보상배율 (배) |
| `operating_margin` | Double | 영업이익률 (%) |
| `net_margin` | Double | 순이익률 (%) |
| `roa` | Double | 총자산이익률 (%) |
| `roe` | Double | 자기자본이익률 (%) |
| `revenue_growth` | Double | 매출 성장률 YoY (%) |
| `profit_growth` | Double | 영업이익 성장률 YoY (%) |
| `risk_level` | String | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `risk_score` | Double | 0~100 위험 점수 |
| `created_at` | DateTime | 계산 시각 |

> 비율·점수는 `Double` 저장 (Table Storage가 `DECIMAL` 미지원). 0.0001%대 미세 정밀도 손실 가능 — PoC 위험 평가에는 무시 가능 수준.

---

### 테이블 10 : `AnalysisJobsRef`

**역할** : 기업별 Job 이력 인덱스 — "이 기업의 과거 분석 목록" 빠른 조회

| 필드 | 타입 | 설명 |
|---|---|---|
| **PartitionKey** | String | `company_id` |
| **RowKey** | String | `job_id` |
| `company_name` | String | 기업명 (denormalized — UI 표시용) |
| `status` | String | `pending` / `collecting` / `analyzing` / `reporting` / `done` / `failed` |
| `risk_level` | String | 최종 위험 등급 (완료 후) |
| `created_at` | DateTime | Job 생성 시각 |
| `finished_at` | DateTime | 완료 시각 |

> `AnalysisJobs` (테이블 1) 가 PartitionKey=`"job"`이라 "특정 기업의 과거 Job" 조회는 Cross-partition scan 필요.
> 이 테이블은 그 보조 인덱스 — `company_id`로 파티셔닝하여 1회 GET으로 이력 조회.
> 무결성은 Agent 코드에서 보장 (트랜잭션 없음).

---

## 3. Azure Blob Storage 구조

> 컨테이너명 : `simsasukgo`
> 모든 Agent 데이터는 `job_id`를 루트로 하는 prefix 아래 저장

```
simsasukgo/                              Blob 컨테이너
│
├── jobs/
│   └── {job_id}/
│       │
│       ├── input/                       사용자 업로드 파일
│       │   ├── {파일명1.pdf}
│       │   ├── {파일명2.xlsx}
│       │   └── prompt.txt               커스텀 프롬프트 원문
│       │
│       ├── collect/                     Agent 1 출력
│       │   └── raw.json
│       │       {
│       │         "company_id": "...",
│       │         "company_name": "...",
│       │         "news": [ {title, url, date, is_negative}, ... ],
│       │         "lawsuits": [ {case_no, name, status, amount}, ... ],
│       │         "uploaded_files_parsed": [ {filename, text_content}, ... ],
│       │         "db_summary": { "years": [...], "sources": [...] },
│       │         "collected_at": "2026-04-26T09:00:00Z"
│       │       }
│       │
│       ├── analyze/                     Agent 2 출력
│       │   └── result.json
│       │       {
│       │         "risk_level": "주의",
│       │         "risk_score": 62.4,
│       │         "financial_summary": { "debt_ratio": 180.2, ... },
│       │         "risk_factors": [ "소송 2건 진행 중", "영업이익 YoY -30%" ],
│       │         "insights": "종합 분석 서술...",
│       │         "similar_cases": [ {case_id, similarity, summary}, ... ],
│       │         "analyzed_at": "2026-04-26T09:03:00Z"
│       │       }
│       │
│       └── report/                      Agent 3 출력
│           ├── report.md                최종 보고서 Markdown
│           └── report.docx              (옵션) DOCX 변환본
│
├── monitoring/
│   └── {company_id}/
│       └── {YYYYMMDD}/
│           └── snapshot.json            모니터링 상세 원시 데이터
│
└── credentials/
    └── gmail_oauth.json                 Gmail OAuth 자격증명 (접근 제한 필수)
```

**Blob 경로 명명 규칙**

| 용도 | 경로 패턴 |
|---|---|
| 사용자 업로드 파일 | `jobs/{job_id}/input/{filename}` |
| 커스텀 프롬프트 | `jobs/{job_id}/input/prompt.txt` |
| Agent 1 출력 | `jobs/{job_id}/collect/raw.json` |
| Agent 2 출력 | `jobs/{job_id}/analyze/result.json` |
| 최종 보고서 MD | `jobs/{job_id}/report/report.md` |
| 최종 보고서 DOCX | `jobs/{job_id}/report/report.docx` |
| 모니터링 스냅샷 | `monitoring/{company_id}/{YYYYMMDD}/snapshot.json` |

---

## 4. Agent별 Storage Read / Write 매핑

| 시점 | 주체 | Action | Storage |
|---|---|---|---|
| 분석 버튼 클릭 | 서버 | INSERT `AnalysisJobs` status=pending | Table |
| 분석 버튼 클릭 | 서버 | INSERT `AnalysisJobsRef` (PK=company_id) | Table |
| 파일 업로드 | 서버 | PUT `jobs/{job_id}/input/*` | Blob |
| 커스텀 프롬프트 | 서버 | PUT `jobs/{job_id}/input/prompt.txt` | Blob |
| Agent 1 시작 | Agent 1 | UPDATE `AnalysisJobs` status=collecting | Table |
| Agent 1 시작 | Agent 1 | INSERT `AgentStatus[collect]` status=running | Table |
| Agent 1 실행 | Agent 1 | GET `jobs/{job_id}/input/*` (파일 파싱) | Blob |
| Agent 1 실행 | Agent 1 | UPSERT `Companies` (기업 마스터 등록/갱신) | Table |
| Agent 1 완료 | Agent 1 | PUT `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 1 완료 | Agent 1 | INSERT `FinancialRaw` 행들 (PK=job_id) | Table |
| Agent 1 완료 | Agent 1 | UPDATE `AgentStatus[collect]` status=done | Table |
| Agent 2 시작 | Agent 2 | UPDATE `AnalysisJobs` status=analyzing | Table |
| Agent 2 시작 | Agent 2 | INSERT `AgentStatus[analyze]` status=running | Table |
| Agent 2 실행 | Agent 2 | GET `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 2 실행 | Agent 2 | Query `FinancialRaw` PK=job_id | Table |
| Agent 2 실행 | Agent 2 | SEARCH 유사 사례 | AI Search |
| Agent 2 완료 | Agent 2 | PUT `jobs/{job_id}/analyze/result.json` | Blob |
| Agent 2 완료 | Agent 2 | INSERT `FinancialMetrics` (PK=job_id, RK=base_year) | Table |
| Agent 2 완료 | Agent 2 | UPDATE `AgentStatus[analyze]` status=done | Table |
| Agent 3 시작 | Agent 3 | UPDATE `AnalysisJobs` status=reporting | Table |
| Agent 3 시작 | Agent 3 | INSERT `AgentStatus[report]` status=running | Table |
| Agent 3 실행 | Agent 3 | GET `jobs/{job_id}/collect/raw.json` | Blob |
| Agent 3 실행 | Agent 3 | GET `jobs/{job_id}/analyze/result.json` | Blob |
| Agent 3 실행 | Agent 3 | Query `FinancialMetrics` PK=job_id | Table |
| Agent 3 실행 | Agent 3 | SEARCH 보고서 템플릿 | AI Search |
| Agent 3 완료 | Agent 3 | PUT `jobs/{job_id}/report/report.md` | Blob |
| Agent 3 완료 | Agent 3 | GENERATE SAS URL (report.md, report.docx) | Blob |
| Agent 3 완료 | Agent 3 | UPDATE `AnalysisJobs` status=done, report_blob_path=? | Table |
| Agent 3 완료 | Agent 3 | UPDATE `AgentStatus[report]` status=done | Table |
| Agent 3 완료 | Agent 3 | UPDATE `AnalysisJobsRef` risk_level=?, finished_at=? | Table |

---

## 5. MCP Tool 간 실제 전달 페이로드

Agent 간에 오가는 데이터는 `job_id` + 경량 메타데이터만.
Claude는 이 응답을 받아 다음 Tool을 호출한다.

```python
# Agent 1 (collect_company_data) 응답 예시
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "collect_done",
  "company_name": "삼성전자",
  "company_id": "KR-0000123456",
  "news_count": 47,
  "lawsuit_count": 2,
  "financial_years": [2021, 2022, 2023],
  "uploaded_files": ["사업계획서.pdf", "재무제표.xlsx"]
}
# Claude → analyze_financials(job_id="550e8400-...")

# Agent 2 (analyze_financials) 응답 예시
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "analyze_done",
  "risk_level": "주의",
  "risk_score": 62.4,
  "key_risk_factors": ["소송 2건 진행 중", "영업이익 YoY -30%"]
}
# Claude → report_generate(job_id="550e8400-...")

# Agent 3 (report_generate) 응답 예시
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "done",
  "report_url":  "https://simsasukgo.blob.core.windows.net/.../report.md?sv=...&sig=...",
  "docx_url":    "https://simsasukgo.blob.core.windows.net/.../report.docx?sv=...&sig=..."
}
```

---

## 6. 에러 복구 전략

| 에러 상황 | 대응 |
|---|---|
| Agent 1 실패 | `AgentStatus[collect]` status=failed 기록, `AnalysisJobs` status=failed. job_id로 해당 Agent만 재실행 가능 |
| Agent 2/3 실패 | 이전 Agent 출력 Blob은 보존 → 실패 Agent부터만 재실행 (`retry_from="analyze"`) |
| 컨테이너 재시작 | 서버 기동 시 `AgentStatus` 테이블에서 status=running 인 Job 감지 → 해당 Agent부터 자동 재개 |
| Blob 쓰기 실패 | 재시도 3회 후 에러를 `AgentStatus.error_detail`에 기록, Job을 failed 처리 |

---

## 7. 구현 참고 — 모듈 구조

```
/storage
  ├── blob_store.py      # Blob 업로드/다운로드/SAS URL 생성
  └── table_store.py     # Table Storage CRUD (10개 테이블)
                         #   - 운영: AnalysisJobs, AgentStatus
                         #   - 정형: Companies, FinancialRaw, FinancialMetrics, AnalysisJobsRef
                         #   - 모니터링: MonitoringTargets, MonitoringSnapshots, AlertHistory
                         #   - 인프라: SchedulerState

/agents
  ├── collect.py         # job_id 받아 Blob + Table 쓰기
  ├── analyze.py         # job_id로 Blob + Table 읽기 → 분석 후 저장
  └── report.py          # job_id로 Blob + Table 읽기 → 보고서 생성 후 저장

# 각 Agent Tool 시그니처 (job_id만 받는다)
@mcp.tool()
async def collect_company_data(job_id: str, company_name: str) -> dict: ...

@mcp.tool()
async def analyze_financials(job_id: str) -> dict: ...

@mcp.tool()
async def report_generate(job_id: str) -> dict: ...
```

> Azure SQL · CosmosDB는 사용하지 않는다. 정형 데이터도 Table Storage로 통합 — PoC 인프라 단순화 목적.
