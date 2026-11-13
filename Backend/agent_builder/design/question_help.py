"""Explain a pending control without turning a question into a business answer."""
import re

from .question_controls import project_question_controls

_HELP = re.compile(
    r'\s*(?P<subject>기준\s*값|비교\s*방식|추출\s*항목|빈\s*값|중복|이\s*질문|질문|이게|그게)?'
    r'\s*(?:이|가|은|는|을|를)?\s*'
    r'(?:뭐야|뭔가요|뭐예요|무엇인가요|무슨\s*뜻(?:이야|인가요|이에요)?|'
    r'설명해\s*(?:줘|주세요)|어떻게\s*답(?:변)?(?:하면|해야)\s*(?:돼|되나요|하나요))'
    r'[?？.!\s]*', re.I)

_KEYS = {'기준값': ('filter.value',), '비교방식': ('filter.operator',),
         '빈값': ('missing.policy',), '중복': ('duplicate.field', 'duplicate.policy')}
_EXPLANATIONS = {
    'filter.value': '기준값은 어떤 행을 남길지 비교할 값입니다. 기준 열에 적용할 값을 직접 정해 입력해주세요.',
    'filter.operator': '이상·이하는 기준값을 포함하고, 초과·미만은 기준값을 포함하지 않습니다.',
    'missing.policy': '빈값 처리는 조건에 필요한 값이 없는 행을 유지할지, 제외할지, 오류로 알릴지 정하는 설정입니다.',
    'duplicate.field': '같은 값이면 중복으로 판단할 열을 선택해주세요.',
    'duplicate.policy': '중복된 행을 모두 유지할지, 첫 행 또는 마지막 행만 남길지, 오류로 알릴지 정하는 설정입니다.',
}


def question_help(state, content):
    match = _HELP.fullmatch(content)
    if not match:
        return None
    projected = project_question_controls(state)
    questions = projected['pending_questions']
    if not questions:
        draft = projected['draft'] or {}
        options = draft.get('question_options') or []
        questions = [{'text': text, 'key': '', 'options': options[i] if i < len(options) else []}
                     for i, text in enumerate(draft.get('questions', []))]
    if not questions:
        return None
    subject = re.sub(r'\s', '', match['subject'] or '')
    keys = _KEYS.get(subject)
    selected = [q for q in questions if q.get('key') in keys] if keys else questions
    if not selected:
        selected = [q for q in questions if not q.get('key') and subject in re.sub(r'\s', '', q['text'])]
        if not selected:
            return '어떤 질문의 설명이 필요한지 알려주세요.\n' + '\n'.join(q['text'] for q in questions) + '\n현재 질문과 기존 조건은 그대로 유지했습니다.'
    paragraphs = []
    for question in selected:
        details = _EXPLANATIONS.get(question.get('key') or (keys[0] if keys and subject in re.sub(r'\s','',question['text']) else '')) or question.get('reason') or '이 항목의 원하는 조건을 입력해주세요.'
        if question.get('options'):
            details += ' 선택지: ' + ', '.join(question['options']) + '.'
        paragraphs.append(question['text'] + '\n' + details)
    return '\n\n'.join(paragraphs) + '\n\n현재 질문과 기존 조건은 그대로 유지했습니다. 설명을 확인한 뒤 답변해주세요.'
