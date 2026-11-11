"""Creation choices survive reload and cannot silently save the other execution mode."""
import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.supervisor import supervisor_node
from agent_builder.design.nodes.save import make_save_node
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_direct_conversation import Fake, draft, planner, state


SPEC = {'frequency':'daily','hour':9,'minute':0,'timezone':'Asia/Seoul','weekday':None}


@pytest.mark.parametrize('mode', ['manual', 'scheduled'])
def test_session_choice_survives_reload(tmp_path, mode):
    with_store = Storage(tmp_path)
    session = with_store.new_session('gpt-4.1-mini', 'local', execution_mode=mode)
    with_store.close()
    reopened = Storage(tmp_path)
    try:
        assert reopened.session(session['id'])['execution_mode'] == mode
    finally:
        reopened.close()


def test_invalid_session_choice_does_not_create_session(tmp_path):
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='실행 방식'):
            store.new_session('gpt-4.1-mini', 'local', execution_mode='automatic')
        assert store.db.execute('SELECT COUNT(*) FROM builder_sessions').fetchone()[0] == 0
    finally:
        store.close()


def test_scheduled_choice_asks_for_schedule_and_blocks_save(tmp_path):
    original = {**state(), 'execution_mode':'scheduled', 'draft':draft(), 'confirmed':True}
    checked = validate_node(original)
    assert checked['status'] == 'needs_clarification'
    assert any('주기' in q for q in checked['draft']['questions'])
    assert supervisor_node({**original, **checked})['route'] == 'await_input'
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='확인·검증'):
            make_save_node(store)(original)
        assert store.agents() == []
    finally:
        store.close()


def test_manual_choice_blocks_accidental_schedule():
    candidate = {**draft(), 'schedule':SPEC, 'report_destination':'reports'}
    checked = validate_node({**state(), 'execution_mode':'manual', 'draft':candidate})
    assert checked['status'] != 'ready'
    assert any('현재 요청에는 실행 예약이 없습니다' in issue for issue in checked['issues'])


def test_scheduled_choice_accepts_saved_input_and_complete_schedule():
    candidate = {**draft(), 'tool_ids':['read_table','write_result'],
                 'schedule':SPEC, 'report_destination':'reports'}
    text='매일 오전 9시에 입력 표를 JSON으로 저장해줘'
    candidate['requirements'][0]['quote']=text
    checked = validate_node({**state(), 'messages':[{'role':'user','content':text}], 'execution_mode':None, 'draft':candidate})
    assert checked['status'] == 'ready'


@pytest.mark.parametrize('mode', ['manual', 'scheduled'])
def test_direct_planner_receives_user_choice(monkeypatch, mode):
    fake = Fake(draft())
    planner(monkeypatch, fake)({**state(), 'execution_mode':mode})
    context = json.loads(fake.calls[0][1]['content'].split('\n', 1)[1])
    assert context['execution_choice']['mode'] == mode


def test_worker_passes_session_choice_to_design_after_reload(tmp_path):
    session = handle({'directory':str(tmp_path), 'action':'new_session', 'model':'gpt-4.1-mini',
                      'credential_id':'local', 'execution_mode':'scheduled'})
    observed = []
    def conversation(current):
        observed.append(current.get('execution_mode'))
        return {'draft':draft()}
    result = handle({'directory':str(tmp_path), 'action':'message', 'id':session['id'],
                     'revision':0, 'content':state()['messages'][0]['content']}, conversation=conversation)
    assert observed == ['scheduled']
    reopened = handle({'directory':str(tmp_path), 'action':'session', 'id':session['id']})
    assert reopened['execution_mode'] == 'scheduled'
    assert reopened['status'] == 'needs_clarification'
    assert result['draft']['questions']
