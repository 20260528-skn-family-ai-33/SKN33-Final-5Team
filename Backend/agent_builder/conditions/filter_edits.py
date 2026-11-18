"""Closed edit intents shared by numeric and textual filter replay."""
import re

_QUOTED = re.compile(r'''"[^"\n]*"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’''')
_METADATA_LABEL = re.compile(
    r'(?<![\w])(?:파일\s*+(?:이름|명)|설명|제목|출력\s*+이름)'
    r'''(?:으로|로|은|는|을|를|에(?:는)?)?(?=[\s:"'“‘=]|$).{0,40}$''')


def in_quoted_text(text, position):
    return any(match.start() <= position < match.end() for match in _QUOTED.finditer(text))


def in_metadata_text(text, position):
    prefix = re.split(r'[.!?;,\n]|그리고|바꾸고|정하고|설정하고', text[:position])[-1]
    return bool(_METADATA_LABEL.search(prefix))

ALL_FILTERS_CANCELLED = re.compile(
    r'(?:모든|전체|기존)\s*+(?P<numeric>숫자\s*+)?(?:선별\s*+)?(?:조건|필터)(?:을|를|은|는)?\s*+'
    r'(?:취소|삭제|제거|해제)(?:해\s*+(?:줘|주세요))?[.!?\s]*')
_CLEAR_DENIED = re.compile(
    r'\s*+(?:(?:하지|하진|는)?\s*+(?:말|마|않|안)'
    r'|(?:하(?:시)?면|해서는|해선)\s*+안\s*+(?:돼|되|됩)'
    r'|할\s*+필요(?:가|는)?\s*+없)')
_CLEAR_UNCERTAIN = re.compile(r'\s*+(?:할지|하는지|해도|할까|인지|여부)')


def addition_intent(text):
    """An explicit addition never means replacement; denied/uncertain edits are distinct."""
    text = _QUOTED.sub(' ', text)
    edits = [match for match in re.finditer('추가', text) if not in_metadata_text(text, match.start())]
    tail = text[edits[-1].start():] if edits else ''
    if re.search(r'대신|교체|새\s*+조건만', tail):
        return None
    if re.search(r'추가\s*+(?:하지|하진|하시는|는)?\s*+(?:말|마|않|안)', tail):
        return 'denied'
    if re.search(r'추가\s*+(?:할지|하는지|인지)', tail):
        return 'uncertain'
    if edits or re.match(r'\s*+(?:그리고|또한|여기에)\s+', text):
        return 'add'
    return None


def filter_clear_events(text, fields, *, intent='clear'):
    """Locate explicit, uncertain or denied edits that would clear a field's filter."""
    events = []
    for field in fields:
        name = r'(?<![\w])' + re.escape(field)
        patterns = [name + r'\s*+(?:와|과|에)?\s*+(?:상관없이|관계없이|무관하게)',
                    name + r'\s*+(?:선별\s*+)?(?:조건|필터)(?:은|는|을|를)?\s*+(?:취소|삭제|제거|해제)']
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                if in_quoted_text(text, match.start()) or in_metadata_text(text, match.start()):
                    continue
                tail = text[match.end():]
                denied = _CLEAR_DENIED.match(tail)
                if denied:
                    if intent == 'keep':
                        events.append((match.start(), match.end() + denied.end(), field))
                    continue
                unresolved = (_CLEAR_UNCERTAIN.match(tail)
                              or re.search(r'모르겠|미정|아직\s*+(?:안|못)|결정하지',
                                           re.split(r'[.!?;\n]|그리고', tail)[0]))
                if intent == ('uncertain' if unresolved else 'clear'):
                    events.append((match.start(), match.end(), field))
    return sorted(events)


def cleared_filter_fields(text, fields):
    return {field for _, _, field in filter_clear_events(text, fields)}


def user_filter_text(state, index, message):
    """Read answer values, never echoed question labels, as new filter instructions."""
    event = next((item for item in reversed(state.get('condition_answers', [])) if item.get('turn') == index), None)
    if event and event.get('answers'):
        for key in ('filter.match', 'filter.value'):
            answers = [item.get('answer', '') for item in event['answers'] if item.get('key') == key]
            if answers:
                return answers[0]
        return None
    content = message['content']
    return content.rsplit('→', 1)[1] if event and '→' in content else content


def filter_clauses(text):
    masked = _QUOTED.sub(lambda match: ' ' * len(match.group()), text)
    start = 0
    for match in re.finditer(r'(?<!\d)[.!?;\n]|[.!?;\n](?!\d)', masked):
        yield start, text[start:match.start()]
        start = match.end()
    yield start, text[start:]
