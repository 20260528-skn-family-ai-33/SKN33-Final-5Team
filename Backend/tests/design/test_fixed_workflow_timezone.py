from copy import deepcopy

import pytest

from contracts.models import ScheduleSpec
from agent_builder.design.requirement_clarification import business_questions, missing_schedule_questions, schedule_facts
from agent_builder.design.requirement_ledger import grounded_schedule
from workflow_engine.scheduling.policy import project_fixed_timezone
from workflow_engine.scheduling.schedules import scheduled_agent


@pytest.mark.parametrize('timezone',[None,'UTC','America/New_York','Asia/Seoul'])
def test_schedule_input_always_uses_seoul(timezone):
    spec={'frequency':'daily','hour':9,'minute':30,'weekday':None,'timezone':timezone}
    assert ScheduleSpec.model_validate(spec).timezone=='Asia/Seoul'
    spec.pop('timezone')
    assert ScheduleSpec.model_validate(spec).timezone=='Asia/Seoul'


def test_schedule_grounding_uses_application_timezone_without_asking_user():
    current={'execution_mode':'scheduled','messages':[{'role':'user','content':'매일 오전 9시 30분에 실행해줘.'}],'resources':[]}
    assert schedule_facts(current)['schedule.timezone']=='Asia/Seoul'
    assert grounded_schedule(current)['timezone']=='Asia/Seoul'
    assert all(question['key']!='schedule.timezone' for question in missing_schedule_questions(current))
    assert business_questions(['schedule.timezone'],current)[0]==[]


def test_legacy_timezone_controls_are_removed_but_other_questions_stay_aligned():
    current={'draft':{'questions':['어느 시간대를 기준으로 실행할까요?','몇 시에 실행할까요?'],
                      'question_options':[['서울','UTC'],[]], 'question_responses':[{'type':'single_choice'},{'type':'number'}]},
             'pending_questions':[{'id':'timezone','key':'schedule.timezone','text':'어느 시간대인가요?'},
                                  {'id':'time','key':'schedule.hour','text':'몇 시에 실행할까요?'}]}
    before=deepcopy(current);shown=project_fixed_timezone(current)
    assert [question['id'] for question in shown['pending_questions']]==['time']
    assert shown['draft']['questions']==['몇 시에 실행할까요?']
    assert shown['draft']['question_options']==[[]]
    assert shown['draft']['question_responses']==[{'type':'number'}]
    assert current==before


def test_existing_job_snapshot_uses_seoul_without_editing_workflow():
    workflow={'id':'workflow','config':{'name':'업무'}}
    result=scheduled_agent(workflow,{'frequency':'daily','hour':9,'minute':0,'timezone':'UTC'})
    assert result['config']['schedule']['timezone']=='Asia/Seoul'
    assert 'schedule' not in workflow['config']


def test_legacy_reservation_is_normalized_with_a_matching_next_slot(tmp_path):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from infrastructure.persistence.storage import Storage
    from workflow_engine.scheduling.schedules import Schedules
    store=Storage(tmp_path)
    try:
        Schedules(store)
        old={'agent_id':'legacy','agent_version':1,'spec':{'frequency':'daily','hour':9,'minute':30,'weekday':None,'timezone':'UTC'}}
        store.db.execute('INSERT INTO builder_schedules(agent_id,json,next_at,active) VALUES(?,?,?,?)',('legacy',json.dumps(old),'2001-01-01T09:30:00+00:00',1));store.db.commit()
        result=Schedules(store).get('legacy')
        local=datetime.fromisoformat(result['next_run_at']).astimezone(ZoneInfo('Asia/Seoul'))
        assert result['spec']['timezone']=='Asia/Seoul'
        assert (local.hour,local.minute)==(9,30)
        assert json.loads(store.db.execute('SELECT json FROM builder_schedules WHERE agent_id=?',('legacy',)).fetchone()[0])['spec']['timezone']=='Asia/Seoul'
    finally:store.close()
