"""Stage 2: required selection criteria are decided by code, not by the model's verdict.

The model only extracts *where* the request selects or judges items and *what* the user said
the criterion is, as quotes of the user's own words. Code then decides:

- a criterion is specified only when its quote is really in a user message and the model
  marked it concrete, or when the user answered this criterion's question (the answer is the
  evidence, so a later model miss cannot re-open it);
- a delegation ("알아서", "상관없") is not a criterion;
- a selection without a specified criterion is a required question, even when the
  capability assessment said the request is ready.

Scope: requests outside the single-table condition checker, which already asks for its own
threshold slots. Mode ``A4B_ORCHESTRATION_MODE``: off (no call), shadow (record only),
enforce (ask).
"""
import hashlib
import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field

from agent_builder.conditions.clarification_core import question_record
from agent_builder.conditions.question_contract import concrete_specifier

SOURCE = 'orchestration'
# Single-table work is owned by condition_check, which asks for its own threshold slots.
TABLE_SCOPE = frozenset({'read_table', 'filter_sort', 'write_result', 'finish', 'aggregate_table', 'calculate_columns'})
# Shared with the condition checker: indifference/delegation is not a policy or criterion.
_DELEGATION = re.compile(r'알아서|적당히|상관\s*없|아무(?:거나|렇게나)|마음대로|맡길게|맡겨')


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_part: str = Field(min_length=1, max_length=300)
    target: str = Field(min_length=1, max_length=80)
    criterion: str | None = Field(default=None, max_length=300)
    criterion_kind: str = Field(pattern='^(concrete|vague|none)$')


class Extraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selections: list[Selection] = Field(max_length=5)


SYSTEM = '''사용자 업무 요청에서 "항목의 일부만 고르거나, 항목을 평가해 판정하는 부분"을 찾습니다.
예: 특정 조항만 찾기, 일부 메일만 고르기, 문제가 있는 부분 판정. 단순 요약·번역·형식 변환·전체 추출·정렬 방향은 해당하지 않습니다.
각 부분마다 request_part(사용자 원문 그대로의 일부), target(고르는 대상), criterion(사용자가 말한 판단 기준 원문 그대로, 없으면 null),
criterion_kind를 적습니다. concrete: 값·조건·명시된 항목처럼 누구나 같은 결과를 낼 수 있는 기준. vague: 위험한·중요한·큰·오래된처럼 평가어만 있고 정의가 없음.
none: 기준이 없음. 대화 전체를 보고, 뒤의 답변이 기준을 정했으면 그 원문을 criterion으로 적습니다. 원문에 없는 기준을 만들지 않습니다.
해당 부분이 없으면 selections=[]입니다. JSON만 반환합니다.'''


def mode():
    value = os.getenv('A4B_ORCHESTRATION_MODE', 'off')
    if value not in ('off', 'shadow', 'enforce'):
        raise ValueError('A4B_ORCHESTRATION_MODE는 off/shadow/enforce여야 합니다.')
    return value


def in_scope(state):
    assessment = state.get('capability_assessment') or {}
    tools = set(assessment.get('tool_ids') or [])
    if assessment.get('decision') != 'existing_tools' or not tools:
        return False
    return not tools <= TABLE_SCOPE


def _compact(text):
    return re.sub(r'\s+', '', text or '')


def _grounded(quote, users):
    q = _compact(quote)
    return bool(q) and any(q in _compact(u) for u in users)


def _strip_tail(phrase):
    """'위험한 조항만' -> '위험한 조항': a delimiter particle on the head is not part of the name."""
    return re.sub(r'(?:만|도|은|는|을|를|이|가)$', '', phrase.strip())


def _key(target):
    return 'criterion.' + hashlib.sha256(_compact(target).encode()).hexdigest()[:10]


def _about(answer, item):
    """An answer counts for this selection only if it shares words with what was selected.
    Without this, an unrecognised side remark ("날씨 좀 알려줘") or non-answer
    ("잘 모르겠네요") would be taken as the criterion."""
    from agent_builder.design.turn_intent import _stems, _related
    words = _stems(item.target + ' ' + item.request_part)
    return bool(words) and _related(_stems(answer or ''), words)


def _answered(state, key, item=None):
    """The user's real answer to this criterion's question, if any (turn, text).

    The key comes from the model's target wording, which can change between turns
    ("중요한 메일" then "메일"). When exactly one criterion question was waiting, a real
    answer to it settles the criterion whatever the target is called now.
    """
    questions = (state.get('orchestration') or {}).get('questions', [])
    pending = [q for q in state.get('pending_questions', []) or [] if q.get('source') == SOURCE]
    asked = {q['id'] for q in questions if q.get('key') == key}
    single = len(pending) == 1 and pending[0]['id'] not in asked
    if single:
        asked.add(pending[0]['id'])
    # The same criterion may have been asked by another stage (the capability check asked
    # "중요한 메일을 선별할 기준"). A real answer in the latest user turn to that single
    # question is the criterion too; stages share one answer, not one question list each.
    messages = state.get('messages', [])
    latest = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
    for event in reversed(state.get('capability_answers', []) or []):
        if (event.get('turn') == latest and event.get('kind') == 'answer' and len(event.get('question_ids', [])) == 1
                and not _DELEGATION.search(event.get('answer', '')) and item is not None and _about(event.get('answer'), item)):
            return event
    for event in reversed(state.get('clarification_answers', []) or []):
        if asked.intersection(event.get('question_ids', [])) and event.get('kind') == 'answer' \
                and not _DELEGATION.search(event.get('answer', '')) and (item is None or _about(event.get('answer'), item)):
            return event
    return None


def extract(state, transport):
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    schema = Extraction.model_json_schema()
    transport.schema = schema
    reply = transport.invoke([{'role': 'system', 'content': SYSTEM},
                              {'role': 'user', 'content': json.dumps({'user_messages': users}, ensure_ascii=False)}])
    content = reply.get('content') if isinstance(reply, dict) else reply.content
    return Extraction.model_validate_json(content)


def decide(state, extraction):
    """Verified selections and the required questions for criteria that are not specified."""
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    selections, questions, seen = [], [], set()
    for item in extraction.selections:
        if not _grounded(item.request_part, users):
            continue  # not the user's request: dropped, never asked about
        # Shown text must be the user's words too: an ungrounded target falls back to the request part.
        target = item.target if _grounded(item.target, users) else item.request_part.strip()
        item = item.model_copy(update={'target': target})
        key = _key(item.target)
        if key in seen:
            continue
        seen.add(key)
        record = {'key': key, 'target': item.target, 'request_part': item.request_part, 'criterion': None,
                  'status': 'missing', 'evidence': None}
        answer = _answered(state, key, item)
        if answer:
            record.update(criterion=answer['answer'], status='specified', evidence={'turn': answer['turn'], 'quote': answer['answer']})
        elif (item.criterion_kind == 'concrete' and item.criterion and _grounded(item.criterion, users)
              and not _DELEGATION.search(item.criterion)):
            turn = max(i for i, m in enumerate(state['messages']) if m.get('role') == 'user'
                       and _compact(item.criterion) in _compact(m['content']))
            record.update(criterion=item.criterion, status='specified', evidence={'turn': turn, 'quote': item.criterion})
        elif specifier := next((p for p in (item.request_part, item.target)
                                if _grounded(p, users) and concrete_specifier(_strip_tail(p))), None):
            # "위약금 조항이 있는 부분", "개인정보 관련 조항": the selection names its items concretely
            # even when the model left the criterion empty.
            turn = max(i for i, m in enumerate(state['messages']) if m.get('role') == 'user'
                       and _compact(specifier) in _compact(m['content']))
            record.update(criterion=specifier, status='specified', evidence={'turn': turn, 'quote': specifier})
        else:
            record['status'] = 'vague' if item.criterion_kind == 'vague' else 'missing'
            text = f'"{item.target}"을(를) 고르는 기준을 구체적으로 알려주세요.'
            questions.append(question_record(SOURCE, key, text, target=item.target))
        selections.append(record)
    return {'selections': selections, 'questions': questions}


def make_orchestration_node(model, transport=None, error_observer=None):
    def node(state):
        setting = mode()
        if setting == 'off' or not in_scope(state):
            return {'orchestration_route': 'continue'}
        try:
            active = transport
            if active is None:
                from agent_builder.design.nodes.direct_conversation import OllamaPlanner
                active = OllamaPlanner(os.getenv('A4B_ORCHESTRATION_MODEL') or os.getenv('A4B_CAPABILITY_MODEL') or model, temperature=0)
            extraction = extract(state, active)
            decision = decide(state, extraction)
            calls = 1
            if extraction.selections and not decision['selections']:
                # Every proposed part was outside the user's words (a live 9B pilot returned
                # "자료와 조건을 확인해 주세요"). Ask once more with that evidence.
                from agent_builder.capabilities.capability_check import _FeedbackTransport
                dropped = ' / '.join(item.request_part for item in extraction.selections)
                feedback = ('앞선 request_part는 사용자 원문에 없어 무효입니다: ' + dropped +
                            '. 사용자 메시지에 실제로 있는 구간만 그대로 인용하고, 해당 부분이 없으면 selections=[]로 반환하세요.')
                extraction = extract(state, _FeedbackTransport(active, feedback))
                decision = decide(state, extraction)
                calls = 2
            failure = 'ungrounded_extraction' if extraction.selections and not decision['selections'] else None
        except Exception as error:
            # The extraction is a helper: its failure keeps the existing path (and is recorded).
            if error_observer:
                error_observer(error)
            return {'orchestration_route': 'continue',
                    'orchestration': {**(state.get('orchestration') or {}), 'error': type(error).__name__},
                    'trace': state.get('trace', []) + [{'node': 'orchestration', 'label': '선별 기준 확인', 'status': 'completed',
                                                        'detail': '기준 추출 실패: 기존 경로로 진행', 'error_type': type(error).__name__}]}
        previous = (state.get('orchestration') or {}).get('questions', [])
        stored = {'selections': decision['selections'], 'mode': setting, 'extraction_failure': failure,
                  # Keep every question ever asked so a later answer can still be matched to it.
                  'questions': list({q['id']: q for q in previous + decision['questions']}.values())}
        update = {'orchestration': stored, 'orchestration_route': 'continue',
                  'trace': state.get('trace', []) + [{'node': 'orchestration', 'label': '선별 기준 확인', 'status': 'completed' if not decision['questions'] else 'needs_input',
                                                      'detail': (f"기준 추출 실패: 원문 근거가 없는 응답만 반환됨 ({setting}, 기존 경로로 진행)" if failure else
                                                                 f"선별 {len(decision['selections'])}개, 미정 기준 {len(decision['questions'])}개 ({setting})"),
                                                      'model_calls': calls, **({'extraction_failure': failure} if failure else {})}]}
        if setting == 'enforce' and decision['questions']:
            from agent_builder.conditions.condition_check import _pending_draft
            records = decision['questions']
            update.update(orchestration_route='ask', status='needs_clarification', confirmed=False, saved_agent_id=None,
                          issues=[], semantic_issues=[], pending_questions=records,
                          draft=_pending_draft(state, {'decision': 'clarify', 'questions': records, 'slots': {}}),
                          messages=state.get('messages', []) + [{'role': 'assistant', 'content': '\n'.join(q['text'] for q in records)}])
        elif setting == 'enforce':
            update['pending_questions'] = [q for q in state.get('pending_questions', []) or [] if q.get('source') != SOURCE]
        return update
    return node
