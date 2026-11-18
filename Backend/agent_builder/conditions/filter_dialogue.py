"""Closed replies to filter questions, shared by replay and output-scope detection.

These labels describe a decision about existing predicates, never an artifact.
Free-form requests are deliberately left intact for the normal extractors.
"""
import re
from dataclasses import dataclass

KEEP_ALL, KEEP_OTHERS, ONLY_NEW = '기존 조건 모두 유지', '나머지 조건 유지', '새 조건만 적용'
MATCH_CHOICES = {'모두 만족': 'all', '하나라도 만족': 'any'}


def match_answer(text, count):
    word = text.strip().casefold()
    if word in ('and', 'or'):
        return 'all' if word == 'and' else 'any'
    text = re.sub(r'\s+', '', text.strip().rstrip('.!?。'))
    choices = {re.sub(r'\s+', '', label): mode for label, mode in MATCH_CHOICES.items()}
    if count == 2:
        choices.update({'둘다': 'all', '둘다만족': 'all'})
    return choices.get(text)


def control_reply(text):
    return text.strip().rstrip('.!?。') in (KEEP_ALL, KEEP_OTHERS, ONLY_NEW) or bool(match_answer(text, 2))


def affirmative_reply(text):
    answer = re.sub(r'\s+', '', text.strip().rstrip('.!?。！？')).casefold()
    return answer in {'네', '예', '응', '맞아', '맞아요', '맞습니다', '그래', '그래요', 'yes', 'ok'}


@dataclass(frozen=True)
class PendingFilterDecision:
    """A question's reason and permitted resolution, independent of the value's type."""
    reason: str
    message: str
    can_keep: bool
    proposed: tuple = ()
    predicate: tuple[int, dict] | None = None


def confirmed_numeric_filter(state):
    """The single numeric filter the last ledger confirmed, and the turn that settled it."""
    ledger = state.get('condition_ledger') or {}
    slots = ledger.get('slots') or {}
    parts = [slots.get(key) or {} for key in ('filter.field', 'filter.operator', 'filter.value')]
    if (any(part.get('status') != 'specified' for part in parts) or parts[1].get('value') not in {'gt', 'gte', 'lt', 'lte'}
            or isinstance(parts[2].get('value'), bool) or not isinstance(parts[2].get('value'), (int, float))):
        return None, None, False
    turns = [item.get('turn') for part in parts for item in part.get('evidence') or []
             if isinstance(item.get('turn'), int)]
    if not turns:
        return None, None, False
    return (dict(field=parts[0]['value'], operator=parts[1]['value'], value=float(parts[2]['value']),
                 compare_field=None, value_type='number'), max(turns), False)
