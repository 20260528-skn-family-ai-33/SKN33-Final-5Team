"""Draft team boundary; opt-in, pure, with no model/DB/network side effects.

This is not a new HTTP endpoint or a replacement for worker revision checks.
The caller must obtain expected session/revision from its trusted store.
"""
from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .condition_readiness import PROTOCOL, QUESTIONS, REQUIRED, input_issues, resource_version
from .input_requirements import design_requirements
from .nodes.direct_conversation import business_condition_context
from .registry import TOOLS

CONTRACT = 'a4b.clarification.v1-draft'
DECISIONS = {'ready':'READY','clarify':'CLARIFY','unsupported':'UNSUPPORTED',
             'system_error':'SYSTEM_ERROR','deferred':'DEFERRED','manual_review':'MANUAL_REVIEW'}
ACTIONS = {'READY':'GENERATE_DRAFT','CLARIFY':'ASK_USER','UNSUPPORTED':'SHOW_UNSUPPORTED',
           'SYSTEM_ERROR':'CHECK_SYSTEM','DEFERRED':'WAIT_FOR_USER','MANUAL_REVIEW':'REVIEW_SCOPE'}


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)


class Message(BoundaryModel):
    role: Literal['user','assistant']
    content: str = Field(min_length=1,max_length=12000)

    @model_validator(mode='after')
    def user_limit(self):
        if self.role=='user' and (not self.content.strip() or len(self.content)>4000):
            raise ValueError('사용자 입력은 공백이 아닌 1~4000자여야 합니다.')
        return self


class SourceHealth(BoundaryModel):
    db_status: Literal['ok','timeout','failed','unavailable']
    messages_loaded: bool


class Resource(BaseModel):
    # Runtime assets also contain previews, sections, tables and original_path.
    model_config = ConfigDict(extra='allow',strict=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal['table','document']
    format: Literal['csv','xlsx','json','txt','md','pdf']
    columns: list[str] = Field(default_factory=list)
    text: str = ''
    pages: list[str] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)
    version: str | int | None = None
    hash: str | None = None
    parser_status: Literal['succeeded','partial','failed','processing'] | None = None
    processing_status: Literal['ready','completed','partial','failed','processing'] | None = None
    warnings: list[str] = Field(default_factory=list)
    parser_metadata: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def native_asset(self):
        extras=self.model_extra or {}
        if any(key in extras for key in ('canonical_text','blocks','contract_version')):
            raise ValueError('DB 파서 공유안은 현재 자산 형식과 다릅니다. 별도 변환·페이지 출처 검증이 필요합니다.')
        if (self.kind=='table') != (self.format in ('csv','xlsx','json')):
            raise ValueError('자산 종류와 파일 형식이 다릅니다.')
        if self.format=='pdf' and self.parser_status is None:
            raise ValueError('PDF에는 parser_status를 명시해야 합니다.')
        if len(set(self.columns))!=len(self.columns) or any(not c.strip() for c in self.columns):
            raise ValueError('열 이름은 공백이나 중복일 수 없습니다.')
        return self


class Candidate(BoundaryModel):
    tool_id: str
    estimated_probability: float = Field(ge=0,le=1,allow_inf_nan=False)


class MLAdvice(BoundaryModel):
    status: Literal['not_requested','available','fallback','failed']
    ml_candidates: list[Candidate] = Field(default_factory=list,max_length=16)
    model_revision: str | None = None

    @model_validator(mode='after')
    def candidates_are_hints(self):
        ids=[c.tool_id for c in self.ml_candidates]
        known={t['id'] for t in TOOLS}-{'finish','saved_workflow'}
        if len(set(ids))!=len(ids) or any(i not in known for i in ids):
            raise ValueError('SVM 후보는 실제 예측 도구의 중복 없는 ID여야 합니다.')
        if (self.status=='available') != bool(ids):
            raise ValueError('추천 상태와 후보 목록이 일치하지 않습니다.')
        return self


class Question(BoundaryModel):
    id: str = Field(pattern=r'^[a-f0-9]{24}$')
    key: str
    text: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def known_condition(self):
        if self.key not in QUESTIONS and self.key!='input.table':
            raise ValueError('현재 계약에 없는 질문 항목입니다.')
        return self


class PreviousState(BoundaryModel):
    session_id: str
    revision: int = Field(ge=0)
    ledger: dict
    pending_questions: list[Question] = Field(default_factory=list,max_length=3)
    answer_events: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def internal_version(self):
        if self.ledger.get('protocol')!=PROTOCOL:
            raise ValueError('이전 조건 장부 버전이 다릅니다. 명시적 버전 변환이 필요합니다.')
        ids=[q.id for q in self.pending_questions]
        if len(ids)!=len(set(ids)):
            raise ValueError('대기 질문 ID가 중복되었습니다.')
        return self


class ClarificationInput(BoundaryModel):
    contract_version: Literal['a4b.clarification.v1-draft']
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    messages: list[Message] = Field(min_length=1)
    resources: list[Resource]
    source_health: SourceHealth
    tool_catalog_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    ml_advice: MLAdvice
    previous_state: PreviousState | None = None
    answer_question_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{24}$')

    @model_validator(mode='after')
    def linked_context(self):
        if self.messages[-1].role!='user':
            raise ValueError('입력 대화의 마지막 항목은 이번 사용자 발언이어야 합니다.')
        ids=[r.id for r in self.resources]
        if len(ids)!=len(set(ids)):
            raise ValueError('자산 ID가 중복되었습니다.')
        previous=self.previous_state
        if previous and (previous.session_id!=self.session_id or previous.revision!=self.revision):
            raise ValueError('다른 세션 또는 오래된 버전의 이전 상태입니다.')
        if self.answer_question_id and (not previous or self.answer_question_id not in {q.id for q in previous.pending_questions}):
            raise ValueError('현재 대기 질문에 속하지 않는 답변입니다.')
        return self


def tool_catalog():
    return [deepcopy({key:t[key] for key in ('id','version','description','input_ports','output_ports')}) for t in TOOLS]


def catalog_fingerprint():
    body=json.dumps(sorted(tool_catalog(),key=lambda t:t['id']),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(body.encode()).hexdigest()


def native_ml_advice(advice):
    """Accept current build_advice output; no classifier inference occurs."""
    if advice is None:
        return MLAdvice(status='not_requested')
    if not isinstance(advice,dict):
        raise ValueError('추천 어댑터에는 현재 build_advice 객체 또는 None이 필요합니다.')
    if any(k in advice for k in ('candidates','probs','full_list','required_tool_ids')):
        raise ValueError('원시 SVM 출력·진단용 정답 도구 대신 운영 build_advice 결과를 전달하세요.')
    candidates=advice.get('ml_candidates',[])
    return MLAdvice(status='available' if candidates else 'fallback',ml_candidates=candidates)


def to_builder_state(payload, *, expected_session_id, expected_revision):
    """Validate a trusted caller's snapshot; caller still owns atomic CAS/dedupe."""
    request=ClarificationInput.model_validate(payload)
    if request.session_id!=expected_session_id or request.revision!=expected_revision:
        raise ValueError('연결 대상의 세션·버전과 요청이 일치하지 않습니다.')
    if request.tool_catalog_fingerprint!=catalog_fingerprint():
        raise ValueError('도구 카탈로그 버전이 다릅니다. 미지원 판정 대신 연결 규격을 먼저 맞추세요.')
    resources=[]
    for asset in request.resources:
        resource=asset.model_dump(exclude_none=True)
        if resource['parser_metadata'].get('whole_document_complete') is False:
            # Do not turn selected page 67 into synthetic page 1 evidence.
            resource['parser_metadata']['boundary_original_parser_status']=resource.get('parser_status')
            resource['processing_status']='partial'
        resources.append(resource)
    previous=request.previous_state
    events=deepcopy(previous.answer_events) if previous else []
    pending=[q.model_dump() for q in previous.pending_questions] if previous else []
    if pending:
        selected=[q for q in pending if not request.answer_question_id or q['id']==request.answer_question_id]
        events.append({'question_ids':[q['id'] for q in selected],'candidate_keys':[q['key'] for q in selected],
                       'answer':request.messages[-1].content,'turn':len(request.messages)-1})
    return {'session_id':request.session_id,'messages':[m.model_dump() for m in request.messages],
            'resources':resources,'source_health':request.source_health.model_dump(),
            'condition_ledger':deepcopy(previous.ledger) if previous else None,
            'pending_questions':pending,'condition_answers':events}


class ConfirmedCondition(BaseModel):
    model_config = ConfigDict(extra='allow')
    key: str
    status: Literal['specified']
    value: str | float | bool
    evidence_ids: list[str]
    evidence: list[dict] = Field(min_length=1)


class UnresolvedCondition(BaseModel):
    model_config = ConfigDict(extra='allow')
    key: str
    status: Literal['missing','conflict']
    value: None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)


class ClarificationOutput(BoundaryModel):
    contract_version: Literal['a4b.clarification.v1-draft']
    request_id: str
    session_id: str
    revision: int
    condition_protocol: Literal[PROTOCOL]
    decision: Literal['READY','CLARIFY','UNSUPPORTED','SYSTEM_ERROR','DEFERRED','MANUAL_REVIEW']
    next_action: Literal['GENERATE_DRAFT','ASK_USER','SHOW_UNSUPPORTED','CHECK_SYSTEM','WAIT_FOR_USER','REVIEW_SCOPE']
    reason: str = Field(min_length=1)
    confirmed_conditions: dict[str,ConfirmedCondition]
    unresolved_conditions: dict[str,UnresolvedCondition]
    unresolved_keys: list[str]
    questions: list[Question] = Field(max_length=3)
    issues: list[str]
    unsupported_actions: list[str]
    resource_versions: dict[str,str]
    input_requirements: list[dict]
    generator_context: dict | None
    requires_user_approval: Literal[True] = True
    allows_save: Literal[False] = False
    allows_execution: Literal[False] = False

    @model_validator(mode='after')
    def routing(self):
        if any(key not in QUESTIONS or key!=slot.key for key,slot in self.confirmed_conditions.items()):
            raise ValueError('현재 조건 계약에 없는 확정 조건입니다.')
        if set(self.unresolved_keys)!=set(self.unresolved_conditions) or set(self.unresolved_keys)&set(self.confirmed_conditions):
            raise ValueError('확정·미정 조건 목록이 일치하지 않습니다.')
        if any(key not in {*QUESTIONS,'input.table'} or key!=slot.key for key,slot in self.unresolved_conditions.items()):
            raise ValueError('현재 계약에 없는 미정 조건입니다.')
        if self.next_action!=ACTIONS[self.decision]:
            raise ValueError('판정과 다음 처리가 다릅니다.')
        if self.decision=='READY' and (self.unresolved_keys or self.questions or self.issues or self.unsupported_actions):
            raise ValueError('READY에는 미정 조건·질문·장애·미지원이 남을 수 없습니다.')
        if self.decision=='READY' and not self.confirmed_conditions:
            raise ValueError('READY에는 근거가 있는 확정 조건이 필요합니다.')
        if (self.decision=='READY') != (self.generator_context is not None):
            raise ValueError('생성 입력은 READY에서만 제공합니다.')
        if self.decision in ('CLARIFY','DEFERRED') and not self.questions:
            raise ValueError('추가 질문·보류에는 질문 기록이 필요합니다.')
        return self


def to_handoff(payload, state):
    """Export only condition readiness, never overall workflow success/approval."""
    request=ClarificationInput.model_validate(payload)
    original=to_builder_state(request,expected_session_id=request.session_id,expected_revision=request.revision)
    ledger=state.get('condition_ledger') or {}
    if not state.get('condition_enforced') or ledger.get('protocol')!=PROTOCOL:
        raise ValueError('활성 조건 검사 결과가 필요합니다. off/shadow는 승인 근거가 아닙니다.')
    if state.get('session_id')!=request.session_id:
        raise ValueError('다른 세션의 결과를 전달할 수 없습니다.')
    messages=original['messages']
    if state.get('messages',[])[:len(messages)]!=messages:
        raise ValueError('요청과 결과의 대화가 일치하지 않습니다.')
    if state.get('source_health')!=original['source_health']:
        raise ValueError('조회 상태가 요청 후 바뀌었습니다. 새 요청으로 다시 판정하세요.')
    if state.get('resources')!=original['resources']:
        raise ValueError('요청과 결과의 자료가 일치하지 않습니다.')
    decision=DECISIONS[ledger['decision']]
    if decision=='READY' and input_issues(state):
        raise ValueError('입력 장애가 남은 결과를 READY로 전달할 수 없습니다.')
    if decision=='READY':
        operations=ledger.get('operations',[])
        if len(operations)!=1 or operations[0] not in REQUIRED:
            raise ValueError('단일 대표 업무 범위의 확인된 판정이 필요합니다.')
        required=list(REQUIRED[operations[0]])
        if ledger.get('slots',{}).get('schedule.frequency',{}).get('value')=='weekly':
            required.append('schedule.weekday')
        if any(ledger.get('slots',{}).get(key,{}).get('status')!='specified' for key in required):
            raise ValueError('필수 조건이 누락된 판정을 READY로 전달할 수 없습니다.')
        if operations==['schedule'] and not any(r.get('kind')=='table' for r in state['resources']):
            raise ValueError('예약용 관리 표 없이 READY로 전달할 수 없습니다.')
    confirmed={key:deepcopy(slot) for key,slot in ledger.get('slots',{}).items() if slot['status']=='specified'}
    unresolved={key:deepcopy(ledger.get('slots',{}).get(key) or {'key':key,'status':'missing','value':None})
                for key in ledger.get('unresolved',[])}
    versions={r['id']:resource_version(r) for r in state['resources']}
    if decision=='READY' and ledger.get('resource_versions')!=versions:
        raise ValueError('판정 후 자료 버전이 변경됐습니다.')
    generator=None
    if decision=='READY':
        generator={'business_conditions':business_condition_context(ledger),
                   'resources':deepcopy(state['resources']),'tool_catalog':tool_catalog(),
                   'tool_catalog_fingerprint':request.tool_catalog_fingerprint,
                   'ml_advice':request.ml_advice.model_dump()}
    reason = ('필수 조건을 확인했습니다. 초안 생성으로 전달하며 저장·실행은 별도 검증과 사용자 확인이 필요합니다.'
              if decision=='READY' else
              ' / '.join(ledger.get('issues',[]) or ledger.get('unsupported_actions',[]) or
                         [q['text'] for q in ledger.get('questions',[])]) or '자동 확정 범위를 벗어나 검토가 필요합니다.')
    return ClarificationOutput(contract_version=CONTRACT,request_id=request.request_id,session_id=request.session_id,
        revision=request.revision,condition_protocol=ledger['protocol'],decision=decision,next_action=ACTIONS[decision],reason=reason,
        confirmed_conditions=confirmed,unresolved_conditions=unresolved,unresolved_keys=ledger.get('unresolved',[]),questions=ledger.get('questions',[]),
        issues=ledger.get('issues',[]),unsupported_actions=ledger.get('unsupported_actions',[]),resource_versions=versions,
        input_requirements=design_requirements(state),generator_context=generator)
