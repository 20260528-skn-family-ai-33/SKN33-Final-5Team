"""공통 계약. DB 제품·파서 제품에 종속되지 않는 버전 고정 DTO."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = 'a4b.verifier.v1'
class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Message(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal['user','assistant']
    content: str = Field(min_length=1, max_length=20000)

class Chunk(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=30000)
    locator: str = Field(min_length=1, max_length=200)

class Table(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    columns: list[str] = Field(min_length=1, max_length=200)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    @model_validator(mode='after')
    def unique_columns(self):
        if len(set(self.columns))!=len(self.columns) or any(not c for c in self.columns):
            raise ValueError('표 열 이름은 비어 있지 않고 유일해야 합니다.')
        if any(set(r)-set(self.columns) for r in self.sample_rows):
            raise ValueError('표 샘플에 미등록 열이 있습니다.')
        return self

class Document(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    parse_status: Literal['ready','partial','failed']
    chunks: list[Chunk] = Field(default_factory=list, max_length=50)
    tables: list[Table] = Field(default_factory=list, max_length=10)
    @model_validator(mode='after')
    def unique_parts(self):
        for values in [self.chunks,self.tables]:
            if len({v.id for v in values})!=len(values):raise ValueError('문서 내 ID가 중복됩니다.')
        if self.parse_status=='failed' and (self.chunks or self.tables):
            raise ValueError('failed 문서의 추출 내용은 제외하고 partial로 명시하세요.')
        return self

class ToolSpec(StrictModel):
    id: str
    description: str
    enabled: bool
    input_ports: dict[str,str]
    output_ports: dict[str,str]
    required_input_ports: list[str]
    required_settings: list[str]
    check_scope: Literal['basic','table','dynamic']

class Binding(StrictModel):
    input: str
    node_id: str
    output: str
class Node(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=100)
    bindings: list[Binding] = Field(default_factory=list, max_length=30)
    settings: dict[str,Any] = Field(default_factory=dict)
class Edge(StrictModel):
    source: str
    target: str
    branch: Literal['next','yes','no'] = 'next'
class Workflow(StrictModel):
    entry: str
    nodes: list[Node] = Field(min_length=1, max_length=15)
    edges: list[Edge] = Field(default_factory=list, max_length=25)
class Draft(StrictModel):
    id: str
    version: str
    document_ids: list[str] = Field(default_factory=list)
    workflow: Workflow

class VerificationInput(StrictModel):
    contract_version: Literal['a4b.verifier.v1']
    request_id: str = Field(min_length=1, max_length=100)
    user_request: str = Field(min_length=1, max_length=20000)
    messages: list[Message] = Field(default_factory=list, max_length=50)
    latest_user_answer_id: str | None = None
    documents: list[Document] = Field(default_factory=list, max_length=10)
    tools: list[ToolSpec] = Field(min_length=1, max_length=100)
    previous_draft: Draft | None = None
    candidate_draft: Draft | None = None
    @model_validator(mode='after')
    def references(self):
        for key in ['messages','documents','tools']:
            items=getattr(self,key)
            if len({x.id for x in items})!=len(items):raise ValueError(key+'의 ID가 중복됩니다.')
        if self.latest_user_answer_id is not None:
            if not any(m.id==self.latest_user_answer_id and m.role=='user' for m in self.messages):
                raise ValueError('최신 답변 ID는 user 메시지를 참조해야 합니다.')
        return self

class EvidenceRef(StrictModel):
    evidence_id: str
    quote: str = Field(min_length=1, max_length=1000)
class MissingCondition(StrictModel):
    code: Literal['field','selection_rule','operator','value','missing_policy','document_priority','resource','other']
    detail: str = Field(min_length=1,max_length=400)
    evidence: list[EvidenceRef] = Field(max_length=5)
class Issue(StrictModel):
    code: Literal['request_missing','unsupported','draft_mismatch','reflection_mismatch','evidence_insufficient']
    detail: str = Field(min_length=1,max_length=500)
    path: str | None
    expected: str | None
    actual: str | None
    evidence: list[EvidenceRef] = Field(max_length=5)

Verdict=Literal['pass','fail','undetermined','not_applicable']
class VerificationOutput(StrictModel):
    request_status: Literal['ready','clarify','unsupported'] | None
    evidence_status: Literal['sufficient','insufficient']
    missing_conditions: list[MissingCondition] = Field(max_length=8)
    unsupported_actions: list[str] = Field(max_length=8)
    draft_verdict: Verdict
    reflection_verdict: Verdict
    evidence: list[EvidenceRef] = Field(max_length=8)
    issues: list[Issue] = Field(max_length=10)
    summary: str = Field(min_length=1,max_length=500)

class CodeFinding(StrictModel):
    code: str
    severity: Literal['block','review']
    detail: str
    path: str | None = None
class VerificationResult(StrictModel):
    contract_version: Literal['a4b.verifier.v1'] = VERSION
    request_id: str
    model_output: VerificationOutput | None
    findings: list[CodeFinding]
    route: Literal['ask_user','repair_plan','unsupported','manual_review','continue_review']
    execution_allowed: Literal[False] = False
    mode: Literal['fixture','live']
    model: str | None
    latency_ms: float
    error_code: str | None = None
