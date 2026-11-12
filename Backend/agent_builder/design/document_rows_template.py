"""Fixed flow for "turn the items in this document/photo into a table" without a planning model.

The small local model kept wiring documents into table-only tools for this request, so the
structure is fixed in code: read_document -> extract_information(rows) -> export_result(csv).
Only the fields to extract are asked from the user, once, without a model call.
"""
import os
import re

from contracts.models import AgentDraft, NodeSettings
from workflow_engine.executors.document.extract_information import reserved_field_issue

# The user wants a table-shaped result, not a summary.
_TABLE_INTENT = re.compile(r'(엑셀|excel|xlsx|csv|표로|표 형태|표 형식|목록으로|한 줄씩|행으로|스프레드시트)', re.IGNORECASE)
_SPLIT = re.compile(r'\s*(?:,|，|、|·|/|\n|\s및\s|\s그리고\s)\s*')
# A reply to the field question that cancels or changes the request instead of naming fields.
_CHANGE = re.compile(r'(취소|그만|중단|됐어|됐습니다|필요\s*없|안\s*할래|하지\s*마|말고|대신|바꿔|바꿀|처음부터)')
_NEGATION = re.compile(r'^(아니|아뇨|아냐|노노|no\b)', re.IGNORECASE)
_SENTENCE_END = re.compile(r'(줘|주세요|해요|세요|하자|할게|싶어|싶어요|싶다|니다|네요|까요|나요|[?？!])$')
# "전체 다", "전부", "알아서 다 뽑아줘": the user wants every item but names no column.
# Matched with spaces removed; taking it as one field named "전체 다" gave a one-column CSV of OCR lines.
_ALL_FIELDS = re.compile(r'^(?:(?:영수증|문서|사진)?에?있는(?:거|것|항목))?(?:그냥|알아서)?'
                         r'(?:전체|전부|모두|모든(?:항목|것|거|내용)?|다|알아서|아무거나|all|everything)'
                         r'(?:다)?(?:항목|내용)?(?:다)?'
                         r'(?:요|뽑아|뽑아줘|뽑아주세요|해줘|해주세요|정리해줘|정리해주세요|넣어줘|넣어주세요)?$', re.IGNORECASE)
# The fixed flow only does "read document -> extract listed rows -> CSV". It is used only when
# every word of the request belongs to that job (allow-list); any other word (a filter, sort,
# top-N, grouping, comparison ...) goes to the full planner. Unknown wording therefore costs a
# planner call instead of silently dropping a requested step.
_EXTRACTION_ONLY = (
    '문서', '파일', '자료', '영수증', '계약서', '견적서', '회의록', '보고서', '송장', '전표', '이미지', '사진', '스캔',
    'pdf', 'png', 'jpg', '첨부', '업로드', '올려', '올린', '올리', '올렸', '내용', '항목', '정보', '데이터', '값', '표', '엑셀',
    'excel', 'csv', '스프레드시트', '목록', '한', '줄씩', '줄', '행', '형태', '형식', '정리', '뽑아', '뽑기', '뽑을', '뽑은', '뽑아서', '추출', '모아', '만들',
    '저장', '변환', '해', '해줘', '해주', '해서', '줘', '주세요', '줄래', '주실', '주면', '좋겠', '부탁', '그리고', '및', '좀', '다', '모두', '전부',
    '각', '여기', '이', '그', '내', '제', '있는', '거', '것', '같이', '깔끔', '알아서', '그냥', '음', '잘', '모르겠')
_PARTICLES = ('으로', '에서', '이랑', '에', '로', '을', '를', '은', '는', '이', '가', '의', '도', '만', '과', '와', '랑')


def _extra_processing(text, fields=''):
    """True when the request has words outside the extraction-only job."""
    if fields:
        text = text.replace(fields, ' ')
    for token in re.findall(r'[가-힣A-Za-z0-9_.]+', text or ''):
        word = token.casefold()
        if word in _PARTICLES:
            continue  # a particle left after removing the listed fields
        # Stems are matched by prefix ("엑셀로", "정리해줘"), so particles need no stripping,
        # and a noun ending in a particle-like syllable ("문서") is never cut.
        if not any(_allowed(word, allowed) for allowed in _EXTRACTION_ONLY):
            return True
    return False


def _allowed(word, allowed):
    # One-syllable entries must match whole ("이", "표로"), never as a prefix of
    # "이상인", "내림차순", "제외", "그룹".
    if len(allowed) == 1:
        return word == allowed or word[1:] in _PARTICLES and word[0] == allowed
    return word.startswith(allowed)


_FIELDS_IN_REQUEST = re.compile(r'(?:에서|의)\s*([^.?!\n]+?)\s*(?:을|를)\s*[^.?!\n,]{0,16}?(?:뽑|추출|정리|모아)')


def _listed_segment(request):
    match = _FIELDS_IN_REQUEST.search(request or '')
    return match.group(1) if match and _fields_in_request(request) else ''


def _fields_in_request(request):
    """Fields the request already lists ("품목, 금액을 뽑아"); a single noun is not a list."""
    match = _FIELDS_IN_REQUEST.search(request or '')
    if not match:
        return []
    # "날짜와 금액" is a list too; a two-letter stem before 와/과 avoids words such as 결과.
    listed = re.sub(r'(?<=[가-힣A-Za-z0-9]{2})(?:와|과)\s+', ', ', match.group(1))
    if not re.search(r',|，|、|\s및\s', listed):
        return []
    fields = requested_fields(listed)
    return fields if len(fields) >= 2 and not reserved_field_issue(fields) else []


_RECEIPT = re.compile(r'(영수증|전표|receipt)', re.IGNORECASE)
RECEIPT_FIELDS = ['상품명', '단가', '수량', '금액']

QUESTION = '어떤 항목을 한 줄씩 뽑을까요? 쉼표로 구분해 적어 주세요. (예: 상품명, 단가, 수량, 금액)'
ASK_EXPLANATION = '첨부한 문서의 반복 항목을 한 줄씩 표로 정리합니다.'
ALL_FIELDS_NOTE = '"전체"처럼 답하면 어떤 열을 만들지 정할 수 없어요. 표의 열이 될 항목 이름을 적어 주세요.'
EXPLANATION = ('첨부한 문서에서 반복 항목을 한 줄씩 추출해 CSV로 저장합니다. '
               'CSV는 엑셀에서 바로 열립니다. 추출 값은 원문에서 확인된 것만 기록되고, 확인되지 않은 칸은 비워 둡니다.')
DEFAULT_FIELDS_NOTE = ('항목을 따로 정하지 않아 영수증 기본 항목(' + ', '.join(RECEIPT_FIELDS) + ')으로 정리합니다. '
                       '다른 항목이 필요하면 항목 이름을 알려 주세요.')


def requested_fields(text):
    fields = [part.strip(' .:;"\'') for part in _SPLIT.split(text or '')]
    fields = [f for f in fields if f]
    if not fields or len(fields) > 12 or any(len(f) > 20 for f in fields):
        return []
    return list(dict.fromkeys(fields))


def wants_all_fields(text):
    """True when the answer asks for "everything" instead of naming the fields."""
    return bool(_ALL_FIELDS.match(re.sub(r'[\s.,!~"\']', '', text or '')))


def _ends_with_l_rae(text):
    # "할래", "바꿀래": 래 after a syllable with final ㄹ is a sentence ending; "거래" is a field.
    return len(text) >= 2 and text[-1] == '래' and '가' <= text[-2] <= '힣' and (ord(text[-2]) - 0xAC00) % 28 == 8


def is_request_change(text):
    """True when the answer is a command (cancel, change the request) rather than a field list."""
    parts = [part.strip(' .:;"\'') for part in _SPLIT.split(text or '')]
    parts = [p for p in parts if p]
    if not parts:
        return False
    # Field names are nouns: any sentence-like part ("요약만 해줘") or a leading "아니"
    # means the user is talking, not listing. "취소 여부, 금액" still names fields.
    if any(_SENTENCE_END.search(p) or _ends_with_l_rae(p) for p in parts) or _NEGATION.match(parts[0]):
        return True
    return len(parts) == 1 and bool(_CHANGE.search(parts[0]))


# A bare cancellation of the field question ends the fixed flow without any model.
# Live 2026-11-03 (RunPod, B-8): "그만할래" was sent to the capability model and the planner,
# which produced a 15-node extraction draft once and a "missing capability" verdict another time.
# A changed request ("대신 금액만 뽑아줘", "처음부터 다시") still goes to the normal path.
_CANCEL_ONLY = re.compile(r'^(?:아니(?:요)?)?(?:그만(?:할래|할게|해|둘래|두자|하자)?|취소(?:할래|할게|해|하자)?|중단(?:할래|해)?|'
                          r'됐어(?:요)?|됐습니다|필요\s*없(?:어|어요|습니다)?|안\s*할래(?:요)?|안\s*해(?:도\s*돼)?|하지\s*마)'
                          r'(?:요|줘|주세요)?$')
CANCELLED = '문서 표 정리를 취소했습니다. 다른 작업이 필요하면 알려 주세요.'


def document_rows_cancel_reply(state):
    """The user cancelled the fixed flow's field question; reply in code, before any model call."""
    if os.getenv('A4B_DOCUMENT_ROWS_TEMPLATE', 'on') == 'off':
        return None
    messages = state.get('messages', [])
    if len(messages) < 2 or messages[-1]['role'] != 'user' or messages[-2]['role'] != 'assistant' \
            or not messages[-2]['content'].endswith(QUESTION):
        return None
    if not any(r.get('kind') == 'document' for r in state.get('resources') or []):
        return None
    answer = re.sub(r'[\s.,!~]+', '', messages[-1]['content'])
    if not _CANCEL_ONLY.match(answer):
        return None
    request = next((m['content'] for m in messages[:-1][::-1] if m['role'] == 'user'), messages[-1]['content'])
    draft = AgentDraft(
        name='문서 항목을 표로 정리', purpose=request[:2000], instructions=request[:12000], tool_ids=[], document_ids=[],
        requirements=[{'description': '사용자 요청', 'source_id': 'user', 'quote': request[:2000]}],
        questions=[], question_options=[], unsupported=[], explanation=CANCELLED,
        execution_plan={'filters': [], 'sort': [], 'match': 'all', 'missing_policy': 'ask',
                        'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'csv'}).model_dump()
    return {'messages': messages + [{'role': 'assistant', 'content': CANCELLED}], 'draft': draft,
            'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 취소', 'status': 'completed',
                                                'detail': '항목 질문에 대한 취소 답변을 모델 호출 없이 처리했습니다.', 'model_calls': 0}]}


def _settings(**values):
    base = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None, question=None,
                        condition_field=None, condition_operator=None).model_dump()
    return {**base, **values}


def _draft(request, answer, fields, documents):
    nodes = [
        {'id': 'read', 'tool': 'read_document', 'label': '문서 읽기', 'bindings': [],
         'settings': _settings(file_ids=documents)},
        {'id': 'extract', 'tool': 'extract_information', 'label': '항목 목록 추출',
         'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}],
         'settings': _settings(fields=fields, extraction_mode='rows')},
        {'id': 'save', 'tool': 'export_result', 'label': 'CSV로 저장',
         'bindings': [{'input': 'records', 'node_id': 'extract', 'output': 'records'}],
         'settings': _settings(format='csv')},
    ]
    edges = [{'source': 'read', 'target': 'extract', 'branch': 'next'},
             {'source': 'extract', 'target': 'save', 'branch': 'next'}]
    return AgentDraft(
        name='문서 항목을 표로 정리', purpose=request[:2000],
        instructions='첨부 문서에서 반복 항목을 한 줄씩 찾아 ' + ', '.join(fields) + ' 값을 원문 그대로 추출하고 CSV로 저장합니다.',
        tool_ids=['read_document', 'extract_information', 'export_result'], document_ids=documents,
        requirements=[{'description': '사용자 요청', 'source_id': 'user', 'quote': request[:2000]},
                      {'description': '추출 항목', 'source_id': 'user', 'quote': answer[:2000]}],
        questions=[], question_options=[], unsupported=[], explanation=EXPLANATION,
        execution_plan={'filters': [], 'sort': [], 'match': 'all', 'missing_policy': 'keep',
                        'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'csv'},
        workflow={'entry': 'read', 'nodes': nodes, 'edges': edges}).model_dump()


def _ask(state, request, note=None):
    draft = AgentDraft(
        name='문서 항목을 표로 정리', purpose=request[:2000], instructions=request[:12000], tool_ids=[], document_ids=[],
        requirements=[{'description': '사용자 요청', 'source_id': 'user', 'quote': request[:2000]}],
        questions=[QUESTION], question_options=[], unsupported=[], explanation=ASK_EXPLANATION,
        execution_plan={'filters': [], 'sort': [], 'match': 'all', 'missing_policy': 'ask',
                        'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'csv'}).model_dump()
    # The message must keep ending with QUESTION; that is how the next answer is recognized.
    return {'messages': state['messages'] + [{'role': 'assistant', 'content': ASK_EXPLANATION + ('\n' + note if note else '') + '\n• ' + QUESTION}],
            'draft': draft, 'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 항목 확인', 'status': 'needs_input',
                                                'detail': '고정 흐름(문서 읽기 → 목록 추출 → CSV 저장)에 쓸 항목을 모델 호출 없이 묻습니다.', 'model_calls': 0}]}


def _field_answer(content):
    # The answer form includes the question in the displayed chat message.
    return content.removeprefix(QUESTION + ' → ').strip()


def document_rows_applies(state):
    """True when this turn is handled by the fixed flow below (its request or its field answer).

    The condition checker only covers table filtering/deduplication/scheduling; with
    A4B_CONDITION_MODE=enforce its model read the field answer "상품명, 수량, 단가, 금액" as a filter and
    asked nine questions or failed its output contract, so this turn is delegated without a model call.
    """
    if os.getenv('A4B_DOCUMENT_ROWS_TEMPLATE', 'on') == 'off':
        return False
    # The fixed flow writes CSV. Explicit XLSX requests belong to the full planner,
    # which can now connect create_spreadsheet instead of changing the requested format.
    if any(re.search(r'(?<![A-Za-z])xlsx(?![A-Za-z])',m.get('content',''),re.I)
           for m in state.get('messages',[]) if m.get('role')=='user'):
        return False
    resources = state.get('resources') or []
    if not any(r.get('kind') == 'document' for r in resources) or any(r.get('kind') == 'table' for r in resources):
        return False
    messages = state.get('messages', [])
    if not messages or messages[-1]['role'] != 'user':
        return False
    if len(messages) >= 2 and messages[-2]['role'] == 'assistant' and messages[-2]['content'].endswith(QUESTION):
        # Same order as document_rows_reply: "다 뽑아줘" answers the question although it ends like a command.
        answer = _field_answer(messages[-1]['content'])
        return wants_all_fields(answer) or not is_request_change(answer)
    request = messages[-1]['content'] if _TABLE_INTENT.search(messages[-1]['content']) else _upload_followup_request(messages)
    return bool(request) and not _extra_processing(request, _listed_segment(request))


def document_rows_reply(state):
    """Return the fixed-flow result, or None to continue with the model planner."""
    if os.getenv('A4B_DOCUMENT_ROWS_TEMPLATE', 'on') == 'off':
        return None
    # The fixed flow writes CSV. Explicit XLSX requests belong to the full planner,
    # which can now connect create_spreadsheet instead of changing the requested format.
    if any(re.search(r'(?<![A-Za-z])xlsx(?![A-Za-z])',m.get('content',''),re.I)
           for m in state.get('messages',[]) if m.get('role')=='user'):
        return None
    resources = state.get('resources') or []
    documents = [r['id'] for r in resources if r.get('kind') == 'document']
    if not documents or any(r.get('kind') == 'table' for r in resources):
        return None
    messages = state.get('messages', [])
    users = [m for m in messages if m['role'] == 'user']
    if not users:
        return None
    asked = len(messages) >= 2 and messages[-1]['role'] == 'user' and messages[-2]['role'] == 'assistant' \
        and messages[-2]['content'].endswith(QUESTION)
    if asked:
        answer = _field_answer(users[-1]['content'])
        request = next((m['content'] for m in users[:-1][::-1] if _TABLE_INTENT.search(m['content'])), users[0]['content'])
        explanation = EXPLANATION
        # Checked before is_request_change: "다 뽑아줘" ends like a command but still answers the question.
        if wants_all_fields(answer):
            names = ' '.join([request] + [r.get('name') or '' for r in resources if r.get('kind') == 'document'])
            if not _RECEIPT.search(names):
                return _ask(state, request, note=ALL_FIELDS_NOTE)
            fields, explanation = RECEIPT_FIELDS, DEFAULT_FIELDS_NOTE + '\n' + EXPLANATION
        elif is_request_change(answer):
            return None  # leave the fixed flow; the planner handles the cancellation or new request
        else:
            fields = requested_fields(answer)
            if not fields:
                return _ask(state, request)
            if reserved_field_issue(fields):
                return _ask(state, request, note=reserved_field_issue(fields))
        draft = _draft(request, answer, fields, documents)
        return {'messages': messages + [{'role': 'assistant', 'content': explanation}], 'draft': draft,
                'confirmed': False, 'saved_agent_id': None, 'semantic_issues': [],
                'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 고정 흐름', 'status': 'completed',
                                                    'detail': '문서 읽기 → 목록 추출(' + ', '.join(fields) + ') → CSV 저장을 코드로 구성했습니다.', 'model_calls': 0}]}
    request=users[-1]['content'] if _TABLE_INTENT.search(users[-1]['content']) else _upload_followup_request(messages)
    if request and _extra_processing(request, _listed_segment(request)):
        return None  # the full planner must implement the filter/sort/aggregate step
    if request:
        fields = _fields_in_request(request)
        if fields:
            # Already listed: do not ask again (clarification core: settled questions are not asked).
            draft = _draft(request, request, fields, documents)
            return {'messages': messages + [{'role': 'assistant', 'content': EXPLANATION}], 'draft': draft,
                    'confirmed': False, 'saved_agent_id': None, 'semantic_issues': [],
                    'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 고정 흐름', 'status': 'completed',
                                                        'detail': '요청에 적힌 항목(' + ', '.join(fields) + ')으로 문서 읽기 → 목록 추출 → CSV 저장을 구성했습니다.', 'model_calls': 0}]}
        return _ask(state, request)
    return None


def _upload_followup_request(messages):
    users=[m['content'] for m in messages if m['role']=='user']
    if not users or not re.search(r'(?:올려|올렸|업로드|첨부).{0,12}(?:놨|놓|했|완료|어|어요)',users[-1]):return None
    for text in reversed(users[:-1]):
        if _TABLE_INTENT.search(text):return text
        if _CHANGE.search(text):return None
    return None
