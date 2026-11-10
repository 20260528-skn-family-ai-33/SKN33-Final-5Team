"""Expose server connection metadata without granting runtime credentials."""
import re


def service_connection_context(state):
    connected = state.get('gmail_connected')
    if type(connected) is not bool:
        connected = None
    return {'gmail': {
        'connected': connected,
        'status': 'connected' if connected else 'not_connected' if connected is False else 'unknown',
        'instruction': ('서버에 Gmail 읽기 계정 연결 정보가 있습니다. 다시 연결하라는 질문을 하지 않습니다. '
                        if connected else
                        'Gmail 계정은 실행 전에 연결합니다. 연결되지 않아도 지원되는 설계 노드는 생성합니다. '
                        if connected is False else
                        '연결 상태를 전달받지 않았으므로 미연결이라고 추측하거나 단정하지 않습니다. ') +
                       '이 값은 연결 정보의 존재 여부입니다. 토큰 유효성·실제 읽기 권한은 실행 직전에 서버가 확인합니다.'}}


_GMAIL_CONNECT_QUESTION = re.compile(
    r'(?:gmail|지메일)(?:의)?(?:계정)?(?:을|를)?(?:먼저)?'
    r'연결(?:해(?:줘|주세요|주시겠어요|주실래요)|하(?:세요|시겠어요|실래요))', re.I)


def reconcile_connection_questions(draft, state):
    """Remove only a contradicted connection request; preserve business questions.

    An empty graph after removing the sole question still goes through the
    existing bounded planner retry. This never fabricates an executable draft.
    """
    if state.get('gmail_connected') is not True:
        return draft
    questions = draft.get('questions') or []
    keep = [index for index, question in enumerate(questions)
            if not _GMAIL_CONNECT_QUESTION.fullmatch(re.sub(r'[\s.!?。？！]', '', question))]
    if len(keep) == len(questions):
        return draft
    options = draft.get('question_options') or []
    return {**draft, 'questions': [questions[index] for index in keep],
            'question_options': [options[index] if index < len(options) else [] for index in keep]}
