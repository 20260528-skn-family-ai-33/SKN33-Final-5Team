"""Retire explicitly cancelled condition evidence without erasing conversation history."""
import re


def cancellation_turns(state):
    field = (state.get('condition_ledger') or {}).get('slots', {}).get('filter.field', {}).get('value')
    subjects = {
        'filter': r'(?:선별|필터링|필터)(?:\s*조건)?|(?:비용|금액)\s*(?:선별\s*)?조건',
        'deduplicate': r'중복\s*(?:제거|처리)(?:\s*조건)?',
    }
    if isinstance(field, str) and field:
        subjects['filter'] += '|' + re.escape(field) + r'\s*(?:선별\s*)?조건'
    result = dict((state.get('condition_ledger') or {}).get('retired_operation_turns', {}))
    for turn, message in enumerate(state.get('messages', [])):
        if message.get('role') != 'user':
            continue
        for operation, subject in subjects.items():
            # Adjacent, explicit scope only. "취소하지 마" and data values quoted
            # inside a filter are not cancellations. Ambiguous prose stays model-owned.
            cancel = r'취소(?:해(?:줘|주세요)?|하고|할게(?:요)?|하겠습니다|합니다|하자|(?=[.!?,\n]|$))'
            pattern = r'(?:^|[\s,.!])(?:' + subject + r')\s*(?:은|는|을|를)?\s*(?:' + cancel + r'|빼(?:줘|고|주세요)|하지\s*마(?:세요|(?=[\s.!?,]|$)))'
            if re.search(pattern, message['content']):
                result[operation] = turn
    return result


def evidence_retired(key, evidence, cutoffs):
    operation = 'filter' if key.startswith('filter.') else 'deduplicate' if key.startswith('duplicate.') else None
    if operation not in cutoffs:
        return False
    # Evidence from the cancellation turn itself cannot silently re-add the same
    # operation. A later explicit request can supply fresh conditions normally.
    return not evidence or all(e.get('source_id') != 'user' or e.get('turn', -1) <= cutoffs[operation]
                               for e in evidence)


def replacement_needs_review(state, cutoffs):
    """Do not silently drop a replacement requested in the cancellation turn."""
    latest = max((i for i, m in enumerate(state.get('messages', [])) if m.get('role') == 'user'), default=-1)
    if latest < 0:
        return False
    text = state['messages'][latest]['content']
    parts = re.split(r'취소(?:해(?:줘|주세요)?|하고|할게(?:요)?|하겠습니다|합니다|하자)|빼(?:줘|고|주세요)|하지\s*마(?:세요)?', text, maxsplit=1)
    if len(parts) != 2:
        return False
    tail = parts[1]
    return bool((cutoffs.get('filter') == latest and re.search(r'선별|필터|\d[^.!?\n]{0,16}(?:이상|초과|미만|이하)', tail)) or
                (cutoffs.get('deduplicate') == latest and re.search(r'중복|첫\s*행|마지막\s*행', tail)))
