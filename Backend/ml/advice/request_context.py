"""Bound the frozen single-request models to inputs they were trained to see.

Do not flatten a conversation into a new, apparently single-turn request. A planner
can resolve revisions and clarification answers; this classifier cannot.
"""
import re
from documents.resources.document_resources import supports

_QUOTED = re.compile(r'''`[^`]*`|"[^"\n]*"|(?<![\w])'[^'\n]*'(?![\w])|“[^”]*”|‘[^’]*’''')
_ACTION = (r'(?:저장|보관|파일\s*생성|전송|발송|보내|내보내|생성|만들|작성|실행|'
           r'요약|검색|추출|집계|검수|검토|분석|필터|정렬|삭제|작업)')
_PARTICLE = r'(?:은|는|을|를|이|가|도|만|하|해|할|하기)?\s*'
_NEGATIVE_ACTION = re.compile(
    _ACTION + _PARTICLE +
    r'(?:하지\s*마|지\s*마|안\s*(?:해|할|하|보내|저장)|'
    r'지\s*않고(?!\s*있(?:는지|었는지))|없이|필요\s*없|취소|중단)', re.IGNORECASE)
_DECLINED_ACTION = re.compile(
    _ACTION + r'(?:하|할|하는)?\s*(?:생각|계획|의향)(?:이|은|는|도)?\s*없')
_CANCEL_COMMAND = re.compile(r'(?:^|\s)(?:취소|중단|그만)(?:해|하자|할게|할래|해주세요|$)')
_ALTERNATIVE = re.compile(r'말고|대신|\b(?:cancel|instead)\b', re.IGNORECASE)
_ENGLISH_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|never|not)\s+(?:\w+\s+){0,2}(?:save|store|export|send|create|run|summarize)\b|"
    r'\bwithout\s+(?:\w+\s+){0,2}(?:saving|storing|exporting|sending|creating|running)\b', re.IGNORECASE)


def instruction_text(text):
    """Literal strings are task data, not instructions to cancel that task."""
    text = text.strip()
    # A whole quoted utterance is still a command; embedded literals stay data.
    if _QUOTED.fullmatch(text):
        return text[1:-1]
    return _QUOTED.sub(' ', text)


def instruction_negation(text):
    """Narrow action-scoped handoff, not general sentiment/negation detection.

    '저장 없이 요약' constrains an action; '승인 없이 공개했는지 검수'
    describes the material being reviewed and must not cancel that review.
    """
    unquoted = instruction_text(text)
    # A whole utterance quoted for emphasis can still be an instruction. An
    # apostrophe inside don't/it's is not a pair of literal delimiters either.
    text = unquoted if unquoted.strip() else text.strip(' `"\'“”‘’')
    return any(pattern.search(text) for pattern in (
        _NEGATIVE_ACTION, _DECLINED_ACTION, _CANCEL_COMMAND, _ALTERNATIVE, _ENGLISH_NEGATION))


def request_context(state):
    messages = state.get('messages', [])
    if not isinstance(messages, list):
        raise ValueError('messages must be a list')
    users = [m.get('content') for m in messages if isinstance(m, dict) and m.get('role') == 'user']
    if any(not isinstance(q, str) for q in users):
        raise ValueError('user content must be text')
    users = [q.strip() for q in users if q.strip()]
    resources = state.get('resources') or []
    if not isinstance(resources, list) or any(not isinstance(r, dict) for r in resources):
        raise ValueError('resources must be a list of objects')
    # Routed CSV/XLSX can expose both rows and validated document text. Use the
    # same capability predicate as execution, not just the original asset kind.
    kinds = sorted({kind for r in resources for kind in ('table', 'document') if supports(r, kind)})
    context = {'resource_kinds': kinds,
               'resource_formats': sorted({r['format'] for r in resources if isinstance(r.get('format'), str)}),
               'columns': list(dict.fromkeys(c for r in resources if r.get('kind') == 'table'
                                            for c in r.get('columns', []) if isinstance(c, str))),
               'user_turns': len(users)}
    if not users:
        return '', context, 'empty_request'
    # Includes short clarification answers: neither the latest answer alone nor
    # concatenation is a validated representation of the current business intent.
    if len(users) > 1:
        return '', context, 'conversation_requires_planner'
    if instruction_negation(users[0]):
        return '', context, 'negation_requires_planner'
    return users[0], context, None


def abstain(reason, context):
    return {'ml_candidates': [], 'ml_status': reason, 'ml_context': context,
            'advisory_only': True,
            'ml_usage': '추천을 보류합니다. 대화의 현재 요청·부정 조건·자료를 전체 도구 명세와 함께 판단하세요. '
                        '빈 후보는 미지원 판정이나 실행할 도구가 없다는 뜻이 아닙니다.'}
