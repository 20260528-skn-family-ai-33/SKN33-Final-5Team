"""Closed edit intents shared by numeric and textual filter replay."""
import re
from dataclasses import dataclass

_QUOTED = re.compile(r'''"[^"\n]*"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’''')
_METADATA_LABEL = re.compile(
    r'(?<![\w])(?:파일\s*+(?:이름|명)|설명|제목|출력\s*+이름)'
    r'''(?:으로|로|은|는|을|를|에(?:는)?)?(?=[\s:"'“‘=]|$).{0,40}$''')


def in_quoted_text(text, position):
    return any(match.start() <= position < match.end() for match in _QUOTED.finditer(text))


def in_metadata_text(text, position):
    prefix = re.split(r'[.!?;,\n]|그리고|바꾸고|정하고|설정하고', text[:position])[-1]
    return bool(_METADATA_LABEL.search(prefix))

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


@dataclass(frozen=True)
class FilterEdit:
    start: int
    end: int
    action: str
    fields: frozenset[str]
    scope: str


_EDIT_ACTION = re.compile(r'취소|삭제|제거|해제|유지|보존|상관없이|관계없이|무관하게')
_EDIT_SCOPE = re.compile(r'(?:(?:모든|전체|기존)\s*)?(?P<kind>숫자|텍스트)?\s*(?:선별\s*)?(?:조건|필터)(?:을|를|은|는)?\s*(?:모두|전부)?\s*$')


def filter_edit_events(text, fields, *, numeric_fields=()):
    """Parse one ordered edit transaction for both numeric and text replay.

    Subjects own their action until the next action/clause. Enumerated column
    names share that action. Unknown subjects and contradictory edits remain
    unresolved; neither consumer may silently reuse a stale predicate.
    """
    fields, numeric_fields = set(fields), set(numeric_fields)
    names = '|'.join(re.escape(field) for field in sorted(fields, key=len, reverse=True))
    name = r'(?<![\w])(?:' + names + ')' if names else r'(?!)'
    atom = name + r'\s*(?:(?:선별\s*)?(?:조건|필터))?'
    listed = re.compile(atom + r'(?:\s*(?:,|와|과|및|하고|이랑|랑|그리고)\s*' + atom + r')*'
                        + r'\s*(?:은|는|을|를|만|에|와|과)?\s*$')
    events = []
    previous = 0
    actions = list(_EDIT_ACTION.finditer(text))
    for index, action in enumerate(actions):
        if in_quoted_text(text, action.start()) or in_metadata_text(text, action.start()):
            continue
        # A comma can enumerate columns; sentence punctuation ends the subject.
        prefix = re.split(r'[.!?;\n]', text[previous:action.start()])[-1]
        start = action.start() - len(prefix)
        previous = action.end()
        named = listed.search(prefix)
        scoped = _EDIT_SCOPE.search(prefix)
        if named:
            targets = {match.group() for match in re.finditer(name, named.group())}
            scope = 'named'
            start += named.start()
        elif scoped and (scoped['kind'] or re.search(r'모든|전체|기존|모두|전부', scoped.group())):
            scope = scoped['kind'] or 'all'
            targets = fields & numeric_fields if scope == '숫자' else fields - numeric_fields if scope == '텍스트' else fields
            start += scoped.start()
        elif re.search(r'조건|필터|선별', prefix):
            targets, scope = fields, 'unresolved'
        else:
            continue  # File/column deletion is owned by another operation.
        tail_end = actions[index + 1].start() if index + 1 < len(actions) else len(text)
        tail = re.split(r'[.!?;\n]|그리고|하고', text[action.end():tail_end])[0]
        denied = _CLEAR_DENIED.match(tail)
        uncertain = (_CLEAR_UNCERTAIN.match(tail)
                     or re.search(r'모르겠|미정|아직\s*(?:안|못)|결정하지', tail))
        kind = 'keep' if action.group() in ('유지', '보존') else 'clear'
        if scope == 'unresolved' or uncertain or denied and kind == 'keep':
            kind = 'uncertain'
        elif denied:
            kind = 'keep'
        events.append(FilterEdit(start, action.end() + (denied.end() if denied else 0), kind, frozenset(targets), scope))
    # An explicit subject with conflicting edits needs a decision, not last-match wins.
    conflicts = {field for field in fields if len({event.action for event in events
                                                  if field in event.fields and event.action != 'uncertain'}) > 1}
    result = []
    for event in events:
        agreed, ambiguous = event.fields - conflicts, event.fields & conflicts
        if agreed or not event.fields:
            result.append(FilterEdit(event.start, event.end, event.action, agreed, event.scope))
        if ambiguous:
            result.append(FilterEdit(event.start, event.end, 'uncertain', ambiguous, event.scope))
    if any(event.action == 'uncertain' for event in result):
        # Keep a mixed edit provisional as a whole: a later KEEP_ALL must not
        # inherit an already-applied cancellation from the same unresolved turn.
        result = [FilterEdit(event.start, event.end,
                             'uncertain' if event.action == 'clear' else event.action,
                             event.fields, event.scope) for event in result]
    return result


def filter_clear_events(text, fields, *, intent='clear', numeric_fields=None):
    return [(event.start, event.end, field) for event in filter_edit_events(text, fields, numeric_fields=fields if numeric_fields is None else numeric_fields)
            if event.action == intent for field in sorted(event.fields)]


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
