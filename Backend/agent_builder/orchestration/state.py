"""Single requirement state (stage 1: format, projection and invariants only).

Target flow: Qwen extracts candidate meaning -> this layer verifies evidence, merges state,
prevents conflicts, removes duplicates, checks required values and decides questions ->
READY -> generation -> existing validator -> approval -> save.

Stage 1 does not decide anything. ``project_state`` reads the ledgers that exist today
(requirement ledger, condition ledger, capability assessment, pending questions, file
timing, schedule removal) and expresses them in one format, so later stages can move
decisions here one domain at a time and compare old and new paths on the same data.

Invariants (``state_issues``):
- every *specified* or *removed* condition, task and file input cites a user message
  whose text contains the quote (evidence is the user's own words);
- one condition per (task, key); one input per subject; question IDs are unique;
- ``version`` is the digest of the canonical content, so an approval can be bound to it.
"""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT = 'a4b.requirement_state.v1'
ConditionStatus = Literal['specified', 'missing', 'conflict', 'removed', 'deferred']
Timing = Literal['now', 'execution', 'attached', 'unknown']
Origin = Literal['user', 'code', 'model', 'legacy']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Strict):
    turn: int = Field(ge=0)          # index in state['messages']
    quote: str = Field(min_length=1)


class Task(Strict):
    id: str
    kind: str                        # requirement spec kind, or 'request' when only a capability verdict exists
    description: str
    evidence: list[Evidence]
    depends_on: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)


class Condition(Strict):
    key: str
    task_id: str | None = None       # None: applies to the whole request (legacy table ledger)
    value: object = None
    status: ConditionStatus
    required: bool = True
    evidence: list[Evidence] = Field(default_factory=list)
    origin: Origin = 'legacy'


class FileInput(Strict):
    subject: str
    timing: Timing
    evidence: list[Evidence] = Field(default_factory=list)
    resource_id: str | None = None


class Question(Strict):
    id: str
    key: str
    source: str
    text: str
    required: bool = True
    status: Literal['open', 'answered'] = 'open'


class Edit(Strict):
    turn: int = Field(ge=0)
    kind: Literal['change', 'remove', 'cancel']
    target: str
    quote: str


class RequirementState(Strict):
    format: Literal['a4b.requirement_state.v1'] = FORMAT
    mode: Literal['shadow'] = 'shadow'   # stage 1: projection only, never authoritative
    tasks: list[Task] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    inputs: list[FileInput] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    edits: list[Edit] = Field(default_factory=list)
    capability: dict = Field(default_factory=dict)   # {'decision', 'tool_ids', 'missing_actions'}
    system_failure: dict | None = None
    version: str = ''


def _user_turns(state):
    return [i for i, m in enumerate(state.get('messages', []) or []) if m.get('role') == 'user']


def _evidence_at(state, turn, quote):
    """Evidence only when the quote is really in that user message."""
    messages = state.get('messages', []) or []
    if not quote or not (0 <= turn < len(messages)) or messages[turn].get('role') != 'user':
        return []
    return [Evidence(turn=turn, quote=quote)] if quote in messages[turn].get('content', '') else []


def _find_quote(state, quote):
    """The latest user turn containing the quote (requirement tasks store the quote, not the turn)."""
    for turn in reversed(_user_turns(state)):
        if quote and quote in state['messages'][turn].get('content', ''):
            return [Evidence(turn=turn, quote=quote)]
    return []


def _tasks(state):
    ledger = state.get('requirement_ledger') or {}
    users = _user_turns(state)
    tasks = []
    for item in ledger.get('tasks', []) or []:
        source = item.get('source_message')
        turn = users[source] if isinstance(source, int) and 0 <= source < len(users) else None
        evidence = _evidence_at(state, turn, item.get('quote', '')) if turn is not None else _find_quote(state, item.get('quote', ''))
        tasks.append(Task(id=item['id'], kind=(item.get('spec') or {}).get('kind', 'other'),
                          description=item.get('description', ''), evidence=evidence,
                          depends_on=list(item.get('depends_on', []))))
    if not tasks:
        assessment = state.get('capability_assessment') or {}
        quotes = assessment.get('user_quotes') or []
        if assessment.get('decision') and quotes:
            tasks.append(Task(id='request', kind='request', description='사용자 요청',
                              evidence=_find_quote(state, quotes[0]), tool_ids=list(assessment.get('tool_ids', []))))
    return tasks


def _conditions(state):
    result = []
    ledger = state.get('condition_ledger') or {}
    for key, slot in (ledger.get('slots') or {}).items():
        status = slot.get('status') if slot.get('status') in ('specified', 'missing', 'conflict', 'deferred') else 'missing'
        evidence = [ev for e in slot.get('evidence', []) or [] if e.get('source_id') == 'user'
                    for ev in _evidence_at(state, e.get('turn', -1), e.get('quote', ''))]
        result.append(Condition(key=key, value=slot.get('value'), status=status, evidence=evidence, origin='legacy'))
    # Unresolved requirement items wait as core questions (source 'requirement').
    for item in (state.get('orchestration') or {}).get('selections', []) or []:
        evidence = item.get('evidence') or {}
        result.append(Condition(key=item['key'], value=item.get('criterion'),
                                status='specified' if item.get('status') == 'specified' else 'missing',
                                evidence=_evidence_at(state, evidence.get('turn', -1), evidence.get('quote', '')) if evidence else [],
                                origin='user' if item.get('status') == 'specified' else 'model'))
    keys = {c.key for c in result}
    for q in state.get('pending_questions', []) or []:
        if q.get('source') in ('requirement', 'orchestration') and q.get('key') not in keys:
            keys.add(q['key'])
            result.append(Condition(key=q['key'], status='missing', required=q.get('required', True), origin='model'))
    return result


def _inputs(state):
    from agent_builder.design import file_timing
    users = _user_turns(state)
    inputs = {}
    for resource in state.get('resources', []) or []:
        inputs[resource['id']] = FileInput(subject=resource.get('name') or resource['id'], timing='attached', resource_id=resource['id'])
    texts = [state['messages'][i]['content'] for i in users]
    for subject in sorted(file_timing.deferred_subjects(texts)) if texts else []:
        turn = next((i for i in reversed(users) if any(c == 'execution' for c, _ in file_timing.clause_choices(state['messages'][i]['content']))), None)
        evidence = [Evidence(turn=turn, quote=state['messages'][turn]['content'])] if turn is not None else []
        inputs.setdefault('exec:' + subject, FileInput(subject=subject, timing='execution', evidence=evidence))
    if users and not state.get('resources'):
        latest = users[-1]
        for subject in file_timing.now_mentions(state['messages'][latest]['content']):
            key = 'now:' + subject
            if not any(i.subject == subject for i in inputs.values()):
                inputs[key] = FileInput(subject=subject, timing='now',
                                        evidence=[Evidence(turn=latest, quote=state['messages'][latest]['content'])])
    return list(inputs.values())


def _questions(state):
    seen, result = set(), []
    for q in state.get('pending_questions', []) or []:
        if not q.get('id') or q['id'] in seen:
            continue
        seen.add(q['id'])
        result.append(Question(id=q['id'], key=q.get('key', ''), source=q.get('source', 'legacy'),
                               text=q.get('text', ''), required=q.get('required', True)))
    return result


def _edits(state):
    from agent_builder.conditions.condition_readiness import schedule_removed_turn
    turn = schedule_removed_turn(state)
    if turn < 0:
        return []
    return [Edit(turn=turn, kind='remove', target='schedule', quote=state['messages'][turn]['content'])]


def digest(value):
    payload = value.model_dump(mode='json', exclude={'version'}) if isinstance(value, BaseModel) else value
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def project_state(state):
    """Read-only projection of today's ledgers into the single state format."""
    assessment = state.get('capability_assessment') or {}
    result = RequirementState(
        tasks=_tasks(state), conditions=_conditions(state), inputs=_inputs(state),
        questions=_questions(state), edits=_edits(state),
        capability={k: assessment.get(k) for k in ('decision', 'tool_ids', 'missing_actions') if k in assessment},
        system_failure=state.get('system_failure'))
    return result.model_copy(update={'version': digest(result)})


def state_issues(value, state):
    """Invariant violations; an empty list means the state is well formed."""
    issues = []
    messages = state.get('messages', []) or []
    def grounded(evidence):
        return all(0 <= e.turn < len(messages) and messages[e.turn].get('role') == 'user'
                   and e.quote in messages[e.turn].get('content', '') for e in evidence)
    for task in value.tasks:
        if not task.evidence or not grounded(task.evidence):
            issues.append(f'task {task.id}: 사용자 원문 근거가 없습니다.')
    pairs = set()
    for condition in value.conditions:
        pair = (condition.task_id, condition.key)
        if pair in pairs:
            issues.append(f'condition {condition.key}: 같은 업무에 조건이 중복됩니다.')
        pairs.add(pair)
        # The schedule timezone is an application setting (main: fixed Asia/Seoul), not a user
        # statement; every other specified value needs the user's words.
        app_setting = condition.key == 'schedule.timezone' and not condition.evidence
        if condition.status in ('specified', 'removed') and not app_setting and (not condition.evidence or not grounded(condition.evidence)):
            issues.append(f'condition {condition.key}: {condition.status} 상태인데 사용자 원문 근거가 없습니다.')
    subjects = set()
    for item in value.inputs:
        if item.subject in subjects:
            issues.append(f'input {item.subject}: 같은 입력 파일이 중복됩니다.')
        subjects.add(item.subject)
        if item.timing in ('now', 'execution') and not grounded(item.evidence):
            issues.append(f'input {item.subject}: 입력 시점의 사용자 원문 근거가 없습니다.')
    ids = [q.id for q in value.questions]
    if len(ids) != len(set(ids)):
        issues.append('questions: 질문 ID가 중복됩니다.')
    for edit in value.edits:
        if not grounded([Evidence(turn=edit.turn, quote=edit.quote)]):
            issues.append(f'edit {edit.target}: 수정 요청의 사용자 원문 근거가 없습니다.')
    if value.version and value.version != digest(value):
        issues.append('version: 상태 내용과 버전이 일치하지 않습니다.')
    return issues


def shadow_view(state):
    """Stage-1 exposure: projection plus its invariant check, never used for decisions."""
    projected = project_state(state)
    return {**projected.model_dump(mode='json'), 'issues': state_issues(projected, state)}
