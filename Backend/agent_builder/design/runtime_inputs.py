"""Carry an explicit deferred file choice into planning without inventing an asset."""
import re

_FILE = re.compile(r'파일|자료|문서|계약서|영수증|첨부|PDF|CSV|엑셀', re.I)
_DEFER = re.compile(r'(?:실행\s*(?:할\s*때|시)|나중에).{0,45}(?:넣을|넣겠|올릴|올리겠|첨부할|첨부하겠|업로드할|업로드하겠|입력할|제공할|받을|받도록|입력받)')
_NOW = re.compile(r'(?:지금|먼저|설계\s*(?:할\s*때|시)).{0,30}(?:넣을|올릴|첨부할|업로드할|제공할)')
_NEGATIVE = re.compile(r'(?:넣|올리|첨부하|업로드하|제공하|입력하)지\s*않|(?:안|못)\s*(?:넣|올|첨부|업로드|제공|입력)')
_FILE_TARGET = r'(?:파일|자료|문서|계약서|영수증|첨부|PDF|CSV|엑셀)(?:\s*(?:파일|문서|PDF))?(?:\s*(?:의\s*)?(?:자체|이름|명|경로|ID|아이디))?'
# Require a complete verbal question: "파일 처리 규칙" is a noun phrase
# about business criteria, while "파일을 처리할까요?" asks for the file.
_FILE_QUESTION_PREDICATE = (
    r'(?:(?:하(?:시)?|되)(?:나요|죠|겠(?:습니까|어요))|하십니까|해요|'
    r'(?:할|하실)(?:까요|래요)|될까요)')
_FILE_ACTION_QUESTION = (
    r'(?:사용|처리|선택|연결)(?:' + _FILE_QUESTION_PREDICATE +
    r'|하(?:시)?면\s*' + _FILE_QUESTION_PREDICATE +
    r'|해야\s*' + _FILE_QUESTION_PREDICATE +
    r'|(?:할|하실)\s*(?:건가요|것인가요|예정인가요|계획인가요)'
    r'|할지\s*알려\s*주세요)(?=\s*(?:[?？.!]|$))')
# Match what the question asks for, not two unrelated words anywhere in it.
# "CSV 파일에서 집계할 열을 선택" asks for a column, not for the file.
_UPLOAD_QUESTION = re.compile(
    _FILE_TARGET + r'(?:을|를|은|는|이|가|도)?\s*(?:(?:다시|먼저|지금)\s*)?'
    r'(?:(?:올려|첨부해|업로드해|제공해|연결해|선택해|알려).{0,12}(?:주세요|주실|주시|줄)|무엇|어디|있나요|있습니까)'
    r'|(?:어떤|어느)\s*' + _FILE_TARGET + r'(?:을|를)?\s*' + _FILE_ACTION_QUESTION, re.I)


def runtime_file_context(state):
    """Use user messages only; a document or assistant suggestion is not consent."""
    selected = None
    file_request = False
    for message in state.get('messages', []):
        if message.get('role') != 'user':
            continue
        text = message.get('content', '')
        file_request = file_request or bool(_FILE.search(text))
        if not file_request:
            continue
        if _NOW.search(text) or _NEGATIVE.search(text):
            selected = None
        elif _DEFER.search(text):
            selected = text
    if selected is None or state.get('resources'):
        return None
    return {
        'source': 'user', 'quote': selected, 'file_delivery': 'at_execution',
        'instruction': ('사용자가 실행할 때 파일을 넣겠다고 답했습니다. 파일 자체·이름·경로·ID를 설계 단계에서 다시 요구하지 마세요. '
                        'read_table/read_document의 기본 동작(operation=null, file_ids=[])으로 실행 입력을 읽고 뒤 단계에 연결하세요. '
                        'asset 인자가 필요한 파일 확장 operation이나 가짜 자료 ID를 쓰지 마세요. '
                        '미정인 업무 기준·열 이름·검토 기준은 구체적으로 질문할 수 있습니다. '
                        '사람 확인을 요청했다면 실행 중 ask_user 단계로 구현하세요.')}


def runtime_file_question_issues(draft, state):
    if runtime_file_context(state) is None:
        return []
    if any(_UPLOAD_QUESTION.search(q) for q in draft.get('questions', [])):
        return ['파일은 실행할 때 받기로 이미 답변했습니다. 파일 업로드를 다시 묻지 말고 실행 입력으로 설계하세요. 미정 업무 조건만 질문할 수 있습니다.']
    return []


def constrain_runtime_readers(schema, state):
    """Deferred input uses the native readers, not an operation needing an existing asset."""
    if runtime_file_context(state) is None:
        return schema
    from copy import deepcopy
    result = deepcopy(schema)
    def available_variant(value):
        props = value.get('properties', {})
        tools = props.get('tool', {}).get('enum', [])
        operation = props.get('settings', {}).get('properties', {}).get('operation', {}).get('enum', [])
        if tools and any(operation):
            remaining = [tool for tool in tools if tool not in ('read_table', 'read_document')]
            if not remaining: return False
            props['tool']['enum'] = remaining
        return True
    def visit(value):
        if isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, dict):
            if 'anyOf' in value:
                value['anyOf'] = [v for v in value['anyOf'] if available_variant(v)]
            for child in value.values(): visit(child)
    visit(result)
    return result
