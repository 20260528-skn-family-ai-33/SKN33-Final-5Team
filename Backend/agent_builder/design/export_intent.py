"""Explicit save cancellation/addition, scoped to actual user request turns."""
import re


_FORMAT = r'(?:csv|json|xlsx|pdf|md)'
_FORMATS = _FORMAT + r'(?:\s*(?:와|과|및|,|/)\s*' + _FORMAT + r')*'
_END = r'(?:해\s*(?:주세요|줘)|하(?:고|며|도록)|해|합니다)?(?=[.!?,;\s]|$)'
_SAVE = re.compile(
    r'(?:(?P<replacement>' + _FORMATS + r')\s*말고\s*)?'
    r'(?P<formats>' + _FORMATS + r')\s*(?:파일)?(?:으로|로)(?P<also>도)?\s*'
    r'(?:저장|내보내)' + _END, re.I)
_CANCEL = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내기)(?:은|는|을|를)?\s*(?:취소|생략|중단)' + _END, re.I)
_DONT_SAVE = re.compile(
    r'(?P<formats>' + _FORMATS + r')?\s*(?:(?:파일|결과물?)(?:을|를)?\s*)?'
    r'(?:으로|로)?\s*(?:저장|내보내)(?:하)?지\s*(?:마|말아)(?:\s*줘)?(?=[.!?,;\s]|$)', re.I)
_CONTEXT = re.compile(r'\s*(?:라는|라고|란|라는\s*문구|이런|같은\s*예)')


def explicit_export_intent(state):
    from .output_selection_request import _request_turns

    formats = set()
    cancelled = False
    additional = False
    active_from = -1
    for source, text in _request_turns(state):
        events = sorted(
            [(m.start(), 'save', m) for m in _SAVE.finditer(text)] +
            [(m.start(), 'cancel', m) for pattern in (_CANCEL, _DONT_SAVE)
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
                    formats.difference_update(selected)
                    additional = len(formats) > 1
                    continue
                # A global cancellation has its own clause. A preceding format,
                # filename or qualifier cannot silently cancel every artifact.
                prefix = re.split(r'[.!?;,\n]', text[:match.start()])[-1].strip()
                if prefix and not re.fullmatch(r'(?:이제|그럼|그리고|또한)', prefix):
                    continue
                formats.clear()
                cancelled = True
                additional = False
                active_from = source
                started = False
            else:
                if match['replacement'] or (not match['also'] and not started):
                    formats.clear()
                    additional = False
                    active_from = source
                formats.update(selected)
                additional = additional or bool(match['also']) or len(selected) > 1
                cancelled = False
                started = True
    return dict(formats=formats, cancelled=cancelled,
                additional=additional and len(formats) > 1, active_from=active_from)
