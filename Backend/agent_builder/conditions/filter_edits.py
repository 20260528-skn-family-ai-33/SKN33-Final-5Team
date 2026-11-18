"""Closed edit intents shared by numeric and textual filter replay."""
import re
from dataclasses import dataclass

_QUOTED = re.compile(r'''"[^"]*"|(?<![A-Za-z0-9_])'[^']*'|“[^”]*”|‘[^’]*’|`[^`]*`''')
_METADATA_LABEL = re.compile(
    r'(?<![\w])(?:파일\s*+(?:이름|명)|설명|제목|출력\s*+이름|예시(?:\s*문구)?|참고\s*문구)'
    r'''(?:으로|로|은|는|을|를|에(?:는)?)?(?=[\s:"'“‘=]|$).{0,40}$''')


def in_quoted_text(text, position):
    return any(match.start() <= position < match.end() for match in _QUOTED.finditer(text))


def in_metadata_text(text, position):
    prefix = re.split(r'[.!?。！？;,，\n]|그리고|바꾸고|정하고|설정하고', text[:position])[-1]
    return bool(_METADATA_LABEL.search(prefix))

_CLEAR_DENIED = re.compile(
    r'\s*+(?:를\s*)?(?:(?:하지|하진|는)?\s*+(?:말|마|않|안)'
    r'|(?:하(?:시)?면|해서는|해선)\s*+안\s*+(?:돼|되|됩)'
    r'|할\s*+필요(?:가|는)?\s*+없'
    r'|(?:하는\s*+(?:건|것(?:은|을|도)?|걸)|하기는|는)\s*+(?:하지\s*+)?(?:말|마|않|안))')
_CLEAR_UNCERTAIN = re.compile(r'\s*+(?:할지|하는지|해도|할까|인지|여부)')


def addition_intent(text, position=None):
    """An explicit addition never means replacement; denied/uncertain edits are distinct."""
    text = _QUOTED.sub(lambda match: ' ' * len(match.group()), text)
    if position is not None:
        prefix = re.split(r'[.;!?\n]|(?<=하고)\s', text[:position])[-1]
        text = prefix + re.split(r'[.;!?\n]', text[position:], maxsplit=1)[0]
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


_INDEPENDENT_OF = ('상관없이', '관계없이', '무관하게')
_KEEP_ACTIONS = ('유지', '보존')
_EDIT_ACTION = re.compile('|'.join(('취소', '삭제', '제거', '해제', *_KEEP_ACTIONS, *_INDEPENDENT_OF)))
_FILTER_OBJECT = r'(?:선별\s*+)?(?:조건|필터)(?:을|를|은|는|의|만)?\s*+'
_FILTER_OBJECT_END = re.compile(_FILTER_OBJECT + r'$')
_EDIT_SCOPE = re.compile(r'(?P<all>모든|전체|기존)?\s*+(?P<kind>숫자|텍스트)?\s*+'
                         + _FILTER_OBJECT + r'(?P<rest>모두|전부)?\s*+')
_OTHER_EDIT_SUBJECT = re.compile(
    r'(?:(?:기존|현재|앞서\s*정한)\s*)?'
    r'(?:문서\s*(?:추출|처리)|(?:행|열|항목)\s*추출|출력(?:\s*(?:형식|포맷|열|컬럼))?'
    r'|(?:파일|저장)\s*(?:형식|포맷)|예약|실행\s*시간|중복(?:\s*(?:제거|처리))?|빈\s*값|결측값?)\s*'
    + _FILTER_OBJECT)
_SUBJECT_INTRO = re.compile(r'^\s*(?:(?:그럼|그러면|대신|이제|우선|먼저|이번에는|이어서|그리고|또한|추가로)\s+|[,，]\s*)*')
_EDIT_CONTINUATION = re.compile(r'^\s*(?:하(?:고|며|면서)|해\s*(?:줘|주세요|주고|주시고)|(?:하지|하진)\s*말고)(?=\s|[,，]|$)')
_SUBJECT_BOUNDARY = re.compile(
    r'[.!?;\n]|(?:남기|뽑|내보내|(?:필터링|선별|정렬|추출|저장|정리|제외)하)'
    r'(?:고|며|면서|지\s*+(?:는\s*+)?말고)\s+')


def filter_edit_events(text, fields, *, numeric_fields=()):
    """Parse one ordered edit transaction for both numeric and text replay.

    Subjects own their action until the next action/clause. Enumerated column
    names share that action. Unknown subjects and contradictory edits remain
    unresolved; neither consumer may silently reuse a stale predicate.
    """
    fields, numeric_fields = set(fields), set(numeric_fields)
    names = '|'.join(re.escape(field) for field in sorted(fields, key=len, reverse=True))
    name = r'(?<![\w])(?:' + names + ')' if names else r'(?!)'
    column_marker = r'\s*+(?:(?:열|컬럼)\s*+)?'
    column = name + column_marker
    separator = r'\s*+(?:[,，]|와|과|및|하고|이랑|랑|그리고)\s*+'
    particle = r'\s*+(?:은|는|을|를|만|에|와|과)?\s*+'
    # Classify the final operand before recovering all fields from the full list.
    bare_names = re.compile(column + r'(?:' + separator + column + r')*' + particle)
    listed_filters = re.compile(r'(?:' + column + r'(?:' + _FILTER_OBJECT + r')?' + separator + r')*'
                                + column + _FILTER_OBJECT)
    listed_operand = re.compile(r'(?P<field>' + name + ')' + column_marker + r'(?:' + _FILTER_OBJECT + r')?')
    events = []
    previous = 0
    column_spans = list(re.finditer(name, text))
    actions = [action for action in _EDIT_ACTION.finditer(text)
               if not any(column.start() <= action.start() < column.end() for column in column_spans)]
    for index, action in enumerate(actions):
        if in_quoted_text(text, action.start()) or in_metadata_text(text, action.start()):
            continue
        between = text[previous:action.start()]
        # A comma can enumerate columns. A completed operation opens a new subject.
        prefix = _SUBJECT_BOUNDARY.split(between)[-1]
        if previous and prefix == between:
            prefix = _EDIT_CONTINUATION.sub('', prefix)
        prefix = _SUBJECT_INTRO.sub('', prefix)
        start = action.start() - len(prefix)
        prefix = re.sub(r'^\s*(?:첨부(?:한)?|연결(?:한)?|현재)\s*(?:표|자료|파일|문서)(?:에서|의)\s*', '', prefix)
        scoped = _EDIT_SCOPE.fullmatch(prefix)
        bare = bare_names.fullmatch(prefix)
        verb = action.group()
        if (bare and verb in (*_INDEPENDENT_OF, *_KEEP_ACTIONS)) or (not bare and listed_filters.fullmatch(prefix)):
            targets = {match['field'] for match in listed_operand.finditer(prefix)}
            scope = 'named'
        elif scoped and (scoped['all'] or scoped['kind'] or scoped['rest']):
            scope = scoped['kind'] or 'all'
            targets = fields & numeric_fields if scope == '숫자' else fields - numeric_fields if scope == '텍스트' else fields
        elif not bare and _FILTER_OBJECT_END.search(prefix) and not _OTHER_EDIT_SUBJECT.fullmatch(prefix):
            targets, scope = fields, 'unresolved'
        else:
            continue  # File/column deletion is owned by another operation.
        previous = action.end()
        tail_end = actions[index + 1].start() if index + 1 < len(actions) else len(text)
        tail = re.split(r'[.!?;\n]|그리고|하고', text[action.end():tail_end])[0]
        denied = _CLEAR_DENIED.match(tail)
        uncertain = (_CLEAR_UNCERTAIN.match(tail)
                     or re.search(r'모르겠|미정|아직\s*(?:안|못)|결정하지', tail))
        kind = 'keep' if verb in _KEEP_ACTIONS else 'clear'
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
    for match in re.finditer(r'[!?。！？;\n]|(?<!\d)\.|\.(?!\d)', masked):
        yield start, text[start:match.start()]
        start = match.end()
    yield start, text[start:]


_SELECTION_VERB = r'(?:남기|남겨|고르|골라|선별하|선별해|포함하|포함해|제외하|제외해|삭제하|삭제해)'
# "데이터" and "자료" can also describe the table to sort. Require a selection action.
DATA_ROW_NOUN = r'(?:데이터|자료)(?:들)?\s*(?=만\s*(?:(?:안|못)\s*)?' + _SELECTION_VERB + r')'
# Compose grammatical negation around the same action, including auxiliaries.
# Unknown/negative selections stay unresolved; do not infer their complement.
_ACTION_NEGATIVE_END = (
    r'(?:\s*(?:주|주시|줘))?'
    r'(?:\s*(?:지|진)\s*(?:는|도)?\s*(?:말|마|않|안)'
    r'|\s*고\s*싶지\s*않|\s*(?:면|서는|선)\s*안\s*(?:돼|되)'
    r'|\s*(?:는|기)\s*(?:건|것(?:은|을|도)?|걸|를|은|는)?\s*(?:하지|하진)\s*(?:말|마|않|안))')
_SELECTION_NEGATION = re.compile(
    _SELECTION_VERB + _ACTION_NEGATIVE_END + r'|(?<![\w])(?:안|못)\s*' + _SELECTION_VERB)


def selection_action_uncertain(text):
    """Negating selection does not authorize the positive predicate or its complement."""
    clause = re.split(r'[.!?;\n]|그리고|남긴\s+(?:뒤|후)', _QUOTED.sub(' ', text), maxsplit=1)[0]
    negative = _SELECTION_NEGATION.search(clause)
    affirmative = re.search(r'남겨\s*줘(?:요)?(?![가-힣])', clause)
    return bool(negative and (not affirmative or negative.start() <= affirmative.start()))


def denied_filter_change(text):
    changes = list(re.finditer(r'바꿔|바꾸|변경|수정', text))
    return bool(changes) and all(re.match(
        r'\s*(?:을|를|은|는)?\s*(?:(?:하)?(?:지|진)\s*(?:는|도)?\s*(?:말|마|않|안)|(?:하)?면\s*안|(?:할\s*)?필요(?:가|는)?\s*없)',
        text[match.end():]) for match in changes)
