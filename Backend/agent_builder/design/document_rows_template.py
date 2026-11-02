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

QUESTION = '어떤 항목을 한 줄씩 뽑을까요? 쉼표로 구분해 적어 주세요. (예: 상품명, 단가, 수량, 금액)'
ASK_EXPLANATION = '첨부한 문서의 반복 항목을 한 줄씩 표로 정리합니다.'
EXPLANATION = ('첨부한 문서에서 반복 항목을 한 줄씩 추출해 CSV로 저장합니다. 엑셀(xlsx) 파일은 아직 만들 수 없지만, '
               'CSV는 엑셀에서 바로 열립니다. 추출 값은 원문에서 확인된 것만 기록되고, 확인되지 않은 칸은 비워 둡니다.')


def requested_fields(text):
    fields = [part.strip(' .:;"\'') for part in _SPLIT.split(text or '')]
    fields = [f for f in fields if f]
    if not fields or len(fields) > 12 or any(len(f) > 20 for f in fields):
        return []
    return list(dict.fromkeys(fields))


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
    if len(messages) >= 2 and messages[-2]['role'] == 'assistant' and messages[-2]['content'].endswith(QUESTION):
        return not is_request_change(messages[-1]['content'])
    return bool(_TABLE_INTENT.search(messages[-1]['content']))


def document_rows_reply(state):
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
    asked = len(messages) >= 2 and messages[-1]['role'] == 'user' and messages[-2]['role'] == 'assistant' \
        and messages[-2]['content'].endswith(QUESTION)
    if asked:
        if is_request_change(users[-1]['content']):
            return None  # leave the fixed flow; the planner handles the cancellation or new request
        request = next((m['content'] for m in users[:-1][::-1] if _TABLE_INTENT.search(m['content'])), users[0]['content'])
        fields = requested_fields(users[-1]['content'])
        if not fields:
            return _ask(state, request)
        if reserved_field_issue(fields):
            return _ask(state, request, note=reserved_field_issue(fields))
        draft = _draft(request, users[-1]['content'], fields, documents)
        return {'messages': messages + [{'role': 'assistant', 'content': EXPLANATION}], 'draft': draft,
                'confirmed': False, 'saved_agent_id': None, 'semantic_issues': [],
                'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '문서 표 정리 고정 흐름', 'status': 'completed',
                                                    'detail': '문서 읽기 → 목록 추출(' + ', '.join(fields) + ') → CSV 저장을 코드로 구성했습니다.', 'model_calls': 0}]}
    if _TABLE_INTENT.search(users[-1]['content']):
        return _ask(state, users[-1]['content'])
    return None
