"""Filter values a planner filled in must come from the user, an attached document or a settled answer.

A draft can pass every structural check while holding a threshold the user never gave
(2026-11-11 RunPod: X07 "amount가 큰 행" became amount > 0). Such a value becomes a question for
the user instead of a ready workflow. This is a verification over closed values, not a decision
from the request's wording:

- a numeric filter value must be tied to its field or an explicit comparison in the
  evidence (Korean units, written numerals and thousands separators are normalized);
- an emptiness filter (is_empty / not_empty) needs the user to have spoken about empty values;
  otherwise "완료 주문" could become 주문상태 is_empty (2026-11-12 Aside AI S5).

Evidence is the user's messages (answers included), attached document text, values the
condition stage already settled, and the application's calendar context (dates only).
Computed columns are checked by main's compute_grounding, not here.
"""
import json
import re

_NUMBER = re.compile(r'(?<![\w.+-])([+-]?\d[\d,]*(?:\.\d+)?)\s*(억|만|천|백)?')
_SCALAR_NUMBER = re.compile(r'[+-]?\d[\d,]*(?:\.\d+)?')
_CLOCK_TIME = re.compile(r'(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)')
_ISO_DATE = re.compile(r'(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)')
_CLAUSE_BREAK = re.compile(r'(?<!\d)[,，;；。!?]|[,，;；。!?](?!\d)|\n')
_FIELD_TO_VALUE = re.compile(r'\s*(?:(?:이|가|은|는|을|를|의|값|기준|약)\s*)*[:=<>]*\s*')
_VALUE_TO_FIELD = re.compile(r'\s*(?:원\s*)?(?:이상|초과|이하|미만)(?:인|의)?\s*')
_COMPARISON_AFTER = re.compile(r'\s*(?:(?:원|건|개|명|행|회|번)\s*)?(?:이상|초과|이하|미만|보다|넘|같|동일|부터|까지|인(?:\s|$))')
_COMPARISON_BEFORE = re.compile(r'(?:최소|최대|>=|<=|>|<|=)\s*$')
_COUNT_UNIT = re.compile(r'\s*(?:건|개|명|행|회|번)')
_BARE_NUMBER = re.compile(r'\s*(?:[+-]?\d[\d,]*(?:\.\d+)?\s*(?:억|만|천|백)?|[일이삼사오육칠팔구십백천만억]+)\s*원?\s*')
_UNIT = {'억': 100_000_000, '만': 10_000, '천': 1_000, '백': 100}
# Words that speak about empty values (closed set); checked in the user's own messages only.
_EMPTY_WORDS = re.compile(r'빈\s*(?:값|칸|셀|행|문자)?|비어|비었|공백|공란|없는|없음|없으면|없을|누락|결측|'
                          r'미입력|미기재|미작성|미등록|null|none|nan|empty|blank|missing', re.I)


def _source_texts(state):
    parts = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    for resource in state.get('resources', []) or []:
        if resource.get('kind') == 'document':
            parts.append(resource.get('text') or '')
            parts.extend(resource.get('pages') or [])
    return [p for p in parts if isinstance(p, str)]


def _settled_values(state):
    values = []
    for slot in ((state.get('condition_ledger') or {}).get('slots') or {}).values():
        if slot.get('status') == 'specified' and slot.get('value') is not None:
            values.append(str(slot['value']))
    return values


def _evidence(state):
    return '\n'.join(_source_texts(state) + _settled_values(state))


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


def _number_mentions(text):
    # Schedule times and dates are not filter thresholds. Date strings are checked
    # separately as whole values in _grounded_value / _calendar_dates.
    text = _ISO_DATE.sub(lambda m: ' ' * len(m.group()),
                         _CLOCK_TIME.sub(lambda m: ' ' * len(m.group()), text))
    for match in _NUMBER.finditer(text):
        digits, unit = match.groups()
        try:
            value = float(digits.replace(',', ''))
        except ValueError:
            continue
        if unit:
            value *= _UNIT[unit]
        yield value, match.start(), match.end()
    for match in _KO_NUMBER.finditer(text):
        yield float(_korean_number(match.group())), match.start(), match.end()


def _numbers(text):
    return {value for value, _, _ in _number_mentions(text)}


def _field_pattern(field):
    escaped = re.escape(field)
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field):
        escaped = rf'(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])'
    return re.compile(escaped, re.I)


def _field_linked(clause, start, end, field_pattern):
    for match in field_pattern.finditer(clause):
        if match.end() <= start:
            between = clause[match.end():start]
            count_without_comparison = (_COUNT_UNIT.match(clause[end:]) and
                                        not _COMPARISON_AFTER.match(clause[end:]) and
                                        not _COMPARISON_BEFORE.search(clause[:start]))
            if len(between) <= 16 and _FIELD_TO_VALUE.fullmatch(between) and not count_without_comparison:
                return True
        elif end <= match.start():
            between = clause[end:match.start()]
            if len(between) <= 20 and _VALUE_TO_FIELD.fullmatch(between):
                return True
    return False


def _numeric_grounded(value, state, field):
    slots = ((state.get('condition_ledger') or {}).get('slots') or {})
    settled_field = slots.get('filter.field') or {}
    settled_value = slots.get('filter.value') or {}
    if (settled_field.get('status') == 'specified' and settled_value.get('status') == 'specified' and
            isinstance(field, str) and isinstance(settled_field.get('value'), str) and
            settled_field['value'].strip().casefold() == field.strip().casefold() and
            value in _numbers(str(settled_value.get('value')))):
        return True
    sources = _source_texts(state)
    field_pattern = _field_pattern(field) if isinstance(field, str) and field else None
    field_mentioned = bool(field_pattern and any(field_pattern.search(source) for source in sources))
    for source in sources:
        for clause in _CLAUSE_BREAK.split(source):
            for number, start, end in _number_mentions(clause):
                if number != value:
                    continue
                if _BARE_NUMBER.fullmatch(clause):  # A short answer to a threshold question.
                    return True
                if field_mentioned:
                    if _field_linked(clause, start, end, field_pattern):
                        return True
                elif (_COMPARISON_AFTER.match(clause[end:]) or
                      _COMPARISON_BEFORE.search(clause[:start])):
                    return True
    return False


def _scalar_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _SCALAR_NUMBER.fullmatch(value.strip()):
        return float(value.replace(',', ''))
    return None


def _grounded_value(value, text, numbers):
    if isinstance(value, bool) or value is None:
        return True
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
            scalar = _scalar_number(value)
            grounded = (_numeric_grounded(scalar, state, f.get('field')) if scalar is not None
                        else _grounded_value(value, text, numbers))
            if not grounded:
                questions.append(f"{f.get('field') or '선별'} 열을 선별할 기준 값은 얼마인가요?")
    # Computed columns (formula and name) are grounded by main's compute_grounding, which knows
    # the request's own expression and Korean column wording; a second, stricter check here asked
    # again for "수량에 단가를 곱한 합계 열" because the columns are named qty/unit_price.
    return list(dict.fromkeys(questions))

