from copy import deepcopy

import pytest

from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from tests.design.test_direct_conversation import draft,state
from workflow_engine.scheduling.schedules import Schedules


@pytest.mark.parametrize('include',[True,False])
def test_final_selection_only_controls_reservation_and_preserves_the_workflow(tmp_path,include):
    store=Storage(tmp_path)
    try:
        session=store.new_session('test','local')
        current=state()
        for resource in current['resources']:store.add_asset(resource,[])
        text='매일 오전 9시에 입력 표를 JSON으로 저장해줘.'
        candidate=deepcopy(draft())
        candidate['tool_ids']=['read_table','write_result']
        candidate['name']='일일 자료 정리'
        candidate['requirements'][0]['quote']=text
        current.update(session_id=session['id'],messages=[{'role':'user','content':text}],
            model='test',credential_id='local',draft=candidate,execution_mode=None)
        checked=validate_node(current)
        assert checked['status']=='ready',checked['issues']
        assert checked['draft']['schedule']['hour']==9
        original=deepcopy(checked['draft'])
        saved=make_save_node(store)({**current,**checked,'confirmed':True,'include_schedule':include})
        config=store.agent(saved['saved_agent_id'])['config']
        assert config['workflow']==original['workflow']
        assert config['requirements']==original['requirements']
        reservation=Schedules(store).get(saved['saved_agent_id'])
        if include:
            assert config['schedule']['hour']==9 and config['schedule']['minute']==0
            assert reservation and reservation['active'] is False
        else:
            assert config['schedule'] is None and reservation is None
            assert saved['messages'][-1]['content']=='예약은 추가하지 않고 워크플로우만 저장합니다.'
        assert saved['draft']==config
    finally:store.close()


@pytest.mark.parametrize('text,expected',[
    ('매일 09:00에 재고를 정리해줘.',(9,0)),
    ('매주 월요일 18:25에 문서를 정리해줘.',(18,25)),
    ('매월 말일 정오에 비용을 정리해줘.',(12,0)),
    ('매일 자정에 결과를 정리해줘.',(0,0)),
])
def test_explicit_clock_forms_are_evidence_across_domains(text,expected):
    from agent_builder.design.execution_mode import requested_schedule
    result=requested_schedule({'messages':[{'role':'user','content':text}],'execution_mode':None})
    assert result and (result['hour'],result['minute'])==expected


def test_query_boundary_clock_does_not_replace_the_execution_clock():
    from agent_builder.design.execution_mode import requested_schedule
    result=requested_schedule({'messages':[{'role':'user','content':'매일 오전 9시에 오후 6시까지 받은 메일을 요약해줘.'}]})
    assert result and (result['hour'],result['minute'])==(9,0)


def test_output_filename_weekday_is_not_a_schedule_update():
    from agent_builder.conditions.condition_readiness import schedule_literals
    assert schedule_literals('출력 파일 이름은 화요일 보고서로 해줘.',follow_up=True)==[]


def test_save_uses_the_validated_clock_in_a_legacy_ready_checkpoint():
    text='매일 오전 9시에 입력 표를 JSON으로 저장해줘.'
    candidate=deepcopy(draft())
    candidate.update(name='일일 자료 정리',tool_ids=['read_table','write_result'],
        schedule={'frequency':'daily','hour':10,'minute':0,'weekday':None,'timezone':'Asia/Seoul'},report_destination='reports')
    candidate['requirements'][0]['quote']=text
    current={**state(),'messages':[{'role':'user','content':text}],'draft':candidate,
        'execution_mode':None,'confirmed':True,'status':'ready'}
    class Capture:
        def save_agent(self,value):self.saved=value['draft'];return 'saved-id'
        def agent(self,identifier):return {'config':self.saved}
    store=Capture()
    result=make_save_node(store)(current)
    assert store.saved['schedule']['hour']==9
    assert result['draft']['schedule']['hour']==9
    assert candidate['schedule']['hour']==10
