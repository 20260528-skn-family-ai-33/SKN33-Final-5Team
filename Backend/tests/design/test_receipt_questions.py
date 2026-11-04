"""Exercise receipt clarification through the same persisted worker actions as the studio."""
import pytest

from infrastructure.persistence.ids import uid
from service.worker import handle
from tests.design.test_builder import draft


REQUEST = '영수증 파일 올려주면 내용 추출해줄래?'
EXCEL_REQUEST = '영수증 올려주면 내용 추출해서 엑셀에 정리해줄 수 있어?'
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

    def act(action, **fields):
        nonlocal session
        session = handle({'action': action, 'directory': str(tmp_path), 'id': session['id'],
                          'revision': session['revision'], **fields}, conversation=planner)
        return session

    return act, planner_inputs


def only_question(session, key):
    assert session['status'] == 'needs_clarification'
    question, = session['pending_questions']
    assert question['key'] == key
    assert session['draft']['questions'] == [question['text']]
    return question


@pytest.mark.parametrize('prompt', [REQUEST, EXCEL_REQUEST])
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
    for messages in (result['messages'], planner_inputs[-1]['messages']):
        assert [m['content'] for m in messages if m['role'] == 'user'] == [prompt, answer]
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
    assert capability.calls == 1
    assert [m['content'] for m in planner_inputs[0]['messages'] if m['role'] == 'user'] == [*user_turns, answer]


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
