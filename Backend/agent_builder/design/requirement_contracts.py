"""Request-only requirements; these are never written by the workflow generator."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from contracts.models import FilterCondition, SortCondition, ScheduleSpec


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Read(Strict):
    kind: Literal['read']
    file_ids: list[str] = Field(max_length=10)


class ExtractFields(Strict):
    kind: Literal['extract_fields', 'receipt_items']
    fields: list[str] = Field(min_length=1, max_length=40)


class Compute(Strict):
    kind: Literal['compute']
    formula: str = Field(min_length=1, max_length=500)
    output_column: str = Field(min_length=1, max_length=100)


class ParseDates(Strict):
    kind: Literal['parse_dates']
    fields: list[str] = Field(min_length=1, max_length=20)
    input_format: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)


class Filter(Strict):
    kind: Literal['filter']
    filters: list[FilterCondition] = Field(min_length=1, max_length=20)
    match: Literal['all', 'any']


class Sort(Strict):
    kind: Literal['sort']
    sort: list[SortCondition] = Field(min_length=1, max_length=5)


class Aggregate(Strict):
    kind: Literal['aggregate']
    fields: list[str] = Field(max_length=20)
    aggregation: Literal['count', 'sum']
    sum_field: str | None


class Join(Strict):
    kind: Literal['join']
    keys: list[str] = Field(min_length=1, max_length=10)
    how: Literal['inner', 'left']
    cardinality: Literal['one_to_one', 'many_to_one']
    null_keys: Literal['error']
    max_rows: int = Field(gt=0)
    left_sources: list[str] = Field(max_length=10)
    right_sources: list[str] = Field(max_length=10)


class Export(Strict):
    kind: Literal['export']
    format: Literal['csv', 'json', 'md', 'xlsx', 'pdf']


class ReceiptWorkbook(Strict):
    kind: Literal['receipt_workbook']
    sheets: list[Literal['영수증', '상품내역']] = Field(min_length=1, max_length=2)


class Policy(Strict):
    kind: Literal['policy']
    missing_policy: Literal['exclude', 'keep', 'error'] | None
    duplicate_policy: Literal['keep', 'first', 'last', 'error'] | None
    duplicate_field: str | None


class Other(Strict):
    kind: Literal['other']
    action: str = Field(min_length=1, max_length=300)


class DocumentAnalysis(Strict):
    kind: Literal['document_analysis']
    request: str = Field(min_length=1, max_length=20000)


class HumanReview(Strict):
    kind: Literal['human_review']


class Rename(Strict):
    kind: Literal['rename']
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)


class FillMissing(Strict):
    kind: Literal['fill_missing']
    fields: list[str] = Field(min_length=1, max_length=20)
    value: str | int | float | bool


class SelectFields(Strict):
    kind: Literal['select_fields']
    fields: list[str] = Field(min_length=1, max_length=40)


class GmailSummary(Strict):
    kind: Literal['gmail_summary']
    max_results: int = Field(ge=1, le=500)
    request: str = Field(min_length=1, max_length=2000)
    summary_instructions: str | None = Field(default=None, min_length=1, max_length=2000)


class GmailScheduledSummary(GmailSummary):
    kind: Literal['gmail_scheduled_summary']
    schedule: ScheduleSpec
    query: Literal['{{gmail.previous_day}}', 'is:unread -in:sent -in:drafts']


class ScheduledExecution(Strict):
    kind: Literal['schedule']
    schedule: ScheduleSpec


Spec = Annotated[Read | ExtractFields | Compute | ParseDates | Filter | Sort | Aggregate | Join | Export | ReceiptWorkbook | Policy | Other | GmailSummary | DocumentAnalysis | HumanReview | Rename | FillMissing | SelectFields | GmailScheduledSummary | ScheduledExecution,
                 Field(discriminator='kind')]


class RequiredTask(Strict):
    id: str = Field(pattern=r'^r[1-9][0-9]?$', max_length=3)
    source_message: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=20000)
    description: str = Field(min_length=1, max_length=300)
    depends_on: list[str] = Field(max_length=15)
    spec: Spec


class RequirementPlan(Strict):
    tasks: list[RequiredTask] = Field(min_length=1, max_length=30)
    unresolved: list[str] = Field(max_length=10)
