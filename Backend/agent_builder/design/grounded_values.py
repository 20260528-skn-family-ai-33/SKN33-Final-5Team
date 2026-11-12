"""Filter values a planner filled in must come from the user, an attached document or a settled answer.

A draft can pass every structural check while holding a threshold the user never gave
(2026-11-11 RunPod: X07 "amount가 큰 행" became amount > 0). Such a value becomes a question for
the user instead of a ready workflow. This is a verification over closed values, not a decision
from the request's wording:

- a filter's constant value must appear in the evidence (numbers compared by value, with
  Korean units such as 만/천/억, written Korean numerals such as 십만, and thousands separators);
- an emptiness filter (is_empty / not_empty) needs the user to have spoken about empty values;
  otherwise "완료 주문" could become 주문상태 is_empty (2026-11-12 Aside AI S5).

Evidence is the user's messages (answers included), attached document text, values the
condition stage already settled, and the application's calendar context (dates only).
Computed columns are checked by main's compute_grounding, not here.
"""
import json
import re

_NUMBER = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(억|만|천|백)?')
_UNIT = {'억': 100_000_000, '만': 10_000, '천': 1_000, '백': 100}
# Words that speak about empty values (closed set); checked in the user's own messages only.
_EMPTY_WORDS = re.compile(r'빈\s*(?:값|칸|셀|행|문자)?|비어|비었|공백|공란|없는|없음|없으면|없을|누락|결측|'
                          r'미입력|미기재|미작성|미등록|null|none|nan|empty|blank|missing', re.I)


def _evidence(state):
    parts = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    for resource in state.get('resources', []) or []:
        if resource.get('kind') == 'document':
            parts.append(resource.get('text') or '')
            parts.extend(resource.get('pages') or [])
    for slot in ((state.get('condition_ledger') or {}).get('slots') or {}).values():
        if slot.get('status') == 'specified' and slot.get('value') is not None:
            parts.append(str(slot['value']))
    return '\n'.join(p for p in parts if isinstance(p, str))


def _calendar_dates(state):
    """Only the calendar dates the application computed ("어제" -> 2026-11-10).

    Its other numbers (00:00, versions, epochs) are not evidence: counting them let the
    invented threshold 0 pass (2026-11-11 RunPod X07).
    """
    context = state.get('temporal_context')
    if not context:
        return set()
    return set(re.findall(r'\d{4}-\d{2}-\d{2}', json.dumps(context, ensure_ascii=False)))


_KO_DIGIT = {'일': 1, '이': 2, '삼': 3, '사': 4, '오': 5, '육': 6, '칠': 7, '팔': 8, '구': 9}
_KO_SMALL = {'십': 10, '백': 100, '천': 1000}
_KO_LARGE = {'만': 10_000, '억': 100_000_000}
_KO_NUMBER = re.compile(r'[일이삼사오육칠팔구십백천]+[만억]|[일이삼사오육칠팔구]?[십백천][일이삼사오육칠팔구십백천]*')


def _korean_number(word):
    """'오십만' -> 500000, '십만' -> 100000, '삼천' -> 3000 (written amounts only)."""
    total, section, digit = 0, 0, 0
    for ch in word:
        if ch in _KO_DIGIT:
            digit = _KO_DIGIT[ch]
        elif ch in _KO_SMALL:
            section += (digit or 1) * _KO_SMALL[ch]; digit = 0
        elif ch in _KO_LARGE:
            total += (section + digit or 1) * _KO_LARGE[ch]; section = digit = 0
    return total + section + digit


def _numbers(text):
    values = set()
    for digits, unit in _NUMBER.findall(text):
        try:
            value = float(digits.replace(',', ''))
        except ValueError:
            continue
        values.add(value)
        if unit:
            values.add(value * _UNIT[unit])
    values.update(float(_korean_number(w)) for w in _KO_NUMBER.findall(text))
    return values


def _grounded_value(value, text, numbers):
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        return float(value) in numbers
    written = str(value).strip()
    if not written:
        return True
    if written.lower() in text.lower():
        return True
    groups = re.findall(r'\d+(?:\.\d+)?', written)
    # A date or code ("2020-01-01") is grounded when each of its numbers was written.
    return bool(groups) and all(float(g) in numbers for g in groups)


def _plans(draft):
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    plans = [n['settings'].get('execution_plan') for n in nodes
             if n.get('tool') == 'filter_sort' and not n['settings'].get('operation') and n['settings'].get('execution_plan')]
    if not draft.get('workflow') and draft.get('execution_plan'):
        plans.append(draft['execution_plan'])
    return plans


def ungrounded_value_questions(state, draft):
    """Questions for planner-filled values without evidence; [] when every value is grounded."""
    text = _evidence(state)
    numbers = _numbers(text)
    dates = _calendar_dates(state)
    user_text = '\n'.join(m['content'] for m in state.get('messages', []) if m.get('role') == 'user')
    questions = []
    for plan in _plans(draft):
        for f in plan.get('filters') or []:
            if f.get('operator') in ('is_empty', 'not_empty'):
                if not _EMPTY_WORDS.search(user_text):
                    questions.append(f"{f.get('field') or '선별'} 열은 어떤 값을 기준으로 선별할까요?")
                continue
            if f.get('compare_field'):
                continue
            value = f.get('value')
            if isinstance(value, str) and value.strip()[:10] in dates:
                continue
            if not _grounded_value(value, text, numbers):
                questions.append(f"{f.get('field') or '선별'} 열을 선별할 기준 값은 얼마인가요?")
    # Computed columns (formula and name) are grounded by main's compute_grounding, which knows
    # the request's own expression and Korean column wording; a second, stricter check here asked
    # again for "수량에 단가를 곱한 합계 열" because the columns are named qty/unit_price.
    return list(dict.fromkeys(questions))
