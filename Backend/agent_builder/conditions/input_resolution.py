"""Deterministic handling of non-answers to pending clarification questions.

Only entire answers match. A request filtering the literal text '모르겠어' is
ordinary data, not a refusal. Values remain missing until real evidence arrives.
"""
import re
from decimal import Decimal, localcontext


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


def pending_nonanswer(state):
    messages=state.get('messages',[])
    text=next((m['content'] for m in reversed(messages) if m['role']=='user'),'')
    return bool(_compact(text) in {'네','예','응','확인','알겠어','알겠습니다'} and
                any(q.get('key') in {'filter.field','duplicate.field','filter.value'}
                    for q in state.get('pending_questions',[])))


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
_PLAIN_AMOUNT = re.compile(r'(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(천만|백만|십만|만|천|백)?'
                           r'\s*(원|점|개|건|명)?\s*(?:으로|로|요|이요)?')
_COLUMN_ANNOTATION = re.compile(r'[\[(]([^\])]+)[\])]$')
_SCALED_UNIT = re.compile(r'(억|천만|백만|십만|만|천|백)?(원|점|개|건|명|kg|%|USD|KRW|달러|유로|엔|킬로)', re.I)
_UNIT_ALIASES = {'KRW': '원', 'USD': '달러', '킬로': 'kg'}


def column_unit(field):
    annotation = _COLUMN_ANNOTATION.search(re.sub(r'\s+', '', field or ''))
    if not annotation:
        return None
    label = annotation[1]
    name = re.sub(r'^단위[:：]?', '', label)
    unit = _SCALED_UNIT.fullmatch(name)
    if unit:
        scale, counter = unit.groups()
        divisor = 100000000 if scale == '억' else _UNITS[scale or '']
        return _UNIT_ALIASES.get(counter.upper(), counter.lower()), divisor
    # An explicit declaration or a code such as EUR identifies a unit even
    # if we cannot convert it. Other labels such as (세전) describe the field.
    if name != label or re.fullmatch(r'[A-Z]{2,4}', name):
        return name.casefold(), 1
    return None


def column_amount(number, field, unit):
    """Explicitly unit-bearing thresholds use the scale printed in the header.

    Bare numbers already refer to the values as stored in that column.
    """
    header = column_unit(field)
    if not header or not unit:
        return number
    counter, divisor = header
    if counter != _UNIT_ALIASES.get(unit.upper(), unit.lower()):
        return None
    return number / divisor


def executable_number(number):
    """The executor compares floats, so the persisted decimal must round-trip."""
    return number.is_finite() and Decimal(str(float(number))) == number


def numeric_column(state, field):
    profiles = ((r.get('profile', {}).get(field) or {}) for r in state.get('resources', [])
                if r.get('kind') == 'table')
    return any(
        profile.get('numericStrings', 0) > 0 or 'number' in profile.get('types', [])
        for profile in profiles
    )


def value_answer(state, *, _allow_comparison=False):
    """The latest turn is only one plain amount for the pending threshold of a numeric column.

    Anything else (억·조, %, k, currency signs, a sign, ranges, dates, extra words, a
    text column) keeps the model path; nothing is parsed by guessing.
    """
    messages = state.get('messages', [])
    latest = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=-1)
    if latest < 0:
        return None
    text=messages[latest]['content'].strip().rstrip('.!?。')
    if _allow_comparison:
        comparison=re.fullmatch(r'(.+?)\s*(초과|이상|미만|이하)(?:으로|로)?(?:요)?',text)
        if not comparison:return None
        text=comparison[1]
    match = _PLAIN_AMOUNT.fullmatch(text)
    if not match:
        return None
    events = state.get('condition_answers', [])
    event = next((e for e in reversed(events) if e.get('turn') == latest), None)
    pending = [q for q in state.get('pending_questions', []) if q['key'] == 'filter.value'
               and (not event or q['id'] in event.get('question_ids', []))]
    field = (state.get('condition_ledger') or {}).get('slots', {}).get('filter.field', {})
    if len(pending) != 1 or field.get('status') != 'specified' or not numeric_column(state, field.get('value')):
        return None
    with localcontext() as context:
        context.prec = max(context.prec, len(text) + 10)
        number = Decimal(match.group(1).replace(',', '') + (match.group(2) or '')) * _UNITS[match.group(3) or '']
        number = column_amount(number, field['value'], match.group(4))
    # This is an answer to the pending question even when it cannot safely
    # be executed. Keep that slot missing instead of letting a model guess.
    answer = {'key': 'filter.value', 'value': number, 'question_id': pending[0]['id'], 'turn': latest}
    if number is None:
        answer['issue'] = f"{field['value']} 열에 표시된 단위로 기준 값을 입력해주세요."
    elif not executable_number(number):
        answer.update(value=None, issue='이 기준 값은 실행 시 정확히 표현할 수 없습니다. 사용할 다른 기준 값이 있나요?')
    return answer


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


def numeric_answer(state):
    """A plain amount or exact amount+comparison for a pending numeric threshold.

    The comparison is separately grounded by explicit_policies; this helper only
    resolves the numeric slot. Negations, ranges and approximate amounts do not match.
    """
    return value_answer(state) or value_answer(state,_allow_comparison=True)
