# TODO_upgrade.md — 심사숙고 Report Agent 고도화 작업 지시서

> 이 문서는 기존 `/ref/TODO.md` 의 Step 5(보고서 작성 Agent)가 완성된 이후 진행하는 후속 업그레이드 작업이다.
> 작업 시작 전 `CLAUDE.md`, `ref/TODO.md`, `ref/DB_DESIGN.md`, `ref/GitHub_Rules.md` 를 먼저 확인한다.
> 모든 PR은 `feature/upgrade-*` prefix 로 분리해 진행한다.

---

## 0. 목표

현재 report agent 는 단일 LLM 호출로 Markdown 한 덩어리를 생성한다.
정식 여신심사 양식 수준의 결과물을 만들기 위해 **multi-agent 파이프라인을 다음 세 가지가 가능하도록 재설계**한다.

1. **양식 보존** — 정형 섹션·표 컬럼·출처 라벨이 매번 동일하게 유지되어야 한다.
2. **원자료 그대로 삽입** — 차주가 업로드한 엑셀의 표·PDF의 텍스트·이미지가 본문에 그대로 들어가야 한다 (LLM이 표를 새로 만들지 않는다).
3. **심사 평가 작성** — 각 표 아래 2~5줄의 분석 bullet 이 자동으로 붙어야 한다.

**레퍼런스 양식**: `㈜예시회사캐피탈 사모사채 여신승인신청서` (8개 섹션 + Check List + 별첨, 표 31개 + 이미지 7개).

---

## 1. 핵심 아키텍처 결정 — Option B

표 아래 bullet 평가(예: "‘24년 자산 6,040억원으로 전년 대비 389억원 증가...")는 **금융 분석가의 판단** 영역이므로 **Financial agent 가 작성**한다. Report agent 는 이를 받아 **렌더링만** 한다.

```
[Collector]                 [Financial]                       [Report]
파일/뉴스 추출       →     글로벌 위험 + 섹션별 평가     →     템플릿 슬롯에 끼워 넣고 docx 빌드
                                                              (LLM은 표를 만들지 않음)
```

이 결정에 따라 책임이 명확히 분리된다:

| Agent | 책임 | 산출물 |
|---|---|---|
| Collector | 외부/업로드 자료 → 구조화된 raw 데이터 | `raw.json` (extracted_tables/images/docs 포함) |
| Financial | raw → 분석 결과 + 섹션별 평가 bullet | `result.json` (section_insights 포함) |
| Report | raw + result → 정식 양식 docx | `report.docx` + `report.md` |

---

## 2. Agent 별 변경 매트릭스

| Agent | 변경 필요? | 영향 범위 | 비고 |
|---|---|---|---|
| **collector** | **필수** | extractors 신규, service/schemas 수정 | xlsx/pdf/image 추출 |
| **financial** | **필수 (Option B 핵심)** | prompts/schemas/service 수정 | section_insights 출력 |
| **report** | **필수 (대규모 재작성)** | template_spec/planner/sections/renderer 신규 | docx 직접 빌드 |
| **monitoring** | 변경 없음 | — | 글로벌 risk 만 사용, 호환성 유지 |
| **create_analysis_job** | 변경 없음 | — | 업로드 파이프라인 그대로 |
| **storage** | 변경 없음 | — | BlobStore/TableStore 인터페이스 충분 |
| **common/constants** | 소량 추가 | `ReportSection` enum | financial ↔ report 공유 키 |

---

## 3. PR 분리 및 의존 순서

PR 의 의존 관계가 명확하므로 반드시 아래 순서로 진행한다.

```
PR1 [collector]   extractors 모듈 + raw.json 확장
                  └─ raw.json 새 필드가 있어야 financial 이 활용 가능
PR2 [financial]   prompts 에 extracted_* 주입 + section_insights 출력
                  └─ result.json 의 section_insights 가 있어야 report 가 효율적으로 동작
PR3 [report]      template_spec + planner + section writer + docx renderer
PR4 [report]      예시 docx 스타일 상속, data_gap 처리 정교화 (선택)
```

각 PR 단독으로도 가치가 있다 (PR1 만 적용해도 financial 분석 품질이 올라간다). 점진 배포 친화적으로 설계되었다.

---

## 4. PR1 — Collector: 업로드 파일 구조화 추출

### 4.1 목적

현재 `_list_uploaded_files` 가 파일 **이름**만 모은다. 업로드된 xlsx/pdf/image 의 **내용**을 추출해 raw.json 안에 구조화된 형태로 박아 넣는다.

### 4.2 신규 파일

#### `src/agents/collector/extractors.py`

```python
"""업로드 파일을 종류별로 구조화 추출하는 모듈."""
from typing import Any
from src.storage.blob_store import BlobStore


async def extract_xlsx(blob: BlobStore, blob_path: str) -> list[dict[str, Any]]:
    """엑셀 파일을 시트별로 추출.

    Returns:
        [{"sheet_name": str, "columns": list[str], "rows": list[list[Any]]}, ...]

    구현 메모:
    - openpyxl 사용
    - 각 시트의 1행을 columns 로, 나머지를 rows 로 처리
    - 빈 셀은 None
    - 시트당 최대 200행까지만 (token 폭증 방지). 초과 시 truncated=True 플래그 추가
    - 결제용 스타일/병합 셀은 무시 (값만 추출)
    """


async def extract_pdf(blob: BlobStore, blob_path: str) -> dict[str, Any]:
    """PDF 텍스트 + 표 추출.

    Returns:
        {
            "text": str,           # 페이지별로 \n\n 으로 분리
            "tables": list[dict],  # extract_xlsx 와 동일 포맷
            "page_count": int,
        }

    구현 메모:
    - pdfplumber 사용
    - text 는 최대 10,000자까지 (초과 시 truncated=True)
    - 표는 page.extract_tables() 결과를 normalize
    """


async def extract_image(
    blob: BlobStore, blob_path: str, original_filename: str
) -> dict[str, Any]:
    """이미지 메타데이터 추출 (LLM Vision 미사용 — 비용/지연 회피).

    Returns:
        {
            "blob_path": str,           # input/ 경로 그대로 (report 가 다시 다운로드)
            "original_filename": str,
            "suspected_role": str,      # 휴리스틱 분류
            "width": int, "height": int,
        }

    suspected_role 휴리스틱:
    - 파일명에 "구조도|ownership|chart|조직" 포함 → "ownership_chart"
    - 파일명에 "상품|product|catalog" 포함 → "product_catalog"
    - 그 외 → "unknown"

    실제 caption 생성은 PR4 에서 Claude Vision 으로 추가 (선택).
    """


async def extract_uploaded_file(
    blob: BlobStore, blob_path: str, original_filename: str
) -> dict[str, Any] | None:
    """확장자 기반 dispatcher. 인식 못하면 None 반환.

    Returns:
        {"kind": "xlsx"|"pdf"|"image", "filename": str, "content": <위 함수 결과>}
    """
```

### 4.3 수정 파일

#### `src/agents/collector/schemas.py`

```python
class ExtractedTable(BaseModel):
    source_file: str          # 어느 업로드 파일에서 나온 표인지
    sheet_name: str | None = None
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


class ExtractedImage(BaseModel):
    source_file: str
    blob_path: str            # input/ 경로 그대로
    suspected_role: str       # ownership_chart, product_catalog, unknown
    width: int | None = None
    height: int | None = None


class ExtractedDoc(BaseModel):
    source_file: str
    text: str                 # PDF 텍스트
    page_count: int = 0
    truncated: bool = False


class CollectResponse(BaseModel):
    # 기존 필드 유지
    job_id: str
    status: Literal["collect_done"] = "collect_done"
    company_name: str
    company_id: str | None = None
    news_count: int
    lawsuit_count: int = 0
    financial_years: list[int] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list)
    output_blob_path: str
    # 신규 카운트 필드
    extracted_table_count: int = 0
    extracted_image_count: int = 0
    extracted_doc_count: int = 0
```

#### `src/agents/collector/service.py`

`_list_uploaded_files` 는 그대로 두되, **추가로** 각 파일을 다운로드해 extractor 로 처리. raw_payload 에 다음 필드 추가:

```python
raw_payload = {
    # ... 기존 필드 ...
    "uploaded_files": uploaded_files,            # 그대로 (이름 목록)
    "extracted_tables": [t.model_dump() for t in extracted_tables],
    "extracted_images": [i.model_dump() for i in extracted_images],
    "extracted_docs":   [d.model_dump() for d in extracted_docs],
    "financial_years": _infer_financial_years(extracted_tables),  # xlsx 에서 자동 추론
}
```

`financial_years` 추론 규칙: extracted_tables 의 columns 에서 4자리 연도(2018~현재) 패턴을 추출. 없으면 빈 리스트.

extractor 가 raise 하면 해당 파일만 skip 하고 `logger.warning("collect.extract.failed", file=...)` 로 남긴다. 전체 collect 는 실패시키지 않는다.

### 4.4 의존성

`pyproject.toml` 에 추가:

```toml
openpyxl = "^3.1"
pdfplumber = "^0.11"
Pillow = "^10.4"   # 이미지 메타데이터 (width/height)
```

### 4.5 테스트 (`tests/agents/collector/`)

- `test_extractors.py`
  - `test_extract_xlsx_basic`: 2시트 xlsx fixture → 시트별 columns/rows 분리 확인
  - `test_extract_xlsx_truncation`: 300행 시트 → truncated=True
  - `test_extract_pdf_text_and_tables`: 표 1개 + 텍스트 fixture
  - `test_extract_image_metadata`: PNG 한 장 → width/height/role 매핑
  - `test_extract_uploaded_file_dispatcher`: 확장자별 분기 + 미지원 확장자 None 반환
- `test_service_extracted_payload.py`
  - mock blob 에 xlsx + png 업로드 → `raw.json` 에 extracted_tables/images 들어가는지
  - 추출 실패 파일이 있어도 collect 는 성공하는지

### 4.6 Acceptance Criteria

- [ ] `raw.json` 에 `extracted_tables`, `extracted_images`, `extracted_docs` 가 항상 존재 (빈 리스트라도)
- [ ] `financial_years` 가 더 이상 항상 비어있지 않음 (xlsx 업로드 시 자동 채움)
- [ ] 단일 파일 추출 실패가 collect 전체를 실패시키지 않음
- [ ] `CollectResponse` 에 `extracted_*_count` 필드 추가
- [ ] 기존 monitoring 테스트가 그대로 통과 (호환성)

---

## 5. PR2 — Financial: section_insights 출력

### 5.1 목적

result.json 에 섹션별 평가 bullet 을 추가한다. report agent 가 이를 그대로 표 아래에 끼워 넣는다.

### 5.2 신규/수정 파일

#### `src/common/constants.py` (신규 enum)

```python
from enum import StrEnum


class ReportSection(StrEnum):
    """보고서 정형 섹션 ID. financial.section_insights 와 report.template_spec 가 공유."""
    OVERVIEW = "1_overview"            # 차주 개요
    LOAN_SUMMARY = "2_loan_summary"    # 여신 개요
    LOAN_TERMS = "3_loan_terms"        # 여신 신청 조건
    BUSINESS = "4_business"            # 사업 현황
    FINANCE = "5_finance"              # 재무 현황
    DEBT = "6_debt"                    # 차입금 현황
    LIQUIDITY = "7_liquidity"          # 유동성 현황
    AFFILIATES = "8_affiliates"        # 관계사 현황
    CONCLUSION = "9_conclusion"        # 종합 의견
```

#### `src/agents/financial/schemas.py` (수정)

```python
from src.common.constants import ReportSection


class SectionInsight(BaseModel):
    """보고서 섹션 단위 분석 인사이트.

    report agent 가 해당 섹션의 표 아래 bullet 으로 그대로 사용한다.
    """
    section_id: ReportSection
    table_id: str | None = None        # 같은 섹션 내 여러 표 구분용 (예: "재무제표", "자산건전성")
    bullets: list[str] = Field(min_length=1, max_length=5)
    cited_data_points: list[str] = Field(
        default_factory=list,
        description="bullet 의 근거가 된 수치/사실 (감사 추적용)",
    )


class ClaudeJudgment(BaseModel):
    """Claude LLM 응답 — section_insights 추가."""
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=100.0)
    summary: str
    key_risk_factors: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    section_insights: list[SectionInsight] = Field(default_factory=list)


class AnalysisInputSummary(BaseModel):
    news_count: int = 0
    lawsuit_count: int = 0
    uploaded_file_count: int = 0
    financial_years: list[int] = Field(default_factory=list)
    extracted_table_count: int = 0    # 신규
    extracted_image_count: int = 0    # 신규


class AnalysisResult(BaseModel):
    # ... 기존 필드 그대로 ...
    section_insights: list[SectionInsight] = Field(default_factory=list)  # 신규
```

#### `src/agents/financial/prompts.py` (수정)

system prompt 에 section_insights 작성 지시 추가, user prompt 에 extracted_tables 주입.

```python
SYSTEM_PROMPT = """당신은 한국 시중은행의 기업여신 심사 분석가입니다.
... (기존 4단계 위험 등급 설명 그대로) ...

추가 작업: 보고서 섹션별 평가 bullet 작성
주어진 자료를 바탕으로 다음 보고서 섹션 각각에 대해 표 아래 bullet 평가를
작성합니다. 각 bullet 은 표의 숫자/사실을 근거로 한 1~2문장이며,
"전년 대비 ~증가/감소", "안정적", "리스크 헷지" 같은 정형 표현을 사용합니다.

섹션 ID 와 매핑:
- 4_business : 사업 현황 (영업자산, 인수율, 예시회사 판매현황 등)
- 5_finance  : 재무 현황 (재무제표, 자산건전성, 자본적정성)
- 6_debt     : 차입금 현황
- 7_liquidity: 유동성 현황 (만기구조, 자금수지)
- 8_affiliates: 관계사 현황
- 9_conclusion: 종합 의견

자료가 부족한 섹션은 section_insights 에서 생략합니다.
한 섹션 안에 여러 표가 있으면 table_id 를 다르게 채워 분리합니다.

응답 JSON 스키마 (다른 출력 금지):
{
  "risk_level": "...", "risk_score": 0~100,
  "summary": "...", "key_risk_factors": [...],
  "positive_signals": [...], "data_gaps": [...],
  "section_insights": [
    {"section_id": "5_finance", "table_id": "재무제표",
     "bullets": ["...", "..."], "cited_data_points": ["자산총계 6,040억"]},
    ...
  ]
}
"""


def build_user_prompt(*, company_name: str, raw: dict[str, Any]) -> str:
    """user prompt 확장 — extracted_tables 주입.

    extracted_tables 는 토큰 폭증을 막기 위해 다음 규칙으로 압축:
    - 시트 컬럼명 + 처음 20행만 포함
    - 20행 초과 시 "(이하 N행 생략)" 표기
    - 한 prompt 에 최대 8개 표까지 (그 이상은 우선순위 휴리스틱으로 컷)

    raw.extracted_images 는 메타정보만 (역할 분류 기반 placeholder),
    raw.extracted_docs 는 첫 2,000자만 인용.
    """
```

#### `src/agents/financial/service.py` (수정)

거의 변경 없음. `ClaudeJudgment.section_insights` 를 그대로 `AnalysisResult` 에 옮기는 한 줄 추가, `AnalysisInputSummary` 의 신규 카운트 필드 채우기.

#### `src/agents/financial/factory.py` (검토)

`max_tokens` 가 settings 에서 오는데, section_insights 추가로 출력이 길어질 수 있으므로 `settings.anthropic_max_tokens` 기본값을 검토 (예: 4096 → 8192). `.env.example` 에도 반영.

### 5.3 테스트

- `tests/agents/financial/test_prompts.py`
  - extracted_tables 가 user prompt 에 정상 직렬화되는지
  - 행 truncation 동작 확인
- `tests/agents/financial/test_service.py`
  - mock anthropic 가 section_insights 포함 JSON 반환 → AnalysisResult 에 그대로 전파
  - section_insights 비어있어도 service 정상 동작 (하위 호환)

### 5.4 Acceptance Criteria

- [ ] `result.json` 에 `section_insights` 필드 존재 (빈 리스트 가능)
- [ ] 각 insight 의 `section_id` 가 `ReportSection` enum 값과 일치
- [ ] 기존 `risk_level/risk_score/summary/key_risk_factors/data_gaps` 필드 의미 변동 없음
- [ ] monitoring 의 alerter/run_service 가 변경 없이 정상 동작 (section_insights 무시)

---

## 6. PR3 — Report: planner + section writer + docx renderer

### 6.1 목적

기존 단일 LLM 호출을 폐기하고, **데이터 매핑 → 섹션별 렌더 → docx 빌드** 의 3단계 파이프라인으로 재작성한다. LLM은 표를 만들지 않는다.

### 6.2 신규 파일

#### `src/agents/report/template_spec.py`

```python
"""보고서 템플릿 정형 스펙. 한 번 정의하면 모든 보고서가 같은 양식으로 나온다."""
from pydantic import BaseModel
from src.common.constants import ReportSection


class TableSpec(BaseModel):
    table_id: str                # SectionInsight.table_id 와 매칭되는 키
    title: str                   # "주요 재무 지표" 등 화면 출력용
    columns: list[str] | None = None  # None 이면 데이터 소스의 컬럼 그대로 사용
    source_label: str            # "※출처: 업무보고서 및 금융통계정보시스템"
    data_source: str             # plan 에서 어떤 키를 보고 데이터를 끌어올지 식별자
                                 # 예: "extracted_tables.재무제표", "raw.news_top10"
    commentary_required: bool = True  # 표 아래 bullet 평가 작성 여부


class ImageSlot(BaseModel):
    image_id: str                # "ownership_chart", "product_catalog_general" 등
    title: str
    suspected_role: str          # extracted_images.suspected_role 와 매칭
    optional: bool = True        # 미제출 시 "[자료 미확보]" 처리할지 섹션 자체를 생략할지


class SectionSpec(BaseModel):
    section_id: ReportSection
    number: str                  # "5"
    title: str                   # "재무 현황"
    tables: list[TableSpec] = []
    images: list[ImageSlot] = []


class ReportTemplate(BaseModel):
    name: str                    # "loan_application_v1"
    sections: list[SectionSpec]


def load_template(name: str = "loan_application_v1") -> ReportTemplate:
    """yaml 파일에서 템플릿 로드 + 캐시."""
```

#### `src/agents/report/templates/loan_application_v1.yaml` (신규)

예시회사 예시 문서 8섹션을 그대로 옮긴 yaml. 작업자가 한 번만 만들면 됨.
필수 섹션 / 표 / 이미지 슬롯 정의. `data_source` 키는 planner 가 채울 슬롯 이름.

> 이 yaml 작성 시 예시회사 예시 문서를 직접 참고. 표마다 source_label 까지 그대로 복사.

#### `src/agents/report/planner.py`

```python
"""LLM Plan 단계 — raw + analysis + template_spec → 슬롯 매핑 JSON."""
from src.agents.report.template_spec import ReportTemplate
from src.common.anthropic_client import AnthropicClient


PLANNER_SYSTEM = """당신은 여신 심사 보고서 데이터 매핑 플래너입니다.
주어진 raw, analysis, 템플릿 스펙을 보고
각 섹션의 각 표/이미지 슬롯에 어떤 데이터를 넣을지 매핑만 하세요.

규칙:
- 실제 문장은 작성하지 않습니다. 데이터 매핑 JSON 만 출력합니다.
- 자료가 없으면 해당 슬롯에 "data_gap": true 로 표시합니다.
- 표 데이터는 raw.extracted_tables 에서 가장 적합한 것을 선택해 columns/rows 를 그대로 인용합니다.
- 새 숫자를 만들지 않습니다. extracted_tables 에 없는 수치는 절대 채우지 않습니다.
"""


async def run_planner(
    template: ReportTemplate,
    raw: dict,
    analysis: dict,
    anthropic: AnthropicClient,
) -> dict:
    """반환 형태:
    {
      "5_finance": {
        "tables": {
          "재무제표": {
            "data_gap": false,
            "columns": [...], "rows": [[...], ...],
            "source_label_override": null
          },
          ...
        },
        "images": {...}
      },
      ...
    }
    """
```

planner 출력은 strict pydantic 모델로 검증. 환각/스키마 위반 시 그 슬롯만 `data_gap=true` 로 fallback.

#### `src/agents/report/sections.py`

```python
"""섹션별 컨텐츠 조립. LLM 호출 없음 — financial.section_insights 를 그대로 사용."""


class SectionContent(BaseModel):
    section_id: ReportSection
    tables: dict[str, dict]           # table_id -> {columns, rows, source_label, bullets}
    images: dict[str, dict]           # image_id -> {blob_path, caption}


def build_section_content(
    spec: SectionSpec,
    plan: dict,                       # planner 의 해당 섹션 출력
    section_insights: list[SectionInsight],  # financial.result.section_insights
) -> SectionContent:
    """plan 의 데이터 슬롯에 financial 의 bullet 을 매칭해 합친다.

    매칭 규칙:
    - section_insights 에서 section_id 가 일치하는 항목들을 가져옴
    - table_id 가 같은 insight 의 bullets 를 해당 표 아래에 배치
    - table_id 가 None 인 insight 는 섹션 마지막에 일반 bullet 으로
    - 어떤 표에 매칭되는 insight 가 없으면 bullets=[] (표만 표시)
    """
```

#### `src/agents/report/renderer.py`

```python
"""python-docx 로 .docx 직접 빌드. Markdown 변환 함수도 같이."""
from io import BytesIO
from docx import Document
from docx.shared import Cm, Pt


async def render_report_docx(
    template: ReportTemplate,
    sections: dict[ReportSection, SectionContent],
    raw: dict,
    blob: BlobStore,
    base_template_path: str | None = None,  # PR4 에서 활용
) -> bytes:
    """반환: docx 바이트.

    구현 메모:
    - base_template_path 가 주어지면 Document(base) 로 열어 스타일 상속,
      아니면 Document() 새로 생성
    - 섹션 헤딩: f"{spec.number}. {spec.title}" (Heading 1)
    - 표: spec.columns 또는 plan 의 columns 로 헤더 만들고 rows 채움
          (테두리/배경색은 예시회사 예시와 유사하게)
    - 출처 라벨: 표 바로 아래 italic 작은 글씨
    - bullets: section_insights 의 문장을 일반 bullet 리스트로
    - 이미지: blob.download(blob_path) 후 add_picture(width=Cm(12))
              미제출 시 "[자료 미확보 — {역할}]" 텍스트 placeholder
    """


def render_report_markdown(
    template: ReportTemplate,
    sections: dict[ReportSection, SectionContent],
) -> str:
    """기존 호환용 Markdown export. 표는 GFM 표, 이미지는 placeholder 텍스트."""
```

#### `src/agents/report/schemas.py` (수정)

```python
class ReportResponse(BaseModel):
    job_id: str
    status: Literal["done"] = "done"
    risk_level: RiskLevel
    risk_score: float
    report_url: str               # md SAS URL (호환)
    report_blob_path: str         # md
    docx_url: str                 # 신규 — docx SAS URL
    docx_blob_path: str           # 신규
```

#### `src/agents/report/service.py` (대규모 재작성)

```python
async def report_generate_service(...):
    # 1. 데이터 로드
    raw = json.loads(await blob.download(_raw_blob_path(job_id)))
    analysis = json.loads(await blob.download(_result_blob_path(job_id)))

    # 2. 템플릿 로드 (캐시됨)
    template = load_template("loan_application_v1")

    # 3. Planner LLM (1회 호출)
    plan = await run_planner(template, raw, analysis, anthropic)

    # 4. 섹션 컨텐츠 조립 (LLM 호출 없음)
    section_insights = [SectionInsight(**si) for si in analysis.get("section_insights", [])]
    sections = {
        spec.section_id: build_section_content(spec, plan[spec.section_id], section_insights)
        for spec in template.sections
    }

    # 5. docx + md 동시 생성
    docx_bytes = await render_report_docx(template, sections, raw, blob)
    md_text = render_report_markdown(template, sections)

    # 6. 두 파일 모두 업로드
    docx_path = f"jobs/{job_id}/report/report.docx"
    md_path = f"jobs/{job_id}/report/report.md"
    await blob.upload(docx_path, docx_bytes,
                      content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    await blob.upload(md_path, md_text.encode("utf-8"), content_type="text/markdown; charset=utf-8")

    docx_url = blob.generate_sas_url(docx_path, timedelta(hours=sas_expiry_hours))
    report_url = blob.generate_sas_url(md_path, timedelta(hours=sas_expiry_hours))

    # 7. 기존 status 업데이트 로직은 그대로
    await tables.jobs.update_status(job_id, JobStatus.DONE,
                                    report_blob_path=md_path, finished_at=...)
    # ...

    return ReportResponse(
        job_id=job_id, risk_level=..., risk_score=...,
        report_url=report_url, report_blob_path=md_path,
        docx_url=docx_url, docx_blob_path=docx_path,
    )
```

#### `src/agents/report/prompts.py` (정리)

기존 거대 single-shot SYSTEM_PROMPT 폐기. planner 전용 짧은 prompt 만 남기거나, planner.py 안으로 이동.

#### `src/agents/report/templates.py` (수정)

`load_report_samples` 는 **삭제하지 말고 유지**. 톤 참고용으로 PR4 단계에서 활용. 다만 service 에서 직접 호출하지 않도록 의존성 정리.

### 6.3 테스트

- `tests/agents/report/test_template_spec.py`
  - yaml 로드 + 모든 섹션 ID 가 ReportSection enum 에 존재하는지
- `tests/agents/report/test_planner.py`
  - mock anthropic → plan JSON 검증
  - 환각 슬롯 입력 시 data_gap=true 로 변환되는지
- `tests/agents/report/test_renderer.py`
  - SectionContent fixture → docx 바이트 생성 → python-docx 로 다시 열어 표/헤딩/이미지 검증
  - 이미지 미제출 케이스 → placeholder 텍스트 확인
- `tests/agents/report/test_service.py`
  - end-to-end mock 으로 raw+result fixture → docx_url + report_url 둘 다 발급되는지

### 6.4 의존성

```toml
python-docx = "^1.1"   # 이미 있을 것 (templates.py 가 이미 사용)
PyYAML = "^6.0"        # template yaml 로드
```

### 6.5 Acceptance Criteria

- [ ] `report.docx` 가 `jobs/{job_id}/report/report.docx` 에 업로드됨
- [ ] `ReportResponse.docx_url`, `docx_blob_path` 필드 추가
- [ ] LLM 호출 횟수: 기존 1회 → planner 1회 (+ PR4에서 vision 옵션) — 총 2회 이내
- [ ] 모든 표는 raw.extracted_tables 또는 result.section_insights 의 cited_data_points 에서 옴 (plan 환각 검증)
- [ ] CLAUDE.md 의 report 응답 예시(`{ ..., "report_url": "...", "docx_url": "..." }`) 와 일치

---

## 7. PR4 — Report: 폴리싱 (선택, 후속)

PR1~3 가 안정화된 후 진행.

- **예시회사 예시 docx 를 base 스타일로 상속**
  - `templates/base_loan_application.docx` 를 Blob 에 보관
  - `Document(base_path)` 로 열어 본문만 비우고 헤더/푸터/폰트/표 보더 색상 상속
- **Claude Vision 으로 이미지 caption 추가**
  - `extract_image` 에 옵션으로 vision 호출 추가
  - `extracted_images[].caption` 채움 → docx 에 그림 캡션으로 표시
- **data_gap 에 따른 섹션 부분 생략 정책 정교화**
  - `ImageSlot.optional=False` 인데 미제출 → 섹션 통째로 "[자료 미확보 — 추가 제출 필요]" 처리
- **Check List / 별첨 자동 채움**
  - 차입금 명세표 별첨은 extracted_tables 에서 35행 이상이면 자동으로 별첨 섹션으로 분리

---

## 8. 데이터 스키마 최종 형태

### `raw.json` (Collector 출력)

```jsonc
{
  "company_name": "...",
  "company_id": "...",
  "collected_at": "ISO-8601",
  "news": [...],                  // 기존
  "lawsuits": [],                 // 기존
  "uploaded_files": [...],        // 기존
  "financial_years": [2021, 2022, 2023, 2024, 2025],  // 자동 추론

  // 신규
  "extracted_tables": [
    {
      "source_file": "excompany_financials_2024.xlsx",
      "sheet_name": "재무제표",
      "columns": ["구분", "2021", "2022", "2023", "2024", "2025.2Q"],
      "rows": [["자산총계", 5395, 5985, 5651, 6040, 6436], ...],
      "truncated": false
    }
  ],
  "extracted_images": [
    {
      "source_file": "ownership_chart.png",
      "blob_path": "jobs/{job_id}/input/ownership_chart.png",
      "suspected_role": "ownership_chart",
      "width": 480, "height": 273
    }
  ],
  "extracted_docs": [...]
}
```

### `result.json` (Financial 출력)

```jsonc
{
  "job_id": "...", "company_name": "...", "company_id": "...",
  "analyzed_at": "ISO-8601", "model": "claude-...",

  // 기존 글로벌 위험
  "risk_level": "MEDIUM", "risk_score": 42.5,
  "summary": "...",
  "key_risk_factors": [...],
  "positive_signals": [...],
  "data_gaps": [...],

  // 신규 섹션별 분석
  "section_insights": [
    {
      "section_id": "5_finance",
      "table_id": "재무제표",
      "bullets": [
        "‘24년 자산 6,040억원으로 전년 대비 389억원 증가하였으며...",
        "특히 ‘23년에는 금리 상승에도 불구하고 영업수익 589억원..."
      ],
      "cited_data_points": ["자산총계 6,040억", "당기순이익 106억"]
    },
    ...
  ],
  "input_summary": {
    "news_count": 30, "lawsuit_count": 0,
    "uploaded_file_count": 3, "financial_years": [2021, ..., 2025],
    "extracted_table_count": 8, "extracted_image_count": 2  // 신규
  }
}
```

### `report.docx` + `report.md` (Report 출력, 신규 docx)

Blob: `jobs/{job_id}/report/report.docx`, `jobs/{job_id}/report/report.md` 양쪽 모두 생성.

---

## 9. 추가 의존성 정리

`pyproject.toml` 에 한꺼번에 추가:

```toml
[project.dependencies]
# ... 기존 ...
openpyxl = "^3.1"        # PR1
pdfplumber = "^0.11"     # PR1
Pillow = "^10.4"         # PR1
PyYAML = "^6.0"          # PR3
# python-docx 는 기존 templates.py 가 이미 사용 중이면 그대로
```

`uv lock` 후 PR 별로 분리 커밋.

---

## 10. 변경하지 말 것 (Non-Goals)

다음은 **이번 업그레이드에서 건드리지 않는다**. 호환성 유지.

- `src/storage/blob_store.py` 인터페이스
- `src/storage/table_store.py` 의 모든 sub-store 인터페이스
- `src/agents/monitoring/` 전체 (run_service, scheduler, alerter, gmail_client, schemas)
  - 단, monitoring 테스트가 collector/financial 변경 후에도 통과해야 함 (회귀 검증만)
- `src/main.py`, `src/mcp/server.py`, `src/mcp/tool_registry.py`
- `create_analysis_job` Tool (업로드 파이프라인 그대로)
- `MonitoringSnapshot`, `AlertHistory` 등 Table 스키마
- Blob 경로 규칙 (`jobs/{job_id}/...` 그대로)
- Tool 이름 (collect_company_data, analyze_financials, report_generate, monitor_*)

---

## 11. 작업 시작 전 체크리스트

각 PR 시작 전 다음을 확인한다 (CLAUDE.md 의 체크리스트 + 본 업그레이드 추가).

- [ ] `develop` 브랜치 최신화
- [ ] PR 의존 순서 준수 (PR1 머지 후 PR2 시작, PR2 머지 후 PR3 시작)
- [ ] 신규 환경변수 추가 시 `.env.example` 동시 업데이트
- [ ] 예시회사 예시 문서(`ref/samples/excompany_capital_loan_app_v6.docx` 등 적절한 경로에 보관)와 결과물을 매번 비교
- [ ] 각 PR 의 Acceptance Criteria 모두 충족 후 PR 생성
- [ ] monitoring 회귀 테스트 통과 확인

---

## 12. PR 별 브랜치/커밋 메시지 예시

```
feature/upgrade-collector-extractors
  - feat: add xlsx/pdf/image extractors module
  - feat: extend raw.json with extracted_tables/images/docs
  - test: collector extractors unit tests
  - chore: add openpyxl/pdfplumber/Pillow deps

feature/upgrade-financial-section-insights
  - feat: add ReportSection enum and SectionInsight schema
  - feat: extend financial prompt with extracted_tables
  - feat: financial output now includes section_insights
  - test: section_insights schema + prompt rendering tests

feature/upgrade-report-pipeline
  - feat: add report template_spec module + loan_application_v1.yaml
  - feat: implement planner LLM stage with strict schema validation
  - feat: implement section content builder (no LLM)
  - feat: implement python-docx renderer
  - feat: report_generate now produces both .md and .docx
  - refactor: deprecate legacy single-shot prompts.py
  - test: end-to-end report generation tests
  - chore: add PyYAML dep

feature/upgrade-report-polish (선택)
  - feat: inherit base docx style from sample
  - feat: optional Claude Vision image captioning
  - feat: data_gap policy refinement
```

---

## 13. 참고 자료

- 예시회사 예시 문서 (분석 완료): 8섹션 + Check List + 별첨, 표 31개, 이미지 7개
- 기존 `templates.py` 의 `extract_docx_text` 는 톤 참고용으로 PR4 에서 재활용 가능
- CLAUDE.md 의 Tool 응답 예시에 이미 `docx_url` 명시되어 있음 → 본 업그레이드는 원래 스펙 완성에 해당
