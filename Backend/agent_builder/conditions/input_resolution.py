"""Deterministic handling of non-answers to pending clarification questions.

Only entire answers match. A request filtering the literal text '모르겠어' is
ordinary data, not a refusal. Values remain missing until real evidence arrives.
"""
import re
from decimal import Decimal


def _compact(content):
    return re.sub(r'[\s.!?。]+', '', content).lower()


def answer_kind(content):
    text = _compact(content)
    if text in {'모르겠어', '모르겠어요', '모르겠습니다', '잘모르겠어', '잘모르겠어요',
                '몰라', '몰라요', '아직몰라요', '아직모르겠어요', '미정', '아직미정', '아직안정했어',
                '아직결정못했어요', '아직결정못했어', '결정하지못했어요', '아직정하지못했어요'}:
        return 'unknown'
    if text in {'나중에결정', '나중에결정할게', '나중에할게', '보류', '보류할게', '일단보류'}:
        return 'defer'
    if text in {'자료연결후기준입력', '연결자료에서기준확인후입력', '기준을직접입력'}:
        return 'guidance'
    return 'answer'


def latest_resolution(state):
    """Bind to the current user turn and unresolved pending keys only."""
    messages = state.get('messages', [])
    latest = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=-1)
    if latest < 0:
        return None
    pending = state.get('pending_questions', [])
    if not pending:
        return None
    events = state.get('condition_answers', [])
    event = next((e for e in reversed(events) if e.get('turn') == latest), None)
    content = messages[latest]['content']
    kind = answer_kind(content)
    if kind == 'answer':
        return None
    keys = [q['key'] for q in pending if not event or q['id'] in event.get('question_ids', [])]
    slots = (state.get('condition_ledger') or {}).get('slots', {})
    keys = [k for k in keys if slots.get(k, {}).get('status') != 'specified']
    if not keys:
        return None
    previous = (state.get('condition_ledger') or {}).get('resolution') or {}
    counts = {}
    for key in keys:
        counts[key] = previous.get('unknown_counts', {}).get(key, 0)
        if kind == 'unknown' and previous.get('turn') != latest:
            counts[key] += 1
    deferred = kind == 'defer' or kind == 'unknown' and any(n >= 2 for n in counts.values())
    return {'kind': kind, 'keys': keys, 'unknown_counts': counts, 'deferred': deferred, 'turn': latest}


def option_answer(state):
    """The latest user turn is exactly one offered choice of one pending question.

    Free text, non-answers and a label shared by several pending questions are
    not choices; they keep the model path so nothing is guessed from them.
    """
    messages = state.get('messages', [])
    latest = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=-1)
    if latest < 0 or answer_kind(messages[latest]['content']) != 'answer':
        return None
    events = state.get('condition_answers', [])
    event = next((e for e in reversed(events) if e.get('turn') == latest), None)
    text = _compact(messages[latest]['content'])
    matches = [{'key': q['key'], 'label': option, 'question_id': q['id'], 'turn': latest}
               for q in state.get('pending_questions', [])
               if not event or q['id'] in event.get('question_ids', [])
               for option in q.get('options', []) if _compact(option) == text]
    return matches[0] if len(matches) == 1 else None


_UNITS = {'천만': 10000000, '백만': 1000000, '십만': 100000, '만': 10000, '천': 1000, '백': 100, '': 1}
_PLAIN_AMOUNT = re.compile(r'(?:약\s*)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(천만|백만|십만|만|천|백)?'
                           r'\s*(?:원|점|개|건|명)?\s*(?:으로|로|요|이요)?')


def value_answer(state):
    """The latest turn is only one plain amount for the pending threshold of a numeric column.

    Anything else (억·조, %, k, currency signs, a sign, ranges, dates, extra words, a
    text column) keeps the model path; nothing is parsed by guessing.
    """
    messages = state.get('messages', [])
    latest = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=-1)
    if latest < 0:
        return None
    match = _PLAIN_AMOUNT.fullmatch(messages[latest]['content'].strip().rstrip('.!?。'))
    if not match:
        return None
    events = state.get('condition_answers', [])
    event = next((e for e in reversed(events) if e.get('turn') == latest), None)
    pending = [q for q in state.get('pending_questions', []) if q['key'] == 'filter.value'
               and (not event or q['id'] in event.get('question_ids', []))]
    field = (state.get('condition_ledger') or {}).get('slots', {}).get('filter.field', {})
    profile = lambda r: (r.get('profile', {}).get(field.get('value')) or {})
    numeric = field.get('status') == 'specified' and any(
        r.get('kind') == 'table' and (profile(r).get('numericStrings', 0) > 0 or 'number' in profile(r).get('types', []))
        for r in state.get('resources', []))
    if len(pending) != 1 or not numeric:
        return None
    number = Decimal(match.group(1).replace(',', '') + (match.group(2) or '')) * _UNITS[match.group(3) or '']
    return {'key': 'filter.value', 'value': number, 'question_id': pending[0]['id'], 'turn': latest}


def resolution_question(key, resolution, base):
    label = {'filter.field': '선별할 열', 'filter.operator': '비교 방식', 'filter.value': '선별 기준값',
             'duplicate.field': '중복 판단 열', 'duplicate.policy': '중복 처리 방식',
             'input.table': '예약에 사용할 표 파일'}.get(key, base.rstrip('?'))
    if resolution['deferred']:
        return f'{label}: 미정 상태로 보류했습니다. 확인한 값이나 자료를 보내면 이어서 진행합니다.'
    return f'{label}: 임의로 정하지 않았습니다. 자료·담당자 확인 후 입력하거나 나중에 결정할 수 있습니다.'


RESOLUTION_OPTIONS = ['자료 연결 후 기준 입력', '기준을 직접 입력', '나중에 결정']


def resolution_options(state):
    options = list(RESOLUTION_OPTIONS)
    if state.get('resources'):
        options[0] = '연결 자료에서 기준 확인 후 입력'
    return options
