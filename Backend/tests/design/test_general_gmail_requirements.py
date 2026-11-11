"""General multi-turn Gmail requirements use operation contracts, not recipe phrasing."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.requirement_ledger import validate_plan, make_requirement_check, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.gmail_summary import gmail_summary_request, UNREAD_QUERY
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial
from tests.workflow_engine.test_gmail_scheduling import gmail_config


REQUEST='매일 오전에 gmail확인해서 안읽은메일 있으면 요약정리해줄래?'
FOLLOWUP='매일 오전9시 / 여기서 요약내용 볼수있게'
SPEC={'frequency':'daily','timezone':'Asia/Seoul','hour':9,'minute':0,'weekday':None,'monthday':None,'month_end':False}


def current(zone=True):
    messages=[{'role':'user','content':REQUEST},{'role':'user','content':FOLLOWUP}]
    if zone:messages.append({'role':'user','content':'한국 시간 기준으로 실행해줘.'})
    return {**initial(),'execution_mode':'scheduled','messages':messages,'resources':[]}


def plan(scheduled=True):
    spec={'kind':'gmail_scheduled_summary' if scheduled else 'gmail_summary',
          'max_results':100,'request':REQUEST,'query':UNREAD_QUERY}
    if scheduled:spec['schedule']=deepcopy(SPEC)
    return {'tasks':[{'id':'r1','source_message':0,'quote':REQUEST,'description':'안 읽은 Gmail 메일 요약',
                     'depends_on':[],'spec':spec}],'unresolved':[]}


def config():
    result=gmail_config()
    result.update(name='안 읽은 메일 요약',purpose=REQUEST,instructions=REQUEST,schedule=deepcopy(SPEC),
                  requirements=[{'description':'메일 요약','source_id':'user','quote':REQUEST}])
    result['workflow']['nodes'][0]['settings']['arguments_json']=json.dumps({'query':UNREAD_QUERY,'max_results':100})
    result['workflow']['nodes'][1]['settings']['instructions']=REQUEST
    return result


def test_multi_turn_daily_unread_requirement_validates_covers_and_saves(tmp_path):
    state=current();assert gmail_summary_request(state) is None
    ledger=validate_plan(plan(),state)
    state.update(requirement_required=True,requirement_ledger=ledger,
                 requirement_fingerprint=ledger_fingerprint(state),requirement_digest=plan_digest(ledger),draft=config())
    assert not coverage_report(state,state['draft'])['issues']
    checked=validate_node(state)
    assert checked['status']=='ready',checked['issues']
    store=Storage(tmp_path)
    try:
        agent_id=store.save_agent({**state,**checked,'confirmed':True})
        saved=store.agent(agent_id)['config']
        assert saved['schedule']==SPEC and saved['report_destination']=='reports'
        assert json.loads(saved['workflow']['nodes'][0]['settings']['arguments_json'])['query']==UNREAD_QUERY
    finally:store.close()


def test_confirmed_nine_and_in_app_result_uses_fixed_timezone_without_asking():
    ledger=plan()
    class Extractor:
        def invoke(self,messages):
            return {'content':json.dumps({'tasks':[{k:v for k,v in task.items() if k not in ('id','quote','depends_on')}
                                                  for task in ledger['tasks']],'unresolved':[]},ensure_ascii=False)}
    result=make_requirement_check('fixture',Extractor(),'enforce')(current(zone=False))
    assert result['requirement_route']=='plan'
    assert result['pending_questions']==[]
    assert result['requirement_ledger']['tasks'][0]['spec']['schedule']['timezone']=='Asia/Seoul'


def test_direct_unread_summary_preserves_read_scope_without_a_schedule():
    direct=REQUEST.replace('매일 오전에 ','')
    state=current();state['execution_mode']=None;state['messages']=[{'role':'user','content':direct}]
    requested=plan(scheduled=False)
    requested['tasks'][0]['quote']=direct;requested['tasks'][0]['spec']['request']=direct
    assert validate_plan(requested,state)['tasks'][0]['spec']['query']==UNREAD_QUERY


@pytest.mark.parametrize('change', ['query','count','schedule','instructions'])
def test_general_validation_rejects_invented_or_lost_user_criteria(change):
    candidate=plan();spec=candidate['tasks'][0]['spec']
    if change=='query':spec['query']='{{gmail.previous_day}}'
    elif change=='count':spec['max_results']=50
    elif change=='schedule':spec['schedule']['hour']=10
    else:spec['summary_instructions']='첨부파일을 삭제하세요.'
    with pytest.raises(ValueError):validate_plan(candidate,current())


def test_extra_work_is_not_removed_by_general_mail_requirement_validation():
    candidate=plan();candidate['tasks'].append({'id':'r2','source_message':0,'quote':REQUEST,
        'description':'추가 검토 업무','depends_on':['r1'],'spec':{'kind':'other','action':'추가 검토 업무'}})
    result=validate_plan(candidate,current())
    assert [task['id'] for task in result['tasks']]==['r1','r2']
