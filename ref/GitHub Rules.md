# 심사숙고 MCP Server GitHub 개발 지침

> 프로젝트명: **심사숙고 : 기업 심사 리포트 Agent**  
> 개발 목적: Agentic Coding Challenge 제출용 Multi-Agent 기반 기업 심사 리포트 MCP 서버 구현  
> 서버 환경: **Python + FastMCP**  
> 연동 방향: Claude.ai에 MCP 서버를 추가하여 Agent별 Tool을 호출하고, Claude가 전체 흐름을 오케스트레이션할 수 있도록 설계

---

## 0. 프로젝트 개발 전제

본 프로젝트는 기업 심사 업무에서 반복적으로 발생하는 **자료 수집, 재무 분석, 보고서 작성, 사후 모니터링** 업무를 Multi-Agent 구조로 분리하여 자동화하는 것을 목표로 한다.

### Agent 구성

| Agent | 역할 | 주요 데이터 |
|---|---|---|
| 자료 수집 Agent | 기업 관련 자료 수집 및 정규화 | 내부 DB, Naver News API, 소송자료 API |
| 재무 분석 Agent | 재무 지표 분석 및 위험 요인 도출 | 학습 DB, 분석 지침 |
| 보고서 작성 Agent | 수집·분석 결과를 바탕으로 심사 보고서 초안 작성 | 학습 DB, 지침, 1·2번 Agent 결과 |
| 사후관리 모니터링 Agent | 3개월 단위 리스크 모니터링 및 알림 | Naver News API, 소송자료 API, Gmail 알림 |

### 개발 원칙

1. **Claude.ai는 오케스트레이터, MCP 서버는 기능 제공자 역할을 수행한다.**
2. MCP 서버 내부에서는 Agent별 기능을 Tool 단위로 명확히 분리한다.
3. 각 Tool은 하나의 명확한 책임만 가진다.
4. 실제 고객정보, 개인정보, 내부 비공개 정보는 사용하지 않는다.
5. API Key, DB 접속정보, Gmail 인증정보 등 민감정보는 GitHub에 커밋하지 않는다.
6. PoC 단계에서는 기능 완성보다 **구조 명확성, 확장성, 재현성, 보안성**을 우선한다.

---

# 1. GitHub 브랜치 규칙

## 1.1 기본 브랜치 전략

본 프로젝트는 PoC 개발 속도와 협업 안정성을 모두 고려하여 **Git Flow를 단순화한 브랜치 전략**을 사용한다.

```text
main
 └── develop
      ├── feature/*
      ├── fix/*
      ├── refactor/*
      ├── docs/*
      ├── test/*
      └── chore/*
```

| 브랜치 | 용도 | 머지 대상 |
|---|---|---|
| `main` | 최종 제출 및 배포 가능한 안정 버전 | - |
| `develop` | 개발 통합 브랜치 | `main` |
| `feature/*` | 신규 기능 개발 | `develop` |
| `fix/*` | 버그 수정 | `develop` |
| `refactor/*` | 구조 개선, 리팩토링 | `develop` |
| `docs/*` | 문서 작성 및 수정 | `develop` |
| `test/*` | 테스트 코드 추가 및 수정 | `develop` |
| `chore/*` | 설정, 의존성, 빌드 관련 작업 | `develop` |

---

## 1.2 브랜치 네이밍 규칙

브랜치명은 아래 형식을 따른다.

```text
{type}/{작업-대상}-{간단한-설명}
```

### 브랜치 타입

| 타입 | 설명 | 예시 |
|---|---|---|
| `feature` | 신규 기능 추가 | `feature/collector-naver-news-api` |
| `fix` | 오류 수정 | `fix/report-null-input-error` |
| `refactor` | 동작 변경 없는 구조 개선 | `refactor/agent-service-layer` |
| `docs` | 문서 수정 | `docs/github-guidelines` |
| `test` | 테스트 코드 작성 | `test/financial-agent-unit-test` |
| `chore` | 설정, 패키지, 환경 구성 | `chore/fastmcp-project-setup` |

### Agent별 브랜치 예시

```text
feature/collector-internal-db-tool
feature/collector-naver-news-api
feature/collector-lawsuit-api

feature/financial-ratio-analysis
feature/financial-risk-scoring

feature/report-draft-generator
feature/report-template-loader

feature/monitoring-quarterly-batch
feature/monitoring-gmail-alert

refactor/mcp-tool-registry
fix/naver-news-timeout-handling
docs/pr-template
```

---

## 1.3 브랜치 운영 규칙

### main 브랜치

`main` 브랜치는 항상 실행 가능한 상태를 유지한다.

`main` 브랜치에 직접 커밋하지 않는다.

```text
금지:
git commit -m "..."
git push origin main
```

`main` 반영은 반드시 Pull Request를 통해 진행한다.

### develop 브랜치

`develop`은 기능 통합 브랜치다.

개별 기능 개발은 반드시 별도 브랜치를 생성하여 진행한다.

```bash
git checkout develop
git pull origin develop
git checkout -b feature/collector-naver-news-api
```

### feature 브랜치

기능 단위로 작게 만든다.

하나의 브랜치에는 하나의 목적만 담는다.

좋은 예시는 다음과 같다.

```text
feature/collector-naver-news-api
```

나쁜 예시는 다음과 같다.

```text
feature/all-agents
feature/complete-server
feature/final-final
```

---

## 1.4 Pull Request 머지 기준

PR은 아래 조건을 만족해야 머지할 수 있다.

- [ ] 브랜치명이 규칙에 맞는다.
- [ ] Commit Message가 규칙에 맞는다.
- [ ] PR 템플릿이 작성되어 있다.
- [ ] 신규 Tool 추가 시 사용 예시가 포함되어 있다.
- [ ] 민감정보가 커밋되지 않았다.
- [ ] `.env.example`이 최신 상태다.
- [ ] 최소 1명 이상 코드 리뷰를 완료했다.
- [ ] 로컬에서 MCP 서버 실행이 가능하다.
- [ ] 테스트 코드 또는 수동 검증 결과가 포함되어 있다.

---

# 2. Commit Message 규칙

## 2.1 기본 형식

Commit Message는 아래 형식을 따른다.

```text
{type}: {작업 내용 요약}
```

예시:

```text
feat: Naver News API 수집 Tool 추가
fix: 기업명 미입력 시 예외 처리 추가
refactor: Agent 서비스 레이어 분리
docs: PR 템플릿 작성
test: 재무비율 계산 테스트 추가
chore: FastMCP 초기 의존성 설정
```

---

## 2.2 Commit Type

| Type | 설명 | 예시 |
|---|---|---|
| `feat` | 새로운 기능 추가 | `feat: 소송자료 API 조회 Tool 추가` |
| `fix` | 버그 수정 | `fix: 뉴스 API 응답 없음 예외 처리` |
| `refactor` | 기능 변화 없는 코드 구조 개선 | `refactor: Tool 로직을 Service 계층으로 분리` |
| `docs` | 문서 추가 또는 수정 | `docs: GitHub 개발 지침 추가` |
| `test` | 테스트 코드 추가 또는 수정 | `test: 재무 분석 Agent 단위 테스트 추가` |
| `chore` | 설정, 패키지, 빌드 등 | `chore: ruff 설정 추가` |
| `style` | 포맷팅, 세미콜론, import 정렬 등 | `style: import 순서 정리` |
| `perf` | 성능 개선 | `perf: 뉴스 수집 중복 제거 로직 개선` |
| `ci` | GitHub Actions 등 CI 설정 | `ci: PR 테스트 워크플로우 추가` |

---

## 2.3 Commit Message 작성 원칙

### 좋은 Commit Message

```text
feat: 기업명 기반 뉴스 검색 Tool 추가
fix: 빈 재무제표 입력 시 분석 중단 오류 수정
refactor: 보고서 생성 로직을 ReportService로 분리
docs: MCP 서버 실행 방법 추가
test: 부채비율 계산 케이스 추가
```

### 피해야 할 Commit Message

```text
수정
최종
진짜최종
테스트
업데이트
작업함
에러고침
```

---

## 2.4 Agent별 Commit Message 예시

### 자료 수집 Agent

```text
feat: 내부 DB 기업 기본정보 조회 Tool 추가
feat: Naver News API 검색 결과 정규화 기능 추가
feat: 소송자료 API 응답 파싱 로직 추가
fix: 뉴스 검색 결과 중복 제거 오류 수정
```

### 재무 분석 Agent

```text
feat: 부채비율 계산 기능 추가
feat: 영업현금흐름 기반 위험 신호 판단 추가
refactor: 재무 지표 계산 로직을 FinancialAnalyzer로 분리
test: 유동비율 계산 테스트 추가
```

### 보고서 작성 Agent

```text
feat: 심사 보고서 Markdown 템플릿 추가
feat: Agent 결과 기반 종합 의견 생성 기능 추가
fix: 리스크 요인 미존재 시 보고서 문구 오류 수정
```

### 사후관리 모니터링 Agent

```text
feat: 3개월 단위 모니터링 배치 Tool 추가
feat: 위험 단계 기업 Gmail 알림 기능 추가
fix: 모니터링 대상 기업 없음 예외 처리 추가
```

---

## 2.5 Commit 단위 기준

Commit은 너무 크지 않게 작성한다.

좋은 단위:

```text
1 Commit = 1 기능 또는 1 수정 목적
```

예시:

```text
feat: Naver News API 클라이언트 추가
feat: 뉴스 검색 MCP Tool 등록
test: 뉴스 검색 Tool 단위 테스트 추가
docs: 뉴스 검색 Tool 사용 예시 추가
```

피해야 하는 단위:

```text
feat: 자료수집 재무분석 보고서작성 모니터링 전부 구현
```

---

# 3. PR 템플릿

아래 내용을 `.github/pull_request_template.md` 파일로 추가한다.

```md
# Pull Request

## 1. 작업 요약

<!-- 이번 PR에서 무엇을 변경했는지 두괄식으로 작성해주세요. -->

예시:
- Naver News API를 호출하는 자료 수집 Tool을 추가했습니다.
- 수집 결과를 기업명, 제목, 게시일, 언론사, URL 기준으로 정규화했습니다.

---

## 2. 작업 유형

해당하는 항목에 체크해주세요.

- [ ] 신규 기능 추가
- [ ] 버그 수정
- [ ] 리팩토링
- [ ] 테스트 코드 추가/수정
- [ ] 문서 수정
- [ ] 환경 설정/의존성 변경
- [ ] 기타

---

## 3. 관련 Agent

해당하는 Agent에 체크해주세요.

- [ ] 자료 수집 Agent
- [ ] 재무 분석 Agent
- [ ] 보고서 작성 Agent
- [ ] 사후관리 모니터링 Agent
- [ ] 공통 MCP 서버 구조
- [ ] 기타

---

## 4. 변경 상세

<!-- 주요 변경사항을 구체적으로 작성해주세요. -->

### 추가된 기능

-

### 수정된 기능

-

### 삭제된 기능

-

---

## 5. MCP Tool 변경 여부

- [ ] 신규 Tool 추가
- [ ] 기존 Tool 수정
- [ ] 기존 Tool 삭제
- [ ] Tool 변경 없음

### Tool 이름

```text
예시: collect_naver_news
```

### Tool 설명

```text
예시: 기업명을 입력받아 Naver News API에서 관련 뉴스를 수집하고 정규화된 결과를 반환합니다.
```

### 입력값 예시

```json
{
  "company_name": "샘플기업",
  "max_results": 10
}
```

### 출력값 예시

```json
{
  "company_name": "샘플기업",
  "articles": [
    {
      "title": "샘플기업, 신규 사업 진출",
      "published_at": "2026-04-01",
      "source": "샘플뉴스",
      "url": "https://example.com/news/1",
      "summary": "샘플기업이 신규 사업에 진출했다는 내용입니다."
    }
  ]
}
```

---

## 6. 테스트 및 검증 결과

<!-- 로컬 실행, 단위 테스트, 수동 테스트 결과를 작성해주세요. -->

### 실행 명령어

```bash
uv run python src/main.py
```

또는

```bash
python src/main.py
```

### 검증 결과

- [ ] MCP 서버가 정상 실행됨
- [ ] Claude.ai에서 Tool 목록이 정상 노출됨
- [ ] 신규/수정 Tool 호출이 정상 동작함
- [ ] 예외 상황을 확인함
- [ ] 테스트 코드가 통과함

### 테스트 케이스

| 케이스 | 입력 | 기대 결과 | 결과 |
|---|---|---|---|
| 정상 입력 | `샘플기업` | 뉴스 목록 반환 | PASS |
| 빈 기업명 | `""` | 검증 오류 반환 | PASS |
| API 응답 없음 | - | 빈 목록 또는 안내 메시지 반환 | PASS |

---

## 7. 보안 점검

아래 항목을 반드시 확인해주세요.

- [ ] 실제 고객정보, 개인정보, 내부 비공개 정보를 사용하지 않았습니다.
- [ ] API Key, DB 접속정보, Gmail 인증정보를 커밋하지 않았습니다.
- [ ] `.env` 파일을 커밋하지 않았습니다.
- [ ] 필요한 환경변수는 `.env.example`에만 작성했습니다.
- [ ] 테스트 데이터는 더미/샘플 데이터입니다.
- [ ] 로그에 민감정보가 출력되지 않습니다.

---

## 8. 영향 범위

<!-- 이번 변경으로 영향을 받는 영역을 작성해주세요. -->

- 영향 받는 Agent:
- 영향 받는 Tool:
- 영향 받는 설정 파일:
- 영향 받는 테스트:

---

## 9. 리뷰어가 중점적으로 봐야 할 부분

<!-- 리뷰어가 어떤 부분을 집중해서 보면 되는지 작성해주세요. -->

-

---

## 10. 체크리스트

- [ ] 브랜치명이 규칙에 맞습니다.
- [ ] Commit Message가 규칙에 맞습니다.
- [ ] 코드 컨벤션을 준수했습니다.
- [ ] 불필요한 주석과 디버그 로그를 제거했습니다.
- [ ] 신규 기능에 대한 테스트 또는 수동 검증 결과를 작성했습니다.
- [ ] README 또는 관련 문서를 업데이트했습니다.
- [ ] PR 본문을 충분히 작성했습니다.

---

## 11. 기타 공유사항

<!-- 추가로 공유할 내용이 있다면 작성해주세요. -->

-
```

---

# 4. Code Convention

## 4.1 기본 개발 환경

### Python 버전

```text
Python 3.11 이상 권장
```

### 패키지 관리

PoC에서는 `uv` 사용을 권장한다. 단, 팀원이 익숙하지 않다면 `pip + venv`를 사용할 수 있다.

권장 방식:

```bash
uv init
uv add fastmcp pydantic python-dotenv httpx pytest ruff mypy
```

또는 일반 방식:

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastmcp pydantic python-dotenv httpx pytest ruff mypy
```

---

## 4.2 권장 프로젝트 구조

```text
simsa-sookgo/
├── .github/
│   └── pull_request_template.md
├── docs/
│   ├── GITHUB_GUIDELINES.md
│   ├── MCP_TOOL_SPEC.md
│   └── ARCHITECTURE.md
├── src/
│   ├── main.py
│   ├── config/
│   │   ├── settings.py
│   │   └── logging.py
│   ├── mcp/
│   │   ├── server.py
│   │   └── tool_registry.py
│   ├── agents/
│   │   ├── collector/
│   │   │   ├── tools.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── clients.py
│   │   ├── financial/
│   │   │   ├── tools.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── rules.py
│   │   ├── report/
│   │   │   ├── tools.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── templates.py
│   │   └── monitoring/
│   │       ├── tools.py
│   │       ├── service.py
│   │       ├── schemas.py
│   │       └── scheduler.py
│   ├── common/
│   │   ├── exceptions.py
│   │   ├── response.py
│   │   ├── constants.py
│   │   └── utils.py
│   └── data/
│       ├── sample_companies.json
│       └── sample_financials.json
├── tests/
│   ├── agents/
│   └── mcp/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 4.3 디렉터리 역할

| 경로 | 역할 |
|---|---|
| `src/main.py` | MCP 서버 실행 진입점 |
| `src/mcp/server.py` | FastMCP 서버 생성 |
| `src/mcp/tool_registry.py` | Agent별 Tool 등록 |
| `src/agents/collector` | 자료 수집 Agent |
| `src/agents/financial` | 재무 분석 Agent |
| `src/agents/report` | 보고서 작성 Agent |
| `src/agents/monitoring` | 사후관리 모니터링 Agent |
| `src/common` | 공통 예외, 응답 형식, 유틸 |
| `tests` | 테스트 코드 |
| `docs` | 설계 및 운영 문서 |

---

## 4.4 FastMCP Tool 작성 규칙

### Tool 함수 네이밍

MCP Tool 함수명은 Claude.ai에서 그대로 노출될 수 있으므로, 의미가 명확해야 한다.

```text
동사_대상_행위
```

예시:

```python
collect_company_profile
collect_naver_news
collect_lawsuit_cases
analyze_financial_ratios
detect_financial_risks
generate_credit_review_report
monitor_company_risk_status
send_gmail_risk_alert
```

피해야 할 이름:

```python
run
process
agent1
test_tool
do_analysis
final_report
```

---

## 4.5 Tool 설명 작성 규칙

Tool에는 반드시 Claude가 이해할 수 있는 설명을 작성한다.

좋은 설명 예시:

```python
@mcp.tool()
def collect_naver_news(company_name: str, max_results: int = 10) -> dict:
    """
    기업명을 기준으로 Naver News API에서 최근 뉴스를 수집합니다.

    사용 시점:
    - 기업 심사 착수 시 최근 이슈를 확인할 때
    - 보고서 작성 전 외부 리스크 요인을 수집할 때

    입력:
    - company_name: 검색할 기업명
    - max_results: 반환할 최대 뉴스 개수

    출력:
    - 기업명, 기사 제목, 게시일, 언론사, URL, 요약 목록
    """
```

---

## 4.6 Tool은 얇게, Service는 두껍게 작성한다

MCP Tool 함수에는 복잡한 로직을 넣지 않는다.

Tool은 입력 검증, Service 호출, 응답 반환만 담당한다.

### 좋은 구조

```python
@mcp.tool()
def collect_naver_news(company_name: str, max_results: int = 10) -> dict:
    request = NewsSearchRequest(
        company_name=company_name,
        max_results=max_results,
    )
    service = NewsCollectionService()
    result = service.collect(request)
    return result.model_dump()
```

### 피해야 할 구조

```python
@mcp.tool()
def collect_naver_news(company_name: str, max_results: int = 10) -> dict:
    # API URL 만들고
    # 요청 보내고
    # 응답 파싱하고
    # 중복 제거하고
    # 요약하고
    # 예외 처리하고
    # 결과 만들기까지 모두 한 함수에서 처리
```

---

## 4.7 Pydantic Schema 사용 규칙

입력과 출력은 가능한 한 Pydantic 모델로 정의한다.

### 예시

```python
from pydantic import BaseModel, Field


class NewsSearchRequest(BaseModel):
    company_name: str = Field(..., min_length=1, description="검색할 기업명")
    max_results: int = Field(default=10, ge=1, le=50, description="반환할 최대 뉴스 개수")


class NewsArticle(BaseModel):
    title: str
    published_at: str | None = None
    source: str | None = None
    url: str | None = None
    summary: str | None = None


class NewsSearchResponse(BaseModel):
    company_name: str
    articles: list[NewsArticle]
```

### 원칙

- 함수 입력값에 `dict`를 남발하지 않는다.
- 응답 구조는 Agent 간 재사용 가능하도록 명확히 만든다.
- 날짜는 가능하면 ISO 형식 문자열을 사용한다.
- Optional 필드는 `None` 가능성을 명시한다.

---

## 4.8 공통 응답 형식

Agent별 Tool 응답은 가능한 한 아래 구조를 따른다.

```json
{
  "success": true,
  "agent": "collector",
  "tool": "collect_naver_news",
  "data": {},
  "warnings": [],
  "errors": [],
  "metadata": {
    "source": "naver_news_api",
    "created_at": "2026-04-26T10:00:00+09:00"
  }
}
```

### 필드 정의

| 필드 | 설명 |
|---|---|
| `success` | Tool 수행 성공 여부 |
| `agent` | Agent 구분 |
| `tool` | 호출된 Tool 이름 |
| `data` | 실제 결과 데이터 |
| `warnings` | 치명적이지 않은 경고 |
| `errors` | 오류 메시지 |
| `metadata` | 출처, 생성시각, 호출 조건 등 |

---

## 4.9 Agent 간 데이터 전달 규칙

보고서 작성 Agent는 자료 수집 Agent와 재무 분석 Agent 결과를 입력으로 받을 수 있어야 한다.

따라서 Agent 결과는 사람이 읽기 좋은 문장만 반환하지 말고, 구조화된 JSON도 함께 반환한다.

### 좋은 예시

```json
{
  "company_name": "샘플기업",
  "risk_level": "MEDIUM",
  "risk_factors": [
    {
      "category": "뉴스",
      "title": "원자재 가격 상승 영향",
      "severity": "MEDIUM",
      "evidence": "최근 원자재 가격 상승으로 수익성 압박 가능성이 언급됨",
      "source_url": "https://example.com/news/1"
    }
  ],
  "summary": "샘플기업은 단기 유동성은 양호하나, 원자재 가격 변동에 따른 수익성 저하 가능성이 있습니다."
}
```

### 피해야 할 예시

```text
이 회사는 조금 위험해 보입니다. 뉴스를 보니 안 좋은 내용도 있습니다.
```

---

## 4.10 위험 등급 표준

Agent마다 위험도를 다르게 표현하지 않도록 공통 상수를 사용한다.

```python
class RiskLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
```

| 위험도 | 의미 | 예시 |
|---|---|---|
| `LOW` | 일반 관리 수준 | 특이사항 없음 |
| `MEDIUM` | 확인 필요 | 부정 뉴스 일부 존재 |
| `HIGH` | 심사자 검토 필요 | 소송, 실적 악화, 현금흐름 문제 |
| `CRITICAL` | 즉시 알림 필요 | 부도, 회생, 중대한 법적 리스크 |

---

## 4.11 환경변수 관리 규칙

민감정보는 반드시 `.env`에서 관리하고, GitHub에는 `.env.example`만 올린다.

### `.env.example`

```env
# Naver News API
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=

# Lawsuit API
LAWSUIT_API_BASE_URL=
LAWSUIT_API_KEY=

# Database
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASSWORD=

# Gmail
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=

# MCP
MCP_SERVER_NAME=simsa-sookgo-mcp
MCP_ENV=local
```

### `.gitignore`

```gitignore
.env
.env.*
!.env.example

__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/

.venv/
dist/
build/

*.log
*.sqlite3
*.db

.DS_Store
```

---

## 4.12 API Client 작성 규칙

외부 API 호출은 Tool에서 직접 하지 않고 Client 클래스로 분리한다.

### 예시

```python
class NaverNewsClient:
    def __init__(self, client_id: str, client_secret: str, base_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url

    def search_news(self, query: str, display: int = 10) -> dict:
        ...
```

### 원칙

- API 호출 로직은 `clients.py`에 둔다.
- timeout을 반드시 설정한다.
- API 실패 시 예외를 공통 예외로 변환한다.
- 원본 응답을 그대로 Agent에 넘기지 않고 필요한 필드만 정규화한다.
- API Key는 로그에 남기지 않는다.

---

## 4.13 예외 처리 규칙

공통 예외 클래스를 사용한다.

```python
class SimsaSookgoError(Exception):
    """심사숙고 프로젝트 공통 예외"""


class ExternalApiError(SimsaSookgoError):
    """외부 API 호출 실패"""


class ValidationError(SimsaSookgoError):
    """입력값 검증 실패"""


class ReportGenerationError(SimsaSookgoError):
    """보고서 생성 실패"""
```

### 예외 처리 원칙

- 사용자 입력 오류와 시스템 오류를 구분한다.
- 외부 API 오류는 재시도 가능 여부를 함께 남긴다.
- Tool 응답에는 내부 stack trace를 그대로 노출하지 않는다.
- Claude가 다음 행동을 판단할 수 있도록 오류 메시지는 명확히 작성한다.

좋은 오류 메시지:

```json
{
  "success": false,
  "errors": [
    {
      "code": "EMPTY_COMPANY_NAME",
      "message": "기업명이 입력되지 않았습니다. company_name 값을 입력해주세요."
    }
  ]
}
```

나쁜 오류 메시지:

```text
Error
NoneType
Something went wrong
```

---

## 4.14 로깅 규칙

### 로그 레벨

| 레벨 | 사용 기준 |
|---|---|
| `DEBUG` | 개발 중 상세 확인 |
| `INFO` | 정상 처리 흐름 |
| `WARNING` | 일부 실패했지만 진행 가능 |
| `ERROR` | 요청 처리 실패 |
| `CRITICAL` | 즉시 조치가 필요한 장애 |

### 로깅 원칙

- API Key, 비밀번호, 토큰, 개인정보는 로그에 남기지 않는다.
- 기업명은 PoC 샘플 데이터 기준으로만 로그에 남긴다.
- 외부 API 응답 전체를 그대로 로그에 남기지 않는다.
- Tool 호출 시작과 종료는 `INFO`로 남긴다.

예시:

```python
logger.info("collect_naver_news started", extra={"company_name": company_name})
logger.info("collect_naver_news completed", extra={"article_count": len(articles)})
```

---

## 4.15 코드 스타일

### Formatter / Linter

`ruff` 사용을 권장한다.

`pyproject.toml` 예시:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### 타입 힌트

모든 함수에는 가능한 한 타입 힌트를 작성한다.

좋은 예시:

```python
def calculate_debt_ratio(total_debt: float, total_equity: float) -> float:
    ...
```

피해야 할 예시:

```python
def calculate_debt_ratio(a, b):
    ...
```

---

## 4.16 함수 작성 기준

함수는 하나의 목적만 가져야 한다.

### 권장 기준

- 함수 길이: 가능하면 50줄 이내
- 인자 수: 가능하면 5개 이하
- 중복 로직은 공통 함수로 분리
- 외부 API 호출, 분석, 보고서 생성 로직을 한 함수에 섞지 않기

좋은 예시:

```python
def calculate_current_ratio(current_assets: float, current_liabilities: float) -> float:
    if current_liabilities == 0:
        return 0.0
    return round(current_assets / current_liabilities * 100, 2)
```

---

## 4.17 주석 작성 규칙

주석은 “무엇을 하는지”보다 “왜 이렇게 했는지”를 설명한다.

좋은 예시:

```python
# API 응답이 불안정할 수 있어 필수 필드만 정규화한 뒤 Agent에 전달한다.
```

피해야 할 예시:

```python
# 리스트를 반복한다.
for item in items:
    ...
```

---

## 4.18 보고서 작성 Agent Convention

보고서 작성 Agent는 단순 문장 생성이 아니라, 심사자가 검토할 수 있는 구조화된 초안을 생성해야 한다.

### 보고서 기본 구조

```md
# 기업 심사 리포트 초안

## 1. 기업 개요

## 2. 신청 목적

## 3. 주요 재무 현황

## 4. 외부 이슈 및 리스크 요인

## 5. 소송 및 법적 리스크

## 6. 추가 확인 필요사항

## 7. 종합 의견

## 8. 참고 자료
```

### 작성 원칙

- 단정적 승인/거절 표현을 피한다.
- 심사자의 판단을 대체하지 않는다.
- 근거가 있는 내용과 추정 내용을 구분한다.
- 출처가 있는 경우 URL 또는 source 정보를 함께 남긴다.
- 위험 요인은 항목화하여 제시한다.

좋은 표현:

```text
최근 부정 뉴스가 일부 확인되어 추가 확인이 필요합니다.
```

피해야 할 표현:

```text
이 기업은 대출이 불가능합니다.
```

---

## 4.19 사후관리 모니터링 Agent Convention

사후관리 모니터링 Agent는 배치성 기능이므로 실행 주기와 알림 기준이 명확해야 한다.

### 실행 주기

```text
3개월에 1회
```

### 모니터링 대상

```text
심사 완료 기업 또는 사후관리 대상 기업
```

### 알림 기준

| 조건 | 알림 여부 |
|---|---|
| `LOW` | 알림 없음 |
| `MEDIUM` | 내부 기록만 남김 |
| `HIGH` | Gmail 알림 발송 |
| `CRITICAL` | Gmail 알림 발송 및 우선 확인 대상으로 표시 |

### Gmail 알림 제목 예시

```text
[심사숙고] 사후관리 위험 신호 감지 - 샘플기업
```

### Gmail 알림 본문 예시

```text
샘플기업에 대해 사후관리 모니터링 중 위험 신호가 감지되었습니다.

- 위험 단계: HIGH
- 감지 사유: 최근 소송자료 및 부정 뉴스 증가
- 확인 필요사항:
  1. 최근 소송 내역 상세 확인
  2. 주요 거래처 변동 여부 확인
  3. 재무 상태 최신 자료 재검토

본 알림은 자동 모니터링 결과이며, 최종 판단은 담당자 검토가 필요합니다.
```

---

## 4.20 테스트 코드 작성 규칙

### 테스트 대상

우선순위는 아래와 같다.

1. 재무 지표 계산 로직
2. 위험 등급 판단 로직
3. 외부 API 응답 정규화 로직
4. 보고서 템플릿 생성 로직
5. MCP Tool 입력 검증

### 테스트 파일명

```text
test_{대상}.py
```

예시:

```text
test_financial_ratios.py
test_risk_scoring.py
test_naver_news_client.py
test_report_generator.py
```

### 테스트 함수명

```python
def test_calculate_debt_ratio_returns_percentage():
    ...
```

---

## 4.21 보안 Convention

본 프로젝트는 금융 심사 업무를 다루므로 PoC 단계에서도 보안을 기본 전제로 둔다.

### 금지 사항

- 실제 고객명, 주민등록번호, 계좌번호, 연락처 사용 금지
- 실제 내부 DB 덤프 사용 금지
- 내부 심사 양식 원본 그대로 외부 저장소 업로드 금지
- API Key, Secret, Token 커밋 금지
- Claude 프롬프트에 내부 비공개 로직 입력 금지
- 로그에 민감정보 출력 금지

### 허용 사항

- 공개 데이터
- 더미 데이터
- 비식별 샘플 데이터
- 공개 API
- 직접 만든 샘플 심사 양식
- 구조를 단순화한 데모용 DB

---

## 4.22 README 필수 포함 내용

`README.md`에는 아래 항목을 반드시 포함한다.

```md
# 심사숙고 MCP Server

## 1. 프로젝트 소개

## 2. Agent 구조

## 3. MCP Tool 목록

## 4. 로컬 실행 방법

## 5. 환경변수 설정

## 6. Claude.ai MCP 연결 방법

## 7. 테스트 방법

## 8. 보안 유의사항

## 9. 데모 시나리오
```

---

# 5. 추천 작업 순서

PoC 개발은 아래 순서로 진행한다.

## Step 1. MCP 서버 기본 골격 생성

- FastMCP 설치
- `src/main.py` 생성
- MCP 서버 실행 확인
- Claude.ai 연결 확인

## Step 2. 공통 구조 생성

- settings
- logging
- common response
- common exception
- sample data

## Step 3. 자료 수집 Agent 구현

- 내부 DB 샘플 조회 Tool
- Naver News API Tool
- 소송자료 API Tool
- 결과 정규화

## Step 4. 재무 분석 Agent 구현

- 재무비율 계산
- 위험 요인 판단
- 재무 분석 요약

## Step 5. 보고서 작성 Agent 구현

- 보고서 템플릿 정의
- 수집/분석 결과 병합
- Markdown 리포트 생성

## Step 6. 사후관리 모니터링 Agent 구현

- 모니터링 대상 조회
- 뉴스/소송자료 재조회
- 위험 단계 판단
- Gmail 알림 발송

## Step 7. PR 및 문서 정리

- README 보완
- Tool Spec 작성
- 실행 방법 정리
- 데모 시나리오 작성

---

# 6. 최종 체크리스트

개발 완료 전 아래 항목을 확인한다.

## GitHub 운영

- [ ] `main` 직접 커밋 없음
- [ ] 기능별 브랜치 분리
- [ ] Commit Message 규칙 준수
- [ ] PR 템플릿 작성
- [ ] 리뷰 후 머지

## MCP 서버

- [ ] FastMCP 서버 정상 실행
- [ ] Claude.ai에서 MCP Tool 노출
- [ ] Agent별 Tool 분리
- [ ] Tool 설명 명확성 확보
- [ ] Tool 입력/출력 구조화

## Agent 기능

- [ ] 자료 수집 Agent 동작
- [ ] 재무 분석 Agent 동작
- [ ] 보고서 작성 Agent 동작
- [ ] 사후관리 모니터링 Agent 동작
- [ ] Agent 간 결과 전달 가능

## 보안

- [ ] 실제 고객정보 미사용
- [ ] 내부 비공개 정보 미사용
- [ ] `.env` 미커밋
- [ ] `.env.example` 제공
- [ ] 로그 민감정보 제거

## 제출 준비

- [ ] README 작성
- [ ] 데모 시나리오 작성
- [ ] 샘플 데이터 준비
- [ ] 주요 Tool 사용 예시 준비
- [ ] 최종 리포트 생성 예시 준비
