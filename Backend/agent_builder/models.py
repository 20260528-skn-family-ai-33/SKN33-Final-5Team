from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field

class Requirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(max_length=1000)
    source_id: str = Field(description='user 또는 연결된 자료 ID')
    quote: str = Field(max_length=2000, description='사용자 대화 또는 자료에서 가져온 정확한 원문')

class FilterCondition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str
    operator: Literal['eq','ne','lt','lte','gt','gte','contains','is_empty','not_empty']
    value: str | float | bool | None
    compare_field: str | None
    value_type: Literal['number','text','boolean']

class SortCondition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str
    direction: Literal['ascending','descending']
    value_type: Literal['number','text']

class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filters: list[FilterCondition] = Field(max_length=20)
    sort: list[SortCondition] = Field(max_length=5)
    match: Literal['all','any']
    missing_policy: Literal['ask','exclude','keep','error']
    duplicate_policy: Literal['ask','keep','first','last','error']
    duplicate_field: str | None
    output_format: Literal['csv','json']

class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: str
    node_id: str
    output: str

class NodeSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_ref: str | None = Field(default=None,max_length=60)
    execution_plan: ExecutionPlan | None = None
    agent_id: str | None = None
    agent_version: int | None = Field(default=None,ge=1)
    output_node: str | None = None
    output_port: Literal['table','documents','records','text','artifact','reviews'] | None = None
    aggregation: Literal["count","sum"] | None = None
    sum_field: str | None = None
    file_ids: list[str]
    query: str | None
    fields: list[str]
    instructions: str | None
    format: Literal['csv','json','markdown'] | None
    question: str | None
    condition_field: str | None
    condition_operator: Literal['has_missing','empty','not_empty','semantic','all_pass','any_revision'] | None = Field(description='has_missing: records 중 value=null 항목이 있는가. empty/not_empty: 입력 전체가 비었는가/내용이 있는가(누락 항목 검사 아님). semantic: instructions의 업무 판단 기준을 사용자 응답 의미로 평가.')

class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    tool: Literal['read_table','filter_sort','write_result','read_document','search_document','extract_information','compose_answer','export_result','condition','ask_user','finish','aggregate_table','reply_review','review_summary','saved_workflow','agent_node']
    label: str
    bindings: list[Binding]
    settings: NodeSettings

class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str
    target: str
    branch: Literal['next','yes','no']

class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entry: str
    nodes: list[WorkflowNode] = Field(max_length=15)
    edges: list[WorkflowEdge] = Field(max_length=25)

class ScheduleSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    frequency: Literal['daily','weekly']
    timezone: str | None
    hour: int | None = Field(ge=0,le=23)
    minute: int | None = Field(ge=0,le=59)
    weekday: int | None = Field(ge=0,le=6,description='월요일=0, 일요일=6. daily이면 null.')

class AgentDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=60)
    purpose: str = Field(max_length=2000)
    instructions: str = Field(max_length=12000)
    tool_ids: list[Literal['read_table','filter_sort','write_result','read_document','search_document','extract_information','compose_answer','export_result','condition','ask_user','finish','aggregate_table','reply_review','review_summary','saved_workflow','agent_node']] = Field(max_length=10)
    document_ids: list[str] = Field(max_length=10)
    requirements: list[Requirement] = Field(max_length=30)
    questions: list[str] = Field(max_length=3)
    question_options: list[list[str]] = Field(default_factory=list,max_length=3,description='questions와 같은 순서. 선택 가능한 질문에는 짧은 선택지 2~4개, 자유 답변 질문은 빈 목록.')
    unsupported: list[str] = Field(max_length=10)
    explanation: str = Field(max_length=2000)
    execution_plan: ExecutionPlan
    workflow: WorkflowDefinition | None = None
    schedule: ScheduleSpec | None = None
    report_destination: Literal['reports'] | None = None

class BuilderState(TypedDict, total=False):
    session_id: str
    messages: list[dict]
    resources: list[dict]
    draft: dict | None
    status: str
    route: str
    issues: list[str]
    semantic_issues: list[str]
    trace: list[dict]
    repair_count: int
    confirmed: bool
    saved_agent_id: str | None
    base_agent_id: str | None
    base_version: int
    model: str
    credential_id: str
    saved_workflows: list[dict]
    node_library: list[dict]
    required_node_refs: list[str]
    verification: dict
    verification_route: str | None
    verification_enforced: bool
    verification_required: bool
    verification_last_candidate: dict | None
    verification_previous_draft: dict | None
    verification_user_fingerprint: str
    condition_ledger: dict | None
    condition_enforced: bool
    condition_required: bool
    condition_route: str
    condition_template: bool
    pending_questions: list[dict]
    condition_answers: list[dict]
    source_health: dict
    tool_gap: dict | None
    capability_route: str
    capability_assessment: dict | None
    capability_enforced: bool
    capability_fingerprint: str | None
    capability_questions: list[dict]
    capability_answers: list[dict]
    capability_deferred: bool
    capability_generation_route: str
