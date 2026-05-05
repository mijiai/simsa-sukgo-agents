"""섹션별 컨텐츠 조립 — LLM 호출 없음. plan + financial.section_insights 를 결합한다.

매칭 규칙 (Option B 핵심):
- plan 의 표 슬롯 (PlannedTable) → 표 헤더/행 데이터
- financial.section_insights → 표 아래 bullet 평가 (financial 이 작성한 것 그대로)
- 두 가지를 (section_id, table_id) 키로 매칭

table_id=None 인 insight 는 섹션 마지막의 "일반 bullet" 으로 분리해 모은다.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.agents.financial.schemas import SectionInsight
from src.agents.report.planner import PlannedImage, PlannedSection, PlannedTable
from src.agents.report.template_spec import SectionSpec
from src.common.constants import ReportSection


class TableContent(BaseModel):
    """렌더용 표 1개 — spec + plan + insights bullet 까지 모두 합친 결과."""

    table_id: str
    title: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    source_label: str = ""
    bullets: list[str] = Field(default_factory=list)
    cited_data_points: list[str] = Field(default_factory=list)
    data_gap: bool = False


class ImageContent(BaseModel):
    image_id: str
    title: str
    blob_path: str | None = None
    caption: str | None = None
    suspected_role: str = "unknown"
    optional: bool = True
    data_gap: bool = False


class SectionContent(BaseModel):
    section_id: ReportSection
    number: str
    title: str
    # 섹션 헤딩 바로 아래에 들어갈 서술형 paragraph(s).
    # narrative writer LLM 이 작성 — 표/bullet 의 근거 데이터를 글로 풀어냄.
    # 빈 문자열이면 narrative 출력 생략 (renderer 가 무시).
    narrative: str = ""
    tables: list[TableContent] = Field(default_factory=list)
    images: list[ImageContent] = Field(default_factory=list)
    # table_id=None 인 insights — 섹션 일반 bullet
    section_bullets: list[str] = Field(default_factory=list)


def _select_table_insights(
    section_insights: list[SectionInsight],
    section_id: ReportSection,
    table_id: str,
) -> SectionInsight | None:
    for si in section_insights:
        if si.section_id == section_id and si.table_id == table_id:
            return si
    return None


def _select_section_insights_without_table(
    section_insights: list[SectionInsight],
    section_id: ReportSection,
) -> list[SectionInsight]:
    return [si for si in section_insights if si.section_id == section_id and si.table_id is None]


def _build_table_content(
    spec_table_id: str,
    spec_title: str,
    spec_source_label: str,
    plan_table: PlannedTable,
    insight: SectionInsight | None,
) -> TableContent:
    return TableContent(
        table_id=spec_table_id,
        title=spec_title,
        columns=plan_table.columns,
        rows=plan_table.rows,
        source_label=plan_table.source_label_override or spec_source_label,
        bullets=insight.bullets if insight else [],
        cited_data_points=insight.cited_data_points if insight else [],
        data_gap=plan_table.data_gap,
    )


def _build_image_content(
    spec_image_id: str,
    spec_title: str,
    spec_role: str,
    spec_optional: bool,
    plan_image: PlannedImage,
) -> ImageContent:
    return ImageContent(
        image_id=spec_image_id,
        title=spec_title,
        blob_path=plan_image.blob_path,
        caption=plan_image.caption,
        suspected_role=spec_role,
        optional=spec_optional,
        data_gap=plan_image.data_gap,
    )


def build_section_content(
    spec: SectionSpec,
    plan_section: PlannedSection,
    section_insights: list[SectionInsight],
    narrative: str = "",
) -> SectionContent:
    """1개 섹션의 컨텐츠를 조립.

    table_id 가 매칭되는 insight 가 있으면 그 bullet 을 표 아래에 배치.
    table_id=None 인 insight 는 section_bullets 로 분리.
    매칭되는 insight 없는 표는 bullets=[] (표만 표시).
    narrative — narrative writer LLM 이 작성한 서술형 paragraph(s); 빈 문자열이면 생략.
    """
    tables: list[TableContent] = []
    for table_spec in spec.tables:
        plan_table = plan_section.tables.get(table_spec.table_id, PlannedTable(data_gap=True))
        insight = _select_table_insights(section_insights, spec.section_id, table_spec.table_id)
        tables.append(
            _build_table_content(
                spec_table_id=table_spec.table_id,
                spec_title=table_spec.title,
                spec_source_label=table_spec.source_label,
                plan_table=plan_table,
                insight=insight,
            )
        )

    images: list[ImageContent] = []
    for image_spec in spec.images:
        plan_image = plan_section.images.get(image_spec.image_id, PlannedImage(data_gap=True))
        images.append(
            _build_image_content(
                spec_image_id=image_spec.image_id,
                spec_title=image_spec.title,
                spec_role=image_spec.suspected_role,
                spec_optional=image_spec.optional,
                plan_image=plan_image,
            )
        )

    # table_id 없는 insights → section_bullets 로 모음
    untabled = _select_section_insights_without_table(section_insights, spec.section_id)
    section_bullets: list[str] = []
    for si in untabled:
        section_bullets.extend(si.bullets)

    return SectionContent(
        section_id=spec.section_id,
        number=spec.number,
        title=spec.title,
        narrative=narrative,
        tables=tables,
        images=images,
        section_bullets=section_bullets,
    )
