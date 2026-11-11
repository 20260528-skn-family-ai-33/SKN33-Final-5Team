"""Request-only requirements; these are never written by the workflow generator."""
from typing import Annotated, Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator
from contracts.models import FilterCondition, SortCondition, ScheduleSpec, QuestionResponse


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


class ScalarCalculate(Strict):
    kind: Literal['scalar_calculate']
    formula: str = Field(min_length=1, max_length=500)
    variables: dict[str, int | float] = Field(max_length=20)


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
    query: str | None = Field(default=None, max_length=2000)


class GmailScheduledSummary(GmailSummary):
    kind: Literal['gmail_scheduled_summary']
    schedule: ScheduleSpec
    query: Literal['{{gmail.previous_day}}', 'is:unread -in:sent -in:drafts']


class ScheduledExecution(Strict):
    kind: Literal['schedule']
    schedule: ScheduleSpec


Spec = Annotated[Read | ExtractFields | Compute | ScalarCalculate | ParseDates | Filter | Sort | Aggregate | Join | Export | ReceiptWorkbook | Policy | Other | GmailSummary | DocumentAnalysis | HumanReview | Rename | FillMissing | SelectFields | GmailScheduledSummary | ScheduledExecution,
                 Field(discriminator='kind')]


class RequiredTask(Strict):
    id: str = Field(pattern=r'^r[1-9][0-9]?$', max_length=3)
    source_message: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=20000)
    description: str = Field(min_length=1, max_length=300)
    depends_on: list[str] = Field(max_length=15)
    spec: Spec


class RequirementQuestion(Strict):
    """A customer decision, independent of a particular workflow or service."""
    key: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1000,
        description='고객에게 표시할 완전한 질문. 내부 필드명이나 구현 상세는 표시하지 않습니다.')
    group: str | None = Field(default=None, max_length=120,
        description='어떤 요청사항에 대한 질문인지 보여줄 짧은 제목. 예: 실행 일정, 결과 형식.')
    reason: str | None = Field(default=None, max_length=500,
        description='이 답이 없으면 어떤 업무 설정을 결정할 수 없는지 고객에게 설명합니다.')
    options: list[str] = Field(default_factory=list, max_length=12,
        description='고객이 선택할 실제 지원 가능한 항목. 선택형 이외에는 빈 목록.')
    response: QuestionResponse = Field(default_factory=lambda: QuestionResponse(type='text'))
    required: bool = Field(default=True,
        description='정해지지 않으면 안전하게 설계할 수 없는 조건만 true. 선택적인 선호는 false.')

    @model_validator(mode='before')
    @classmethod
    def legacy_response(cls,value):
        if isinstance(value,dict):
            from .question_time import clock_response
            response=value.get('response',{'type':'single_choice' if value.get('options') else 'text'})
            if isinstance(response,dict):value={**value,'response':clock_response(value,response)}
        return value

    @model_validator(mode='after')
    def coherent_control(self):
        if not self.key.strip() or not self.text.strip():
            raise ValueError('질문의 키와 고객 안내는 공백일 수 없습니다.')
        if self.reason is not None and not self.reason.strip():
            raise ValueError('질문이 필요한 이유는 공백일 수 없습니다.')
        if re.search(r'(?:더|좀|조금)\s*구체적(?:으로|인)',self.text) or re.fullmatch(
                r'(?:업무|요청|작업|처리할\s*내용|필요한\s*기준|추가\s*정보)(?:을|를)?\s*(?:설명|입력|작성|알려)(?:해|해\s*주|주)?(?:세요|주세요|주시겠어요)[.!?]?',
                self.text.strip()):
            raise ValueError('고객에게 실제로 빠진 조건 하나를 구체적으로 질문해야 합니다.')
        credential=r'(?:access[_. -]*token|refresh[_. -]*token|client[_. -]*secret|api[_. -]*key)'
        if re.search(credential,self.key,re.I) or re.search(credential,self.text,re.I):
            raise ValueError('인증 정보는 서비스 연결에서 관리하며 고객 질문으로 요청할 수 없습니다.')
        if any(not option.strip() for option in self.options) or len(set(self.options))!=len(self.options):
            raise ValueError('질문의 선택지는 비어 있거나 중복될 수 없습니다.')
        choice=self.response.type in ('single_choice','multi_choice')
        if choice and len(self.options)<2:
            raise ValueError('선택형 질문에는 두 개 이상의 실제 선택지가 필요합니다.')
        if not choice and self.options:
            raise ValueError('선택지는 단일·다중 선택형 질문에서만 사용할 수 있습니다.')
        return self


class RequirementPlan(Strict):
    tasks: list[RequiredTask] = Field(max_length=30)
    # Strings and partial dictionaries are accepted for persisted legacy ledgers.
    # The active extraction schema is narrowed to complete RequirementQuestion objects.
    unresolved: list[RequirementQuestion | str] = Field(max_length=10)

    @model_validator(mode='after')
    def task_or_question(self):
        if not self.tasks and not any((q if isinstance(q,str) else q.text).strip() for q in self.unresolved):
            raise ValueError('요청한 업무나 확인할 조건이 하나 이상 필요합니다.')
        keys=[question.key for question in self.unresolved if isinstance(question,RequirementQuestion)]
        if len(set(keys))!=len(keys):
            raise ValueError('같은 조건을 묻는 질문 키가 중복됩니다.')
        return self


def question_capability_context(state):
    """Use the installed catalogue, rather than invented delivery/format choices."""
    from workflow_engine.catalog.registry import TOOLS
    from workflow_engine.catalog.canonical import planner_catalog
    from agent_builder.capabilities.capability_judgement import APP_CAPABILITIES
    selected=set((state.get('capability_assessment') or {}).get('tool_ids',[]))
    return {'registered_tools':planner_catalog(TOOLS,discovery=True),
            'selected_tool_contracts':planner_catalog([tool for tool in TOOLS if tool['id'] in selected],
                                                       details=True,native_first=True),
            'native_app_capabilities':APP_CAPABILITIES}


def validate_unanswered_questions(plan,state):
    """A resolved control cannot be repackaged as another missing requirement."""
    from .question_language import validate_question_language
    validate_question_language(plan.get('unresolved',[]),state)
    from .requirement_clarification import schedule_facts
    confirmed={key for key,value in schedule_facts(state).items() if value!='conflict'}
    if {'schedule.hour','schedule.minute'}<=confirmed:confirmed.add('schedule.time')
    for event in state.get('requirement_answers',[]):
        if event.get('skipped') or 'value' not in event or event['value'] is None:continue
        turn=event.get('turn')
        messages=state.get('messages',[])
        if (type(turn) is not int or not 0<=turn<len(messages) or messages[turn].get('role')!='user'
                or messages[turn].get('content')!=event.get('message_content',event.get('answer'))):continue
        for question in event.get('questions',[]):
            response=(question.get('response') or {}).get('type')
            value=event['value'];options=question.get('options',[])
            valid=(response=='single_choice' and isinstance(value,str) and value in options
                   or response=='multi_choice' and isinstance(value,list) and value and all(item in options for item in value)
                   or response=='number' and isinstance(value,(int,float)) and not isinstance(value,bool)
                   or response=='time' and isinstance(value,str) and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value)))
            from .question_time import clock_question,time_parts
            if clock_question(question):
                try:time_parts(value)
                except ValueError:pass
                else:valid=True
            if valid:confirmed.add(question['key'])
    if any(isinstance(question,dict) and question['key'] in confirmed for question in plan['unresolved']):
        raise ValueError('이미 확인한 조건을 다시 미정으로 질문할 수 없습니다. 기존 답변을 반영하세요.')
