"""Fixed flow for "turn the items in this document/photo into a table" without a planning model.

The small local model kept wiring documents into table-only tools for this request, so the
structure is fixed in code: read_document -> extract_information(rows) -> export_result(csv).
Only the fields to extract are asked from the user, once, without a model call.
"""
import os
import re
from copy import deepcopy

from contracts.models import AgentDraft, NodeSettings, WorkflowDefinition
from workflow_engine.executors.document.extract_information import reserved_field_issue
from workflow_engine.executors.document.table_rows import TABLE_INTENT as _TABLE_INTENT, has_repeated_rows, label, row_collection_intent

_SPLIT = re.compile(r'\s*(?:,|，|、|·|/|\n|\s및\s|\s그리고\s)\s*')
_ALL_TABLES = r'(?:모든|전체)\s*(?:표|페이지)'
_ALL_TABLES_ANSWER = re.compile(_ALL_TABLES + r'(?:에서|의)\s+(.+)', re.S)
_ALL_TABLES_REQUEST = re.compile(r'(?<![\w])' + _ALL_TABLES + r'(?:에서|의|를|을)?(?=\s|[.!?]|$)')
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
    for token in re.findall(r'[가-힣A-Za-z0-9_.]+', _ALL_TABLES_REQUEST.sub(' ', text or '')):
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
    if _inline_fields(request):
        return re.search(r'(?:추출\s*항목|출력\s*열|항목)(?:은|는)?\s*[:：]\s*([^\n.!?]+)', request).group(0)
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

QUESTION_KEY = 'document.fields'
QUESTION = '어떤 항목을 한 줄씩 뽑을까요? 쉼표로 구분해 적어 주세요. (예: 상품명, 단가, 수량, 금액)'
TABLE_SCOPE_QUESTION = '어느 표에서 행을 추출할까요?'
FIELD_QUESTIONS = (QUESTION, TABLE_SCOPE_QUESTION)
QUESTION_LABELS = {label for question in FIELD_QUESTIONS for label in (question, *re.split(r'(?<=[?？])\s+', question))}
ASK_EXPLANATION = '첨부한 문서의 반복 항목을 한 줄씩 표로 정리합니다.'
ALL_FIELDS_NOTE = '"전체"처럼 답하면 어떤 열을 만들지 정할 수 없어요. 표의 열이 될 항목 이름을 적어 주세요.'
FIELD_NAMES_NOTE = '항목 이름만 쉼표로 구분해 입력해주세요. 예: 품명, 실입고.'
TABLE_SCOPE_NOTE = ('같은 열이 여러 표에 있어요. 특정 표는 파일 미리보기에서 열을 선택해 주세요. '
                    '모든 표를 읽으려면 “모든 표에서 품명, 실입고”처럼 답해 주세요.')
EXPLANATION = ('첨부한 문서에서 반복 항목을 한 줄씩 추출해 CSV로 저장합니다. '
               'CSV는 엑셀에서 바로 열립니다. 추출 값은 원문에서 확인된 것만 기록되고, 확인되지 않은 칸은 비워 둡니다.')
DEFAULT_FIELDS_NOTE = ('항목을 따로 정하지 않아 영수증 기본 항목(' + ', '.join(RECEIPT_FIELDS) + ')으로 정리합니다. '
                       '다른 항목이 필요하면 항목 이름을 알려 주세요.')


def requested_fields(text):
    # Semicolons delimit instructions, never field names in the fixed recipe.
    # Keep the complete request for the general planner rather than discarding
    # an unrecognized operation after the list.
    if re.search(r'[;；]', text or ''):
        return []
    fields = [part.strip(' .:;"\'') for part in _SPLIT.split(text or '')]
    fields = [f for f in fields if f]
    if not fields or len(fields) > 12 or any(len(f) > 20 for f in fields) or _processing_in_fields(fields):
        return []
    return list(dict.fromkeys(fields))


def _processing_in_fields(fields):
    """A list segment is removable from scope checks only if it names fields."""
    return any(re.search(r'오름차순|내림차순|(?:^|\s)(?:정렬|필터|선별|집계|그룹화)(?:\s|$)|'
                         r'[<>=]|\d[\d,.]*\s*(?:이상|이하|초과|미만)|'
                         r'\b(?:asc|desc|sort|filter|group\s+by)\b', field, re.I)
               for field in fields)


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
    if not _awaiting_fields(state):
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
    return {'messages': messages + [{'role': 'assistant', 'content': CANCELLED}], 'draft': draft, 'pending_questions': _remaining_questions(state),
            'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 취소', 'status': 'completed',
                                                'detail': '항목 질문에 대한 취소 답변을 모델 호출 없이 처리했습니다.', 'model_calls': 0}]}


def _settings(**values):
    base = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None, question=None,
                        condition_field=None, condition_operator=None).model_dump()
    return {**base, **values}


def _draft(request, answer, fields, documents, *, document_table_id=None):
    nodes = [
        {'id': 'read', 'tool': 'read_document', 'label': '문서 읽기', 'bindings': [],
         'settings': _settings(file_ids=documents, document_table_id=document_table_id)},
        {'id': 'extract', 'tool': 'extract_information', 'label': '항목 목록 추출',
         'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}],
         'settings': _settings(fields=fields, extraction_mode='rows')},
        {'id': 'save', 'tool': 'export_result', 'label': 'CSV로 저장',
         'bindings': [{'input': 'records', 'node_id': 'extract', 'output': 'records'}],
         'settings': _settings(format='csv', fields=fields)},
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


def _ask(state, request, note=None, *, question_text=QUESTION):
    draft = AgentDraft(
        name='문서 항목을 표로 정리', purpose=request[:2000], instructions=request[:12000], tool_ids=[], document_ids=[],
        requirements=[{'description': '사용자 요청', 'source_id': 'user', 'quote': request[:2000]}],
        questions=[question_text], question_options=[[]], question_responses=[{'type':'text'}], unsupported=[], explanation=ASK_EXPLANATION,
        execution_plan={'filters': [], 'sort': [], 'match': 'all', 'missing_policy': 'ask',
                        'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'csv'}).model_dump()
    from agent_builder.conditions.clarification_core import question_record
    scope = ':'.join([state.get('session_id', ''), *sorted(str(resource.get('id')) + '@' + str(resource.get('version'))
                                                   for resource in state.get('resources', []) if resource.get('kind') == 'document')])
    question = question_record('orchestration', QUESTION_KEY, question_text, scope=scope)
    question['response'] = {'type':'text'}
    # Preserve one typed question identity, independently of how the chat renders its sentences.
    return {'messages': state['messages'] + [{'role': 'assistant', 'content': ASK_EXPLANATION + ('\n' + note if note else '') + '\n• ' + question_text}],
            'draft': draft, 'pending_questions': [question], 'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 항목 확인', 'status': 'needs_input',
                                                'detail': '고정 흐름(문서 읽기 → 목록 추출 → CSV 저장)에 쓸 항목을 모델 호출 없이 묻습니다.', 'model_calls': 0}]}



def _remaining_questions(state):
    return [question for question in state.get('pending_questions', [])
            if question.get('key') != QUESTION_KEY and question.get('text') not in QUESTION_LABELS]

def _field_answer(content, state=None):
    """Read free text or resolve a preview selection, returning None for an unsupported preview."""
    # Read a typed answer by question identity; the chat label is presentation only.
    text = content.strip()
    if state:
        latest = max((index for index, message in enumerate(state.get('messages', [])) if message.get('role') == 'user'), default=-1)
        for event in reversed(state.get('clarification_answers', [])):
            if event.get('turn') != latest or not event.get('explicit'):
                continue
            ids = {question['id'] for question in event.get('questions', [])
                   if question.get('key') == QUESTION_KEY or question.get('text') in QUESTION_LABELS}
            values = [item.get('value') for item in event.get('answers', []) if item.get('question_id') in ids]
            if len(values) == 1 and isinstance(values[0], str):
                text = values[0].strip()
                if text.startswith('{'):
                    from .document_selection import document_selection
                    return document_selection(text, state.get('resources', []))
                break
    # Compatibility for older messages that saved a rendered question fragment.
    for label in sorted(QUESTION_LABELS, key=len, reverse=True):
        if text.startswith(label + ' → '):
            text = text[len(label + ' → '):].strip()
            break
    if state:
        resources = state.get('resources') or []
        if _is_preview_request(text, resources):
            return _preview_field_selection(text, resources)
    # Normalize only a complete field-list command, never a general sentence.
    command = re.fullmatch(r'(.+?)(?:을|를|도)\s*(?:추출|추가|포함)(?:해\s*(?:줘|주세요)|해주세요)[.!\s]*', text)
    if command:
        fields = command[1].strip()
        if requested_fields(fields) and not is_request_change(fields):
            return fields
    return text



_PREVIEW_SUFFIX = ' 항목을 추출해 주세요.'


def _is_preview_request(content, resources):
    return _PREVIEW_SUFFIX in content and '에서 ' in content and (
        bool(re.search(r'「[^」]+」의 ', content))
        or any(resource.get('name') and resource['name'] + '에서 ' in content for resource in resources)
    )


def _preview_field_selection(content, resources):
    """Resolve all displayed aliases to exact columns on one selected parser block."""
    matches = [(resource, resource['name'] + '에서 ') for resource in resources
               if resource.get('kind') == 'document' and resource.get('name')
               and content.startswith(resource['name'] + '에서 ')]
    if len(matches) != 1 or not content.endswith(_PREVIEW_SUFFIX):
        return None
    resource, prefix = matches[0]
    from documents.resources.file_inspection import inspect_file
    aliases = [(f"「{table['name']}」의 {field}", table.get('block_id'), field)
               for table in inspect_file(resource)['tables'] for field in table['columns']]
    selected = content[len(prefix):-len(_PREVIEW_SUFFIX)]
    block_ids, fields = set(), []
    # A comma may be part of a column label. A new selection starts with a displayed table alias.
    while selected:
        matches = [choice for choice in aliases if selected == choice[0] or selected.startswith(choice[0] + ', 「')]
        if len(matches) != 1:
            return None
        alias, block_id, field = matches[0]
        block_ids.add(block_id)
        fields.append(field)
        selected = selected[len(alias) + 2:]
    if len(block_ids) != 1 or None in block_ids:
        return None
    return resource['id'], block_ids.pop(), list(dict.fromkeys(fields))

def _multiple_matching_tables(resources, fields):
    from documents.resources.file_inspection import inspect_file
    targets = {label(field) for field in fields}
    matches = set()
    for resource in resources:
        if resource.get('kind') != 'document':
            continue
        for table in inspect_file(resource)['tables']:
            if table.get('block_id') and table['preview'] and targets <= {label(column) for column in table['columns']}:
                matches.add((resource['id'], table['block_id']))
                if len(matches) > 1:
                    return True
    return False


def _simple_extraction(state):
    from .request_guards import review_request
    from agent_builder.capabilities.capability_judgement import active_request_messages
    if review_request(state)[0]:
        return False
    # A fixed CSV recipe cannot own analysis, final reports or explicit node graphs.
    users = [m['content'] for m in active_request_messages(state) if m.get('role') == 'user']
    return not any(re.search(r'최종\s*(?:메모|보고서)|중요\s*조항|위험|노드.{0,30}(?:순서|연결)|Markdown', text, re.I)
                   for text in users)


def _supports_csv_recipe(state, *, preview=False):
    # Source-backed column labels can contain format names or words used in instructions.
    if preview:
        state = {**state, 'messages': state['messages'][:-1]}
    return _simple_extraction(state) and not any(
        re.search(r'(?<![A-Za-z])xlsx(?![A-Za-z])', message.get('content', ''), re.I)
        for message in state.get('messages', []) if message.get('role') == 'user'
    )


def _inline_fields(request):
    match = re.search(r'(?:추출\s*항목|출력\s*열|항목)(?:은|는)?\s*[:：]\s*([^\n.!?]+)', request)
    if not match:
        return []
    answer = _field_answer(match[1])
    return requested_fields(answer) if not is_request_change(answer) and not wants_all_fields(answer) else []


def _awaiting_fields(state):
    messages = state.get('messages', [])
    draft_questions = (state.get('draft') or {}).get('questions') or []
    return (len(messages) >= 2 and messages[-1]['role'] == 'user'
            and messages[-2]['role'] == 'assistant'
            and (messages[-2]['content'].endswith(FIELD_QUESTIONS)
                 or len(draft_questions) == 1 and draft_questions[0] in FIELD_QUESTIONS))


def _field_addition(state):
    """Extend only our unchanged fixed recipe after an explicit list command."""
    draft = state.get('draft') or {}
    messages = state.get('messages') or []
    if draft.get('questions') or draft.get('unsupported') or not messages or messages[-1]['role'] != 'user':
        return None
    match = re.fullmatch(r'(.+?)(?:을|를|도)\s*추가(?:해\s*(?:줘|주세요)|해주세요)[.!\s]*', messages[-1]['content'].strip())
    if not match or is_request_change(match[1]) or wants_all_fields(match[1]):
        return None
    extra = requested_fields(match[1])
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    if len(nodes) != 3 or nodes[1]['tool'] != 'extract_information':
        return None
    current = nodes[1]['settings'].get('fields', [])
    if not current or not extra:
        return None
    documents = [r['id'] for r in state.get('resources', []) if r.get('kind') == 'document']
    reader = nodes[0].get('settings') or {}
    table_id = reader.get('document_table_id')
    if table_id:
        scoped_documents = reader.get('file_ids') or []
        if len(scoped_documents) != 1 or scoped_documents[0] not in documents:
            return None
        documents = scoped_documents
    expected = _draft(draft['purpose'], '', current, documents, document_table_id=table_id)
    try:
        workflow = WorkflowDefinition.model_validate(draft['workflow']).model_dump()
    except ValueError:
        return None
    if (workflow != expected['workflow'] or draft['instructions'] != expected['instructions']
            or draft['document_ids'] != documents or draft['tool_ids'] != expected['tool_ids']):
        return None  # A customized graph belongs to the full planner.
    fields = list(dict.fromkeys([*current, *extra]))
    if len(fields) > 12 or reserved_field_issue(fields):
        return None
    result = deepcopy(draft)
    result['workflow']['nodes'][1]['settings']['fields'] = fields
    result['workflow']['nodes'][2]['settings']['fields'] = fields
    result['instructions'] = _draft(draft['purpose'], '', fields, documents)['instructions']
    result['requirements'].append(dict(description='추출 항목 추가',source_id='user',quote=messages[-1]['content']))
    result['explanation'] = '기존 항목을 유지하고 추출 항목을 반영했습니다: ' + ', '.join(fields)
    return result


def document_rows_applies(state):
    """True when this turn is handled by the fixed flow below (its request or its field answer).

    The condition checker only covers table filtering/deduplication/scheduling; with
    A4B_CONDITION_MODE=enforce its model read the field answer "상품명, 수량, 단가, 금액" as a filter and
    asked nine questions or failed its output contract, so this turn is delegated without a model call.
    """
    if os.getenv('A4B_DOCUMENT_ROWS_TEMPLATE', 'on') == 'off':
        return False
    resources = state.get('resources') or []
    if not any(r.get('kind') == 'document' for r in resources) or any(r.get('kind') == 'table' for r in resources):
        return False
    messages = state.get('messages', [])
    if not messages or messages[-1]['role'] != 'user':
        return False
    answer = _field_answer(messages[-1]['content'], state)
    preview = isinstance(answer, tuple)
    if answer is None or not _supports_csv_recipe(state, preview=preview):
        return False
    if preview:
        return _awaiting_fields(state)
    if _field_addition(state):
        return True
    if _awaiting_fields(state):
        # Same order as document_rows_reply: "다 뽑아줘" answers the question although it ends like a command.
        if _processing_in_fields(_SPLIT.split(answer)):
            return False
        return wants_all_fields(answer) or not is_request_change(answer)
    request = messages[-1]['content'] if _TABLE_INTENT.search(messages[-1]['content']) else _upload_followup_request(messages)
    return bool(request) and not _extra_processing(request, _listed_segment(request))


def document_rows_reply(state, *, check_sources=True):
    """Return the fixed-flow result, or None to continue with the model planner."""
    if os.getenv('A4B_DOCUMENT_ROWS_TEMPLATE', 'on') == 'off':
        return None
    resources = state.get('resources') or []
    documents = [r['id'] for r in resources if r.get('kind') == 'document']
    if not documents or any(r.get('kind') == 'table' for r in resources):
        return None
    messages = state.get('messages', [])
    users = [m for m in messages if m['role'] == 'user']
    if not users:
        return None
    answer = _field_answer(users[-1]['content'], state)
    preview = isinstance(answer, tuple)
    asked = _awaiting_fields(state)
    scope_question = next((question for question in state.get('pending_questions', [])
                           if question.get('text') == TABLE_SCOPE_QUESTION), None)
    if scope_question and not preview:
        fields = scope_question.get('document_fields') or []
        from documents.resources.file_inspection import inspect_file
        tables = [(resource['id'], table) for resource in resources if resource.get('kind') == 'document'
                  for table in inspect_file(resource)['tables'] if table.get('block_id')
                  and set(fields) <= set(table['columns'])]
        chosen = [(identifier, table) for identifier, table in tables if answer == table['name']]
        if len(chosen) == 1 and fields:
            answer = chosen[0][0], chosen[0][1]['block_id'], fields
            preview = True
        elif not (isinstance(answer, str) and _ALL_TABLES_ANSWER.fullmatch(answer)):
            waiting = _ask(state, (state.get('draft') or {}).get('purpose') or users[0]['content'],
                           note=TABLE_SCOPE_NOTE, question_text=TABLE_SCOPE_QUESTION)
            waiting['pending_questions'][0]['document_fields'] = fields
            waiting['clarification_answers'] = [{**event, 'kind':'unresolved'} if event.get('turn') == len(messages)-1 else event
                                                for event in state.get('clarification_answers', [])]
            return waiting
    if answer is None or (preview and not asked) or not _supports_csv_recipe(state, preview=preview):
        return None
    addition = _field_addition(state)
    if addition:
        return {'draft':addition, 'messages':messages+[{'role':'assistant','content':addition['explanation']}],
                'confirmed':False,'saved_agent_id':None,'semantic_issues':[],
                'trace':state.get('trace',[])+[dict(node='conversation',label='추출 항목 추가',status='completed',
                    detail='기존 문서 표 정리 흐름의 항목 목록만 확장했습니다.',model_calls=0)]}
    if asked:
        request = next((m['content'] for m in users[:-1][::-1] if _TABLE_INTENT.search(m['content'])), users[0]['content'])
        explanation = EXPLANATION
        table_id = None
        if preview:
            file_id, table_id, fields = answer
            documents = [file_id]
        elif _processing_in_fields(_SPLIT.split(answer)):
            return None
        # Checked before is_request_change: "다 뽑아줘" ends like a command but still answers the question.
        elif wants_all_fields(answer):
            names = ' '.join([request] + [r.get('name') or '' for r in resources if r.get('kind') == 'document'])
            if not _RECEIPT.search(names):
                return _ask(state, request, note=ALL_FIELDS_NOTE)
            fields, explanation = RECEIPT_FIELDS, DEFAULT_FIELDS_NOTE + '\n' + EXPLANATION
        elif is_request_change(answer):
            return None  # leave the fixed flow; the planner handles the cancellation or new request
        else:
            all_tables = _ALL_TABLES_ANSWER.fullmatch(answer)
            fields = requested_fields(all_tables[1] if all_tables else answer)
            if not fields:
                return _ask(state, request, note=FIELD_NAMES_NOTE)
            if not all_tables and not _ALL_TABLES_REQUEST.search(request) and _multiple_matching_tables(resources, fields):
                waiting = _ask(state, request, note=TABLE_SCOPE_NOTE, question_text=TABLE_SCOPE_QUESTION)
                # A field list alone does not settle the separate table-scope question.
                waiting['clarification_answers'] = [
                    {**event, 'kind': 'unresolved'} if event.get('turn') == len(messages) - 1
                    and any(question.get('text') == TABLE_SCOPE_QUESTION for question in event.get('questions', []))
                    else event for event in state.get('clarification_answers', [])
                ]
                waiting['pending_questions'][0]['document_fields'] = fields
                return waiting
        if issue := reserved_field_issue(fields):
            return _ask(state, request, note=issue)
        draft = _draft(request, users[-1]['content'], fields, documents, document_table_id=table_id)
        source_issues = document_row_source_issues(state, draft) if check_sources else []
        if source_issues:
            draft['questions'] = source_issues
            explanation += '\n' + '\n'.join(source_issues)
        return {'messages': messages + [{'role': 'assistant', 'content': explanation}], 'draft': draft,
                'pending_questions': _remaining_questions(state), 'confirmed': False, 'saved_agent_id': None, 'semantic_issues': [],
                'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 고정 흐름', 'status': 'completed',
                                                    'detail': '문서 읽기 → 목록 추출(' + ', '.join(fields) + ') → CSV 저장을 코드로 구성했습니다.', 'model_calls': 0}]}
    request=users[-1]['content'] if _TABLE_INTENT.search(users[-1]['content']) else _upload_followup_request(messages)
    if request and _extra_processing(request, _listed_segment(request)):
        return None  # the full planner must implement the filter/sort/aggregate step
    if request:
        fields = _inline_fields(request) or _fields_in_request(request)
        if fields and not reserved_field_issue(fields):
            # Already listed: do not ask again (clarification core: settled questions are not asked).
            draft = _draft(request, request, fields, documents)
            source_issues = document_row_source_issues(state, draft) if check_sources else []
            if source_issues:
                draft['questions'] = source_issues
            return {'messages': messages + [{'role': 'assistant', 'content': '\n'.join(source_issues) if source_issues else EXPLANATION}], 'draft': draft,
                    'pending_questions': _remaining_questions(state), 'confirmed': False, 'saved_agent_id': None, 'semantic_issues': [],
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


def document_row_contract_issues(state, draft):
    """The source request owns extraction granularity; a generated graph cannot downgrade it."""
    messages = state.get('messages', [])
    latest = max((index for index, message in enumerate(messages) if message.get('role') == 'user'), default=-1)
    if latest < 0 or draft.get('questions') or draft.get('unsupported'):
        return []
    feasibility = document_row_source_issues(state, draft)
    if feasibility:
        return feasibility
    expected = document_rows_reply({**state, 'messages': messages[:latest + 1], 'draft': None}, check_sources=False)
    expected_nodes = ((expected or {}).get('draft', {}).get('workflow') or {}).get('nodes', [])
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    readers = {node['id']: node['settings'] for node in nodes if node['tool'] == 'read_document'}
    if not expected_nodes:
        # The full planner handles arbitrary document names/extra processing.
        # Only evidence of multiple item rows forbids a scalar extraction.
        from agent_builder.capabilities.capability_judgement import active_request_messages
        requests = [message['content'] for message in active_request_messages(state) if message.get('role') == 'user']
        rows_requested = row_collection_intent(*requests)
        if rows_requested is not True and not any(_TABLE_INTENT.search(request) for request in requests):
            return []
        from documents.resources.document_resources import chunks_for_asset, supports
        from workflow_engine.executors.document.row_contract import scalar_selection
        if rows_requested is not True and scalar_selection(*requests) is not None:
            return []
        for node in nodes:
            settings = node['settings']
            if (node['tool'] != 'extract_information' or settings.get('operation') or not settings.get('fields')
                    or settings.get('extraction_mode') in ('rows', 'receipt_items')):
                continue
            reader = next((readers[b['node_id']] for b in node.get('bindings', [])
                           if b.get('input') == 'documents' and b['node_id'] in readers), {})
            file_ids = reader.get('file_ids') or draft.get('document_ids', [])
            table_id = reader.get('document_table_id')
            for asset in state.get('resources', []):
                if asset['id'] not in file_ids or not supports(asset, 'document'):
                    continue
                parts = (part for part in chunks_for_asset(asset) if table_id is None or part.get('block_id') == table_id)
                if has_repeated_rows(parts, settings['fields'], across_pages=rows_requested is True):
                    return ['원본 표의 반복 행을 단일 값으로 축소할 수 없습니다. 행 단위 추출로 설계해 주세요.']
        return []
    fields = expected_nodes[1]['settings']['fields']
    extractors = [node for node in nodes if node['tool'] == 'extract_information']
    exporters = [node for node in nodes if node['tool'] == 'export_result']
    if (len(extractors) != 1 or extractors[0]['settings'].get('extraction_mode') != 'rows'
            or extractors[0]['settings'].get('fields') != fields
            or len(exporters) != 1 or exporters[0]['settings'].get('fields') != fields
            or exporters[0]['settings'].get('format') != 'csv'
            or not any(binding.get('node_id') == extractors[0]['id'] and binding.get('output') == 'records'
                       and binding.get('input') == 'records' for binding in exporters[0].get('bindings', []))):
        return ['문서 표 정리는 항목마다 한 행을 추출하고 요청한 열을 CSV에 저장해야 합니다. 단일 값 추출로 변경할 수 없습니다.']
    expected_reader = expected_nodes[0]['settings']
    table_id = expected_reader.get('document_table_id')
    if table_id is not None and not any(
            binding.get('input') == binding.get('output') == 'documents'
            and readers.get(binding['node_id'], {}).get('document_table_id') == table_id
            and readers[binding['node_id']].get('file_ids') == expected_reader['file_ids']
            for binding in extractors[0].get('bindings', [])):
        return ['미리보기에서 선택한 원본 문서와 표만 읽도록 연결해 주세요.']
    return []


def document_row_source_issues(state, draft):
    """Reject known unexecutable row exports before ready/save, without model calls."""
    from documents.resources.document_resources import chunks_for_asset
    from workflow_engine.executors.document.row_contract import native_rows, NO_COORDINATES
    from workflow_engine.executors.document.extract_information import EVIDENCE_KEYS, MAX_ROWS, validate_extraction_size
    from workflow_engine.executors.sources import source_file_ids
    from workflow_engine.execution_path import records_source_node

    nodes = (draft.get('workflow') or {}).get('nodes', [])
    readers = {node['id']: node for node in nodes if node['tool'] == 'read_document'}
    by_id = {node['id']: node for node in nodes}
    exported_sources = {source['id'] for node in nodes if node['tool'] == 'export_result'
                        and (source := records_source_node(node, by_id)) is not None}
    resources = {asset['id']: asset for asset in state.get('resources', [])}
    chunks = {}

    class DesignStorage:
        def asset(self, identifier):
            if identifier not in resources:
                raise ValueError('선택한 문서를 다시 연결해 주세요.')
            return resources[identifier]

    storage = DesignStorage()
    for node in nodes:
        settings = node['settings']
        if (node['tool'] != 'extract_information' or settings.get('operation') or settings.get('extraction_mode') != 'rows'
                or node['id'] not in exported_sources):
            continue
        for binding in node.get('bindings', []):
            reader = readers.get(binding['node_id']) if binding.get('input') == 'documents' else None
            if not reader or reader['settings'].get('operation') or binding.get('output') != 'documents':
                continue
            table_id = reader['settings'].get('document_table_id')
            selected_parts = []
            try:
                ids = source_file_ids(reader, [], storage, draft, allow_deferred=True)
                for identifier in ids:
                    asset = storage.asset(identifier)
                    if identifier not in chunks:
                        chunks[identifier] = list(chunks_for_asset(asset))
                    parts = chunks[identifier]
                    if table_id is not None:
                        parts = [part for part in parts if part.get('block_id') == table_id]
                    selected_parts.append((asset['name'], parts))
                validate_extraction_size(part for _, parts in selected_parts for part in parts)
            except ValueError as error:
                return [str(error)]
            for name, parts in selected_parts:
                try:
                    rows = native_rows(parts, settings.get('fields') or [], EVIDENCE_KEYS, MAX_ROWS)
                    if rows is None:
                        return [name + ': ' + NO_COORDINATES]
                except ValueError as error:
                    return [name + ': ' + str(error)]
    return []
