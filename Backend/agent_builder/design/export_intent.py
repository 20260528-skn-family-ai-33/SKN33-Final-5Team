"""Explicit save cancellation/addition, scoped to actual user request turns."""
import re


_FORMAT = r'(?:csv|json|xlsx|pdf|md)'
_FORMATS = _FORMAT + r'(?:\s*(?:와|과|및|,|/|\+)\s*' + _FORMAT + r')*'
_END = r'(?:해\s*(?:주세요|줘)|하(?:고|며|도록)|해|합니다)?(?=[.!?,;\s]|$)'
_SAVE = re.compile(
    r'(?:(?P<replacement>' + _FORMATS + r')\s*말고\s*)?'
    r'(?P<formats>' + _FORMATS + r')\s*(?:파일)?(?:으로|로)(?P<also>도)?\s*'
    r'(?:저장|내보내)' + _END, re.I)
_CANCEL = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:만)?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내기)(?:은|는|을|를)?\s*(?:취소|생략|중단)' + _END, re.I)
_DONT_SAVE = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내)(?:하)?지\s*(?:마|말아)(?:\s*줘)?(?=[.!?,;\s]|$)', re.I)
_CANCEL_FORMAT = re.compile(
    r'(?P<formats>' + _FORMATS + r')\s*(?:만|은|는|을|를)?\s*'
    r'(?:취소|생략|중단)' + _END, re.I)
_CHANGE = re.compile(
    r'(?P<formats>' + _FORMATS + r')\s*(?:형식)?(?:으로|로)\s*'
    r'(?:변경해|바꿔)(?:\s*(?:주세요|줘))?(?=[.!?,;\s]|$)', re.I)
_CONTEXT = re.compile(r'\s*(?:라는|라고|란|라는\s*문구|이런|같은\s*예)')


def explicit_export_intent(state):
    from .output_selection_request import _request_turns

    formats = set()
    forbidden_formats = set()
    cancelled = False
    additional = False
    active_from = -1
    for source, text in _request_turns(state):
        events = sorted(
            [(m.start(), 'save', m) for pattern in (_SAVE, _CHANGE) for m in pattern.finditer(text)] +
            [(m.start(), 'cancel', m) for pattern in (_CANCEL, _DONT_SAVE, _CANCEL_FORMAT)
             for m in pattern.finditer(text)],
            key=lambda event: event[0])
        started = False
        for _, action, match in events:
            if _CONTEXT.match(text[match.end():]):
                continue
            selected = set(re.findall(_FORMAT, match['formats'] or '', re.I))
            selected = {fmt.lower() for fmt in selected}
            if action == 'cancel':
                if selected:
                    removed = bool(formats & selected)
                    forbidden_formats.update(selected)
                    formats.difference_update(selected)
                    if removed:
                        cancelled = not formats
                        active_from = source
                    additional = len(formats) > 1
                    continue
                # A global cancellation has its own clause. A preceding format,
                # filename or qualifier cannot silently cancel every artifact.
                prefix = re.split(r'[.!?;,\n]', text[:match.start()])[-1].strip()
                if prefix and not re.fullmatch(r'(?:이제|그럼|그리고|또한)', prefix):
                    continue
                forbidden_formats.update(formats)
                formats.clear()
                cancelled = True
                additional = False
                active_from = source
                started = False
            else:
                replacement = match.groupdict().get('replacement')
                also = match.groupdict().get('also')
                if replacement or (not also and not started) or match.re is _CHANGE:
                    formats.clear()
                    additional = False
                    active_from = source
                formats.update(selected)
                forbidden_formats.difference_update(selected)
                additional = additional or bool(also) or len(selected) > 1
                cancelled = False
                started = True
    return dict(formats=formats, cancelled=cancelled,
                additional=additional and len(formats) > 1, active_from=active_from,
                forbidden_formats=forbidden_formats)
