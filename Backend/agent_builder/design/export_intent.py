"""Explicit save cancellation/addition, scoped to actual user request turns."""
import re


_FORMAT = r'(?:csv|json|xlsx|pdf|md)'
_FORMATS = _FORMAT + r'(?:\s*(?:와|과|및|,|/|\+)\s*' + _FORMAT + r')*'
_END = r'(?:해\s*(?:주세요|줘)|하(?:고|며|세요|도록)|해|합니다)?(?=[.!?,;\s]|$)'
_SAVE = re.compile(
    r'(?:(?P<replacement>' + _FORMATS + r')\s*말고\s*)?'
    r'(?P<formats>' + _FORMATS + r')\s*(?:파일)?(?:으로|로)(?P<also>도)?\s*'
    r'(?:저장|내보내)' + _END, re.I)
_CANCEL = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:만)?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내기)(?:만|은|는|을|를)?\s*(?:취소|생략|중단)' + _END, re.I)
_DONT_SAVE = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내)(?:하)?지\s*(?:마|말아)(?:\s*줘)?(?=[.!?,;\s]|$)', re.I)
_CANCEL_FORMAT = re.compile(
    r'(?P<formats>' + _FORMATS + r')\s*(?:만|은|는|을|를)?\s*'
    r'(?:취소|생략|중단)' + _END, re.I)
_CHANGE = re.compile(
    r'(?P<formats>' + _FORMATS + r')\s*(?:형식)?(?:으로|로)\s*'
    r'(?:변경해|바꿔)(?:\s*(?:주세요|줘))?(?=[.!?,;\s]|$)', re.I)
_SINGLE_SAVE = re.compile(
    r'(?P<formats>' + _FORMAT + r')\s*(?:파일\s*)?(?:하나|한\s*개)만\s*'
    r'(?:저장|내보내)' + _END, re.I)
_CONTEXT = re.compile(r'\s*(?:라는|라고|란|라는\s*문구|이런|같은\s*예)')
_CLAUSE_BREAK = re.compile(r'(?<=[.!?;])\s+|\n+')
_NEGATIVE_DESIRE = re.compile(r'\s*싶지\s*않(?:아(?:요)?|습니다|다)\s*(?=[.!?,;]|$)')
_OUTPUT_PREFIX = re.compile(
    r'(?:(?:이제|그럼|그리고|또한)\s+)?'
    r'(?:(?:같은|동일한)\s*(?:열|결과)(?:을|를)\s*|'
    r'(?:기존\s*)?(?:결과(?:물|\s*파일)?|출력)(?:은|을|를)\s*|'
    r'(?:(?:파일|저장|출력|결과\s*파일)\s*)?형식(?:은|을|를)?\s*)?'
    r'(?:' + _FORMATS + r'(?:(?:으로|로)?\s*저장하지\s*말고|\s*(?:에서|말고|대신))\s*)?', re.I)
_DELIVERY = r'(?:저장|내보내|다운로드|만들|생성|작성)'
_FORMAT_DESTINATION = r'(?:(?:' + _FORMATS + r')\s*)?(?:파일\s*)?(?:(?:으로|로)(?:도)?\s*)?'
_SEPARATE = re.compile(
    r'(?:(?:별도|별개의)\s*(?:' + _FORMAT + r'\s*)?(?:파일|결과물|출력)(?:들)?(?:으로|로|을|를|도)?\s*'
    r'|(?:각각|각자|따로|별도(?:로)?)\s*' + _FORMAT_DESTINATION
    + r'|(?:두\s*파일|파일\s*두\s*개)(?:로|를|씩)?\s*(?:각각\s*)?)'
    + _DELIVERY, re.I)
_QUANTIFIED_DELIVERY = re.compile(
    r'(?:모두|둘\s*다)\s*' + _FORMAT_DESTINATION + _DELIVERY, re.I)
_DATA_UNIT = re.compile(
    r'(?<!\w)(행|열|레코드|주문|데이터|값|항목|건)(?:들)?(?:만|은|는|을|를|도)?'
    r'\s*(?:(?:그대로|빠짐없이)\s*)?$')
_TWO_FORMATS = re.compile(_FORMAT + r'\s*(?:와|과|및|,|/|와 함께|이?랑)\s*' + _FORMAT, re.I)


def request_clauses(text):
    """Yield sentence offsets without treating a filename extension as a sentence break."""
    start = 0
    for separator in _CLAUSE_BREAK.finditer(text):
        yield start, text[start:separator.start()]
        start = separator.end()
    yield start, text[start:]


def _separate_artifacts(text):
    if _SEPARATE.search(text):
        return True
    for match in _QUANTIFIED_DELIVERY.finditer(text):
        subject = text[:match.start()]
        unit = _DATA_UNIT.search(subject)
        if unit and not re.search(
                rf'{unit[1]}(?:들)?\s*(?:과|와|및|,)\s*[^.!?;\n]*$', subject[:unit.start()]):
            continue
        return True
    return False


def _multiple_outputs(state, turns, active_from, reset_from, reset_offset):
    from agent_builder.conditions.filter_dialogue import control_reply
    from agent_builder.conditions.filter_edits import user_filter_text

    positions = [(i, message) for i, message in enumerate(state.get('messages', []))
                 if message['role'] == 'user']
    for source, text in turns:
        if source < reset_from:
            continue
        position, message = positions[source]
        if source == reset_from:
            text = text[reset_offset:]
        else:
            reply = user_filter_text(state, position, message)
            if reply and reply != message['content']:
                text = reply
        if control_reply(text):
            continue
        for _, clause in request_clauses(text):
            # A format answer may fill in a previously requested separate artifact.
            # A plain list of formats, in contrast, is superseded by the newer answer.
            if _separate_artifacts(clause) or (source >= active_from and _TWO_FORMATS.search(clause)):
                return True
    return False


def explicit_export_intent(state):
    from .output_selection_request import _request_turns

    turns = list(_request_turns(state))
    formats = set()
    forbidden_formats = set()
    cancelled = False
    active_from = -1
    reset_from = -1
    reset_offset = 0
    accepted_clauses = set()
    projects_writer = False
    for source, text in turns:
        started = False
        for offset, clause in request_clauses(text):
            events = sorted(
                [(m.start(), 'save', m) for pattern in (_SAVE, _CHANGE, _SINGLE_SAVE) for m in pattern.finditer(clause)] +
                [(m.start(), 'cancel', m) for pattern in (_CANCEL, _DONT_SAVE, _CANCEL_FORMAT)
                 for m in pattern.finditer(clause)],
                key=lambda event: event[0])
            for _, action, match in events:
                tail = clause[match.end():]
                if _CONTEXT.match(tail):
                    continue
                negative = _NEGATIVE_DESIRE.match(tail)
                end = match.end() + (negative.end() if negative else 0)
                prefix = clause[:match.start()].strip()
                output_scope = bool(_OUTPUT_PREFIX.fullmatch(prefix))
                whole_clause = bool(re.fullmatch(r'\s*[.!]?\s*', clause[end:]))
                if match.re is _CHANGE and not output_scope:
                    continue
                if output_scope and whole_clause:
                    accepted_clauses.add((source, clause.strip()))
                selected = {fmt.lower() for fmt in re.findall(_FORMAT, match['formats'] or '', re.I)}
                if negative:
                    if action == 'cancel':
                        continue
                    action = 'cancel'
                if action == 'cancel':
                    if selected:
                        removed = bool(formats & selected)
                        forbidden_formats.update(selected)
                        formats.difference_update(selected)
                        if removed:
                            reset_from, reset_offset = source, offset + end
                            cancelled = not formats
                            active_from = source
                        continue
                    # A global cancellation must be a clause of its own.
                    prefix = re.split(r'[;,]', prefix)[-1].strip()
                    if prefix and not re.fullmatch(r'(?:이제|그럼|그리고|또한)', prefix):
                        continue
                    forbidden_formats.update(formats)
                    reset_from, reset_offset = source, offset + end
                    formats.clear()
                    cancelled = True
                    active_from = source
                    started = False
                else:
                    replacement = match.groupdict().get('replacement')
                    also = match.groupdict().get('also')
                    if replacement or match.re in (_CHANGE, _SINGLE_SAVE):
                        reset_from, reset_offset = source, offset + end
                    if replacement or (not also and not started) or match.re in (_CHANGE, _SINGLE_SAVE):
                        formats.clear()
                        active_from = source
                    formats.update(selected)
                    forbidden_formats.difference_update(selected)
                    cancelled = False
                    projects_writer = match.re is not _CHANGE
                    started = True
    additional = len(formats) > 1
    return dict(formats=formats, cancelled=cancelled,
                additional=additional, active_from=active_from, forbidden_formats=forbidden_formats,
                accepted_clauses=frozenset(accepted_clauses), projects_writer=projects_writer,
                multiple=not cancelled and (additional or _multiple_outputs(
                    state, turns, active_from, reset_from, reset_offset)))
