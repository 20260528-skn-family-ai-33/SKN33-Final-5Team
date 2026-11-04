"""Collect the document and fields for a simple receipt extraction before planning."""
import hashlib
import os
import re
from pathlib import PurePath

from agent_builder.conditions.condition_check import _pending_draft
from agent_builder.conditions.input_resolution import answer_kind


# Collect missing receipt inputs before planning; preserve an optional export request.
_CONTEXT = re.compile(r'파일|사진|이미지|pdf|문서|스캔본|내용|정보|항목|데이터|텍스트|글자|'
                      r'올려주면|올리면|올려서|올렸는데|첨부해주면|첨부하면|첨부해서|업로드해주면|업로드하면|업로드해서|'
                      r'에서|으로|안에|있는|속|의|을|를|에|좀')
_REQUEST_ENDING = re.compile(r'주세요|줄래요?|주실|있을까요|있나요|있어요?|있니|싶어요?|'
                             r'가능|한가요|할래|할|해|하|줘|줄|고|수|요')
_EXPORT = re.compile(r'^(?:해서|하여|해|고|서)?(?:엑셀|excel|\.?xlsx|csv)(?:파일)?'
                     r'(?:형식)?(?:으로|로|에)?(?:정리|저장|출력|만들어?|뽑아|내보내)')
_RECEIPT_FORMATS = {'pdf', 'jpeg', 'jpg', 'png', 'bmp', 'webp', 'tif', 'tiff', 'heic'}
_FIELD_CHOICES = ['날짜', '상호명', '결제 금액']


def _request(state):
    # Old conversations store button clicks as user turns, including clicks before attachment.
    return next((m['content'] for m in reversed(state.get('messages', []))
                 if m['role'] == 'user' and answer_kind(m['content']) != 'guidance'), '')


def simple_receipt_request(text):
    compact = ''.join(text.casefold().split()).strip('.!?~？')
    receipt = re.match(r'(?:첨부한|올려준|올린|업로드한|이|내|제)?영수증', compact)
    if not receipt:
        return False
    body = compact[receipt.end():]
    action = re.search(r'추출|뽑아|읽어', body)
    if not action or _CONTEXT.sub('', body[:action.start()]):
        return False
    ending = _EXPORT.sub('', body[action.end():], count=1)
    return not _REQUEST_ENDING.sub('', ending)


def receipt_workbook_request(text):
    return bool(re.search(r'엑셀|excel|xlsx', text, re.I) and simple_receipt_request(text))


def receipt_document(resource):
    fmt = resource.get('format') or PurePath(resource.get('name') or '').suffix
    return resource.get('kind') == 'document' and fmt.lower().lstrip('.') in _RECEIPT_FORMATS


def _field_action(documents, workbook, inspections=None):
    from documents.resources.document_resources import chunks_for_asset
    from documents.resources.file_inspection import inspect_file
    from workflow_engine.executors.document.receipt_fields import canonical_label, recognized_facts
    from workflow_engine.executors.document.receipt_items import ITEM_FIELDS, preview_items

    by_id = {i['id']: i for i in inspections or []}
    values, rows, item_columns = {}, [], set()
    all_have_items = bool(documents)
    readable = False
    for document in documents:
        inventory = by_id.get(document['id']) or inspect_file(document)
        readable |= inventory['has_readable_text']
        chunks = list(chunks_for_asset(document))
        labels = [canonical_label(f['label']) for f in inventory['fields']]
        candidates = list(dict.fromkeys([*_FIELD_CHOICES, '거래시간', 'POS번호', '부가세', '과세 물품가액', '공급가액', *labels]))
        for field, fact in recognized_facts(candidates, chunks).items():
            if fact:
                values.setdefault(field, set()).add(fact['value'])
        if workbook:
            sample = preview_items(chunks, limit=5)
            all_have_items &= bool(sample)
            item_columns.update(field for field in ITEM_FIELDS if any(row.get(field) is not None for row in sample))
            rows.extend(sample[:5-len(rows)])

    detected = [{'label': field, **({'value': next(iter(hits))[:160]} if len(hits)==1 else {})}
                for field, hits in values.items()]
    action = {'type': 'select_fields', 'options': list(dict.fromkeys([*_FIELD_CHOICES, *values])),
              'detected_fields': detected, 'has_readable_text': readable}
    if workbook:
        action['item_details'] = {'label':'상품별 구매 내역', 'options':[f for f in ITEM_FIELDS if f in item_columns],
                                  'selected_by_default': bool(item_columns) and all_have_items, 'preview':rows}
    return action


def current_receipt_questions(state, resources, inspections):
    """Refresh a previously interrupted receipt's controls from the current reviewed assets."""
    pending = state.get('pending_questions') or []
    if not any(q.get('key') == 'receipt.fields' for q in pending):
        return pending
    documents = [r for r in resources if receipt_document(r)]
    workbook = receipt_workbook_request(_request(state))
    return [{**q, 'action': _field_action(documents, workbook, inspections)}
            if q.get('key') == 'receipt.fields' else q for q in pending]


def _question(state, original_id=None):
    documents = [r for r in state.get('resources', []) if receipt_document(r)]
    workbook = receipt_workbook_request(_request(state))
    if documents:
        key = 'receipt.fields'
        text = '엑셀(.xlsx)에 담을 항목을 선택해 주세요.' if workbook else '어떤 항목을 추출할까요?'
        prefix = ', '.join(r['name'] for r in documents if r.get('name'))
        message = (f'{prefix} 파일을 연결했어요. ' if prefix else '') + text
        action = _field_action(documents, workbook)
    else:
        key = 'receipt.attachment'
        text = '영수증 사진이나 PDF를 올려 주세요.'
        message = text + (' 엑셀(.xlsx)에 담을 항목은 파일을 올린 뒤 선택할 수 있어요.' if workbook
                          else ' 파일을 올린 뒤 추출할 항목을 선택할 수 있어요.')
        action = {'type': 'attach_receipt'}
    question_id = original_id or hashlib.sha256(
        f"{state.get('session_id', '')}:{len(state['messages'])}:{key}".encode()).hexdigest()[:24]
    question = {'id': question_id, 'key': key, 'source': 'receipt', 'text': text, 'options': [], 'action': action}
    return question, message


def _prompt(state, original_id=None):
    question, message = _question(state, original_id)
    draft = _pending_draft(state, {'decision': 'clarify', 'questions': [question]})
    draft['explanation'] = message
    return {'pending_questions': [question], 'draft': draft, 'confirmed': False, 'saved_agent_id': None, 'repair_count': 0,
            'issues': [], 'semantic_issues': [], 'condition_enforced': False, 'status': 'needs_clarification',
            'messages': state['messages'] + [{'role': 'assistant', 'content': message}]}


def receipt_intake(state):
    messages = state.get('messages') or []
    if (os.getenv('A4B_ATTACHMENT_PRECHECK', 'on') == 'off' or not messages or messages[-1]['role'] != 'user'
            or not simple_receipt_request(messages[-1]['content'])):
        return None
    return _prompt(state)


def legacy_receipt_intake(state):
    """Present the new control for the equivalent pending question in an old checkpoint."""
    pending = state.get('pending_questions') or []
    request = _request(state)
    if state.get('status') != 'needs_clarification' or not simple_receipt_request(request):
        return None
    if not pending:
        # Planner questions in older checkpoints have neither action metadata nor an ID.
        texts = (state.get('draft') or {}).get('questions') or []
        def format_question(text):
            return re.search(r'엑셀|xlsx|excel', text, re.I) and re.search(r'형식|포맷|텍스트|저장|출력', text)
        def file_question(text):
            return re.search(r'파일|영수증|사진|이미지|pdf', text, re.I) and re.search(r'첨부|업로드|올려|연결', text)
        if (not receipt_workbook_request(request) or not any(map(format_question, texts))
                or not all(format_question(t) or file_question(t) for t in texts)):
            return None
        projected = _prompt(state)
        projected['pending_questions'][0]['supersedes_texts'] = texts
        return projected
    if not all(q.get('source') == 'capability' for q in pending):
        return None
    file_record = next((q for q in pending if re.search(r'영수증|파일|첨부', q['text'])), None)
    fields_record = next((q for q in pending if re.search(r'항목|필드', q['text'])), None)
    if not (file_record and fields_record) or len(pending) > 2:
        return None
    current = fields_record if any(map(receipt_document, state.get('resources', []))) else file_record
    prompt = _prompt(state, current['id'])
    prompt['pending_questions'][0]['supersedes_texts'] = [q['text'] for q in pending]
    return prompt


def receipt_upload_prompt(state, asset):
    if not any(q.get('source') == 'receipt' for q in state.get('pending_questions', [])) and not legacy_receipt_intake(state):
        return None
    if not receipt_document(asset):
        raise ValueError('영수증 사진이나 PDF 파일을 첨부해 주세요.')
    return _prompt({**state, 'resources': [*state.get('resources', []), asset]})
