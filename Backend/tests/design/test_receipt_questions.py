"""Exercise receipt clarification through the same persisted worker actions as the studio."""
import pytest

from infrastructure.persistence.ids import uid
from service.worker import handle
from tests.design.test_builder import draft


REQUEST = '영수증 파일 올려주면 내용 추출해줄래?'
EXCEL_REQUEST = '영수증 올려주면 내용 추출해서 엑셀에 정리해줄 수 있어?'
SCREENSHOT_REQUEST = '영수증 올려주면 항목 추출해서 엑셀로 뽑아줄 수 있어?'
SCREENSHOT_QUESTIONS = ['영수증 파일을 업로드해 주시겠습니까?',
                        '데이터를 엑셀 파일로 저장할지, 아니면 텍스트로 출력할지 선택해 주세요.']
OLD_BUTTON_REPLIES = ['자료 연결 후 기준 입력', '자료 연결 후 기준 입력', '연결 자료에서 기준 확인 후 입력']
RECEIPT = {'name': 'receipt.png', 'kind': 'document', 'format': 'png',
           'text': '상호명: A4B 상점\n결제 금액: 12,000원', 'columns': []}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from service.handlers import design
    build = design.build_graph
    planner_inputs = []

    def configured(*args, **kwargs):
        return build(*args, **kwargs, capability_mode='off', condition_mode='off',
                     verifier_mode='off', requirement_mode='off')

    def planner(state):
        planner_inputs.append(state)
        question = '결과는 어떤 형식으로 저장할까요?'
        planned = draft([question])
        planned['requirements'] = [{'source_id': 'user', 'description': '요청',
                                    'quote': state['messages'][0]['content']}]
        return {'draft': planned}

    monkeypatch.setattr(design, 'build_graph', configured)
    session = handle({'action': 'new_session', 'directory': str(tmp_path),
                      'model': 'test', 'credential_id': uid()})

    def act(action, conversation=planner, **fields):
        nonlocal session
        session = handle({'action': action, 'directory': str(tmp_path), 'id': session['id'],
                          'revision': session['revision'], **fields}, conversation=conversation)
        return session

    return act, planner_inputs


def only_question(session, key):
    assert session['status'] == 'needs_clarification'
    question, = session['pending_questions']
    assert question['key'] == key
    assert session['draft']['questions'] == [question['text']]
    return question


def assert_workbook_draft(session, fields, asset):
    assert session['status'] == 'ready', session['issues']
    assert not session['draft']['questions'] and not session['pending_questions']
    nodes = session['draft']['workflow']['nodes']
    assert any(n['tool'] == 'read_document' and asset['id'] in n['settings']['file_ids'] for n in nodes)
    assert any(n['tool'] == 'extract_information' and n['settings']['fields'] == fields for n in nodes)
    assert any(n['tool'] == 'create_spreadsheet' for n in nodes)


@pytest.mark.parametrize('prompt', [REQUEST, EXCEL_REQUEST, SCREENSHOT_REQUEST])
def test_upload_advances_to_fields_and_selection_survives_worker_restart(studio, prompt):
    act, planner_inputs = studio
    upload = only_question(act('message', content=prompt), 'receipt.attachment')
    assert upload['action']['type'] == 'attach_receipt'

    attached = act('upload', asset=RECEIPT, rows=[])
    fields = only_question(attached, 'receipt.fields')
    assert fields['action']['type'] == 'select_fields'
    assert set(fields['action']['options']) >= {'날짜', '상호명', '결제 금액'}
    assert act('session')['pending_questions'] == [fields]
    assert not planner_inputs

    with pytest.raises(ValueError):
        act('message', question_id=upload['id'], content='날짜')
    answer = '영수증에서 추출할 항목: 날짜, 상호명, 승인번호'
    result = act('message', question_id=fields['id'], content=answer)
    assert [m['content'] for m in result['messages'] if m['role'] == 'user'] == [prompt, answer]
    if prompt != REQUEST:
        assert_workbook_draft(result, ['날짜', '상호명', '승인번호'], attached['resources'][0])
        assert not planner_inputs
    else:
        assert planner_inputs[-1]['resources'][0]['id'] == attached['resources'][0]['id']
    assert not any(q['key'].startswith('receipt.') for q in result['pending_questions'])


def test_rejected_receipt_upload_preserves_revision_for_corrected_retry(studio):
    act, _ = studio
    original = act('message', content=REQUEST)
    wrong = {'name': 'report.csv', 'kind': 'table', 'format': 'csv', 'columns': ['상품']}
    with pytest.raises(ValueError, match='PDF|사진|이미지'):
        act('upload', asset=wrong, rows=[])
    restored = act('session')
    assert restored['revision'] == original['revision']
    assert restored['pending_questions'] == original['pending_questions']
    assert not restored['resources']
    only_question(act('upload', asset=RECEIPT, rows=[]), 'receipt.fields')


def test_an_existing_receipt_goes_directly_to_fields(studio):
    act, _ = studio
    act('upload', asset=RECEIPT, rows=[])
    only_question(act('message', content=REQUEST), 'receipt.fields')


def test_library_receipt_follows_the_same_question_and_deletes_from_both_drafts(studio, tmp_path):
    act, _ = studio
    first = act('upload', asset=RECEIPT, rows=[])
    asset_id = first['file_ids'][0]
    def call(action, **fields):
        return handle({'action': action, 'directory': str(tmp_path), **fields}, conversation=lambda _: {})
    second = call('new_session', model='test', credential_id=uid())
    waiting = call('message', id=second['id'], revision=0, content=REQUEST)
    only_question(waiting, 'receipt.attachment')
    reused = call('attach_asset', id=second['id'], revision=waiting['revision'], asset_id=asset_id)
    only_question(reused, 'receipt.fields')
    [asset] = call('assets')['assets']
    assert {u['id'] for u in asset['usages']} == {first['id'], second['id']}
    call('prepare_asset_delete', id=asset_id)
    with pytest.raises(ValueError, match='삭제 중'):
        call('attach_asset', id=first['id'], revision=first['revision'] + 1, asset_id=asset_id)
    call('finish_asset_delete', id=asset_id)
    for session in (first, second):
        assert call('session', id=session['id'])['resources'] == []


def test_deleted_receipt_is_detached_when_reopening_and_can_be_replaced(studio, tmp_path):
    act, _ = studio
    act('message', content=REQUEST)
    session = act('upload', asset=RECEIPT, rows=[])
    removed = session['resources'][0]['id']
    for action in ('prepare_asset_delete', 'finish_asset_delete'):
        handle({'action': action, 'id': removed, 'directory': str(tmp_path)})
    reopened = act('session')
    assert reopened['resources'] == [] and reopened['pending_questions'] == []
    assert reopened['draft'] is None and reopened['revision'] == session['revision'] + 1
    replaced = act('upload', asset={**RECEIPT, 'name': '새 영수증.png'}, rows=[])
    only_question(replaced, 'receipt.fields')
    assert [r['name'] for r in replaced['resources']] == ['새 영수증.png']


def test_already_named_fields_are_passed_to_planning_with_attached_receipt(studio):
    act, planner_inputs = studio
    act('upload', asset=RECEIPT, rows=[])
    request = '영수증에서 날짜와 결제 금액만 추출해줘.'
    act('message', content=request)
    assert planner_inputs and planner_inputs[0]['messages'][0]['content'] == request


@pytest.mark.parametrize('prompt,prior_choices', [
    (REQUEST, []), (EXCEL_REQUEST, OLD_BUTTON_REPLIES[:2]), (EXCEL_REQUEST, OLD_BUTTON_REPLIES),
])
def test_reopened_capability_questions_resume_with_selected_fields(tmp_path, monkeypatch, prompt, prior_choices):
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from langgraph.types import Command
    from service.handlers import design
    from tests.capabilities.test_capability_assessment import packet
    from tests.conditions.test_condition_readiness import Fake, state

    # Create the actual persisted interrupt that an old, still-open chat contains.
    store = Storage(tmp_path)
    session = store.new_session('test', uid())
    old_state = {**state(prompt), 'session_id': session['id']}
    decision = packet(old_state, 'clarify', conditions=[
        '연결된 영수증 파일이 있는지, 그리고 어떤 형식(PDF, 이미지 등)으로 있는지 확인해야 합니다.',
        '내용을 추출할 구체적인 항목 (예: 금액, 날짜, 업체명 등) 을 정의해야 합니다.',
    ])
    with monkeypatch.context() as old:
        old.setenv('A4B_ATTACHMENT_PRECHECK', 'off')
        graph, connection = build_graph(store, conversation=lambda _: pytest.fail('awaiting clarification'),
                                        capability_mode='enforce', capability_transport=Fake([decision]),
                                        condition_mode='off', verifier_mode='off')
        try:
            config = {'configurable': {'thread_id': session['id']}}
            result = graph.invoke(old_state, config)
            for index, choice in enumerate(prior_choices):
                if index == 2:
                    asset = {**RECEIPT, 'id': uid()}
                    store.add_asset(asset, [])
                    session['file_ids'].append(asset['id'])
                    graph.update_state(config, {'resources': [asset]})
                result = graph.invoke(Command(resume={'type': 'message', 'content': choice,
                                     'question_id': result['pending_questions'][0]['id']}), config)
            assert {q['source'] for q in result['pending_questions']} == {'capability'}
            session = store.update_session(session, session['revision'], result)
        finally:
            connection.close()
            store.close()

    request = {'directory': str(tmp_path), 'id': session['id'], 'revision': session['revision']}
    reopened = handle({**request, 'action': 'session'})
    if reopened['resources']:
        attached = reopened
    else:
        only_question(reopened, 'receipt.attachment')
        attached = handle({**request, 'action': 'upload', 'asset': RECEIPT, 'rows': []})
    fields = only_question(attached, 'receipt.fields')
    assert handle({**request, 'action': 'session'})['pending_questions'] == [fields]
    user_turns = [prompt, *prior_choices]
    assert [m['content'] for m in attached['messages'] if m['role'] == 'user'] == user_turns

    capability = Fake([packet(old_state, tools=['read_document', 'extract_information'])])

    def reassess(*args, **kwargs):
        return build_graph(*args, **kwargs, capability_mode='enforce', capability_transport=capability,
                           condition_mode='off', verifier_mode='off', requirement_mode='off')
    monkeypatch.setattr(design, 'build_graph', reassess)
    answer = '영수증에서 추출할 항목: 상호명, 승인번호'
    planner_inputs = []
    def planner(current):
        planner_inputs.append(current)
        return {'draft': draft(['추가할 항목이 있나요?'])}
    resumed = handle({**request, 'action': 'message', 'revision': attached['revision'],
                      'question_id': fields['id'], 'content': answer}, conversation=planner)
    assert resumed['capability_assessment']['decision'] == 'existing_tools'
    assert [m['content'] for m in resumed['messages'] if m['role'] == 'user'] == [*user_turns, answer]
    if prompt == EXCEL_REQUEST:
        assert_workbook_draft(resumed, ['상호명', '승인번호'], attached['resources'][0])
        assert not planner_inputs
    else:
        assert planner_inputs and capability.calls == 1


@pytest.mark.parametrize('already_uploaded', [False, True])
def test_plain_planner_interrupt_reopens_with_receipt_controls(studio, monkeypatch, already_uploaded):
    from service.handlers import design
    act, planner_inputs = studio

    def old_planner(state):
        planned = draft(SCREENSHOT_QUESTIONS)
        planned.update(name='영수증 엑셀 정리', purpose=SCREENSHOT_REQUEST, instructions=SCREENSHOT_REQUEST)
        planned['requirements'] = [{'source_id': 'user', 'description': '요청', 'quote': SCREENSHOT_REQUEST}]
        reply = '아래 항목을 확인해 주세요.\n' + '\n'.join('• ' + q for q in SCREENSHOT_QUESTIONS)
        return {'draft': planned, 'messages': [*state['messages'],
                {'role': 'assistant', 'content': reply}]}

    # Persist the old planner's plain draft, including its await_input interrupt and
    # the possible subsequent upload. These sessions had no structured question IDs.
    with monkeypatch.context() as old:
        old.setenv('A4B_ATTACHMENT_PRECHECK', 'off')
        old.setattr(design, 'receipt_upload_prompt', lambda *_: None)
        act('message', content=SCREENSHOT_REQUEST, conversation=old_planner)
        if already_uploaded:
            act('upload', asset={**RECEIPT, 'name': 'receipt.jpg', 'format': 'jpg'}, rows=[])

    reopened = act('session')
    assert SCREENSHOT_QUESTIONS[0] in reopened['messages'][-1]['content']
    if not already_uploaded:
        only_question(reopened, 'receipt.attachment')
        reopened = act('upload', asset=RECEIPT, rows=[])
    fields = only_question(reopened, 'receipt.fields')
    assert '엑셀' in fields['text']
    answer = '영수증에서 추출할 항목: 날짜, 상호명, 결제 금액'
    with pytest.raises(ValueError):
        act('message', content=answer, question_id='outdated-field-question')
    unchanged = act('session')
    assert unchanged['revision'] == reopened['revision']
    assert unchanged['messages'] == reopened['messages']
    assert unchanged['pending_questions'] == [fields]
    advanced = act('message', content=answer, question_id=fields['id'])
    assert_workbook_draft(advanced, ['날짜', '상호명', '결제 금액'], reopened['resources'][0])
    assert not planner_inputs


def test_legacy_planner_migration_keeps_an_independent_question():
    from agent_builder.design.receipt_intake import legacy_receipt_intake

    session = {'status': 'needs_clarification', 'pending_questions': [],
               'messages': [{'role': 'user', 'content': SCREENSHOT_REQUEST}],
               'draft': {'questions': [*SCREENSHOT_QUESTIONS, '취소한 거래도 포함할까요?']}}
    assert legacy_receipt_intake(session) is None


@pytest.mark.parametrize('run_uploads', [False, True], ids=['saved-sample', 'new-receipts'])
def test_file_only_run_exports_selected_fields_of_the_requested_receipts(studio, tmp_path, monkeypatch, run_uploads):
    import base64
    import io
    import json
    from openpyxl import load_workbook
    from workflow_engine.executors.schemas import Facts

    class ReceiptFacts:
        def with_structured_output(self, schema, **kwargs):
            assert schema is Facts
            return self

        def invoke(self, messages):
            doc = json.loads(messages[-1]['content'])['documents'][0]
            evidence = dict(line.split(': ', 1) for line in doc['text'].splitlines())
            evidence['승인번호'] = '없는번호'  # Deliberately ungrounded; the output cell must be empty.
            return Facts.model_validate({'facts': [
                {'field': field, 'value': value, 'source_id': doc['source_id'], 'quote': field + ': ' + value}
                for field, value in evidence.items()
            ]})

    act, _ = studio
    act('message', content=SCREENSHOT_REQUEST)
    asset = {**RECEIPT, 'text': '날짜: 2026-11-05\n' + RECEIPT['text']}
    attached = act('upload', asset=asset, rows=[])
    question = only_question(attached, 'receipt.fields')
    fields = ['날짜', '상호명', '결제 금액', '승인번호']
    ready = act('message', content='영수증에서 추출할 항목: ' + ', '.join(fields), question_id=question['id'])
    assert_workbook_draft(ready, fields, attached['resources'][0])
    saved = act('confirm')
    job = {'directory': str(tmp_path), 'id': saved['saved_agent_id']}
    assert handle({**job, 'action': 'agent'})['request_required'] is False
    monkeypatch.setattr('workflow_engine.runner.create_chat_model', lambda **_: ReceiptFacts())
    expected = [('2026-11-05', 'A4B 상점', '12,000원', None)]
    file_ids = []
    if run_uploads:
        wrong = handle({**job, 'action': 'run_upload', 'asset': {'name': 'bad.csv', 'kind': 'table',
                       'format': 'csv', 'columns': ['금액']}, 'rows': [{'금액': 1000}]})['asset']
        with pytest.raises(ValueError, match='자료|문서'):
            handle({**job, 'action': 'test', 'file_ids': [wrong['id']]})
        assert not handle({**job, 'action': 'agent'})['tests']
        expected = [('2026-12-02', '빵집', '3,000원', None), ('2026-12-03', '서점', '18,000원', None)]
        for index, (date, shop, amount, _) in enumerate(expected):
            asset = {**RECEIPT, 'name': f'replacement-{index}.png',
                     'text': f'날짜: {date}\n상호명: {shop}\n결제 금액: {amount}'}
            uploaded = handle({**job, 'action': 'run_upload', 'asset': asset, 'rows': []})
            file_ids.append(uploaded['asset']['id'])
    content = {} if run_uploads else {'content': '  \n'}
    run = handle({**job, 'action': 'test', 'file_ids': file_ids, **content})
    assert run['status'] == 'succeeded', run.get('error')
    assert run['answer'] == ''
    # A workflow may still bind this raw text port, while people get a download.
    assert json.loads(run['outputs']['excel']['text'])['artifact'] == run['outputs']['excel']['asset']
    assert run['file_ids'] == file_ids
    persisted = handle({**job, 'action': 'run', 'id': run['id']})['run']
    assert persisted['request'] == ''
    assert not [m for m in persisted['messages'] if m['role'] == 'user']
    workbook = load_workbook(io.BytesIO(base64.b64decode(run['artifact']['content'])))
    try:
        assert list(workbook.active.values) == [tuple(fields), *expected]
    finally:
        workbook.close()


@pytest.mark.parametrize('prompt', [
    '청구서 PDF에서 내용을 추출해 줘.',
    '매일 들어오는 영수증의 금액을 추출해서 담당자에게 메일로 보내줘.',
    '영수증과 발주서의 금액을 추출해서 엑셀에 비교해줘.',
])
def test_other_document_and_scheduled_requests_stay_with_the_planner(studio, prompt):
    act, planner_inputs = studio
    act('message', content=prompt)
    assert planner_inputs and planner_inputs[0]['messages'][0]['content'] == prompt


@pytest.mark.parametrize('later_prompt,question_source', [
    ('영수증 추출을 취소해줘.', 'capability'),
    ('영수증과 장부를 비교해줘.', 'capability'),
    (None, 'condition'),
])
def test_unrelated_pending_questions_keep_their_original_controls(later_prompt, question_source):
    from agent_builder.design.receipt_intake import legacy_receipt_intake

    user_inputs = [EXCEL_REQUEST, OLD_BUTTON_REPLIES[0]]
    if later_prompt:
        user_inputs.append(later_prompt)
    user_inputs.append(OLD_BUTTON_REPLIES[-1])
    session = {'messages': [{'role': 'user', 'content': text} for text in user_inputs],
               'status': 'needs_clarification', 'pending_questions': [
                   {'id': 'file', 'source': 'capability', 'text': '파일 형식은 무엇인가요?'},
                   {'id': 'fields', 'source': question_source, 'text': '필요한 항목은 무엇인가요?'},
               ]}
    assert legacy_receipt_intake(session) is None
