"""보고서 템플릿 정형 스펙 — 한 번 정의하면 모든 보고서가 같은 양식으로 나온다.

PR3 의 핵심 데이터 구조. yaml 파일에서 로드해 메모리 캐시.

흐름:
    yaml → ReportTemplate (Pydantic) → planner / sections / renderer 가 모두 참조
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from src.common.constants import ReportSection


class TableSpec(BaseModel):
    """섹션 안의 표 1개에 대한 정형 스펙.

    table_id: SectionInsight.table_id 와 매칭되는 키 (financial 의 bullet 매칭용)
    """

    table_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_label: str = Field(default="")
    data_source: str = Field(
        default="",
        description="planner LLM 에 전달되는 데이터 소스 힌트. "
        "예: 'extracted_tables.재무제표', 'analysis.section_insights.5_finance'",
    )
    commentary_required: bool = True


class ImageSlot(BaseModel):
    """섹션 안의 이미지 1개 슬롯."""

    image_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    suspected_role: str = Field(default="unknown")
    optional: bool = True


class SectionSpec(BaseModel):
    section_id: ReportSection
    number: str = Field(min_length=1, description="화면 표시용 번호 ('1', '2' 등)")
    title: str = Field(min_length=1)
    tables: list[TableSpec] = Field(default_factory=list)
    images: list[ImageSlot] = Field(default_factory=list)


class ReportTemplate(BaseModel):
    name: str
    sections: list[SectionSpec]


# yaml 파일들이 위치하는 디렉터리
_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=4)
def load_template(name: str = "loan_application_v1") -> ReportTemplate:
    """yaml 파일에서 템플릿 로드 + LRU 캐시.

    캐시 무효화는 파이썬 프로세스 재시작 또는 load_template.cache_clear() 호출.
    yaml 검증 실패 (스키마 위반) 시 ValidationError 가 그대로 raise — 부팅 단계에서 발견하도록.
    """
    yaml_path = _TEMPLATES_DIR / f"{name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"report template not found: {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return ReportTemplate.model_validate(data)
