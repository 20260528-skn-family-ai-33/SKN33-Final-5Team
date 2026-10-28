import json
import pytest
from agent_builder.worker import handle
from agent_builder.storage import uid,Storage
from agent_builder.test_tool_gap import session_gap,REQUEST
from agent_builder.test_tool_gap_choice import select,job_claim,job_result
from agent_builder.capability_judgement import assess_capability

class Fake:
    def __init__(self,value):self.value=value;self.calls=0
    def invoke(self,messages):self.calls+=1;return {'content':json.dumps(self.value)}

def test_capability_never_turns_input_fault_into_missing_tool():
    f=Fake({})
    result=assess_capability({'source_health':{'db_status':'timeout','messages_loaded':False},'resources':[]},f)
    assert result['decision']=='system_error' and f.calls==0

@pytest.mark.parametrize('change',[{'tool_ids':['made_up']},{'user_quotes':['없는 원문']},{'missing_actions':['메일']}])
def test_capability_validation_does_not_grant_execution(change):
    text='부서별 비용 합계를 집계해줘.'
    value={'decision':'existing_tools','tool_ids':['aggregate_table'],'missing_conditions':[],
        'missing_actions':[],'reason':'집계 도구','user_quotes':[text],**change}
    with pytest.raises(ValueError):assess_capability({'messages':[{'role':'user','content':text}],'resources':[]},Fake(value))

def propose(tmp_path,s,alternative=None):
    return handle({'directory':str(tmp_path),'id':s['id'],'revision':s['revision'],'action':'tool_gap_replan_propose',
        'alternative':alternative or {'summary':'원문 전화번호를 그대로 파일로 저장하는 대안',
        'proposed_request':'phone 열의 원문을 변환하지 않고 JSON 파일로 저장해줘.',
        'tool_ids':['read_table','write_result'],'changes':[{'original_quote':REQUEST,
        'proposed_change':'공백·하이픈 제거를 생략하고 원문을 보존'}],
        'limitations':['원래 요구한 정규화가 수행되지 않으므로 사용자 확인 필요']}})

def decide(tmp_path,s,decision,**extra):
    return handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_replan_decide','revision':s['revision'],
        'proposal_id':s['redesign_proposal']['id'],'request_id':uid(),'decision':decision,**extra})


def test_redesign_proposal_and_acceptance_preserve_original_and_require_new_draft_review(tmp_path):
    original=session_gap(tmp_path);s=select(tmp_path,original,'redesign_workflow');s=propose(tmp_path,s)
    assert s['tool_gap_choice']['status']=='redesign_requested'
    assert s['redesign_proposal']['status']=='awaiting_confirmation' and s['redesign_generation'] is None
    before=s;s=decide(tmp_path,s,'accept')
    assert s['redesign_generation']['effective_request'].startswith('phone')
    assert s['redesign_generation']['requires_condition_recheck']
    assert not s['redesign_generation']['allows_save'] and not s['redesign_generation']['allows_execution']
    assert s['messages']==original['messages'] and s['draft']==original['draft']
    reopened=handle({'directory':str(tmp_path),'id':s['id'],'action':'session'})
    assert reopened['redesign_generation']==s['redesign_generation']
    with pytest.raises(ValueError):decide(tmp_path,before,'accept')
    with pytest.raises(ValueError):job_claim(tmp_path,s)

@pytest.mark.parametrize('decision',['reject','defer'])
def test_redesign_reject_or_defer_does_not_enable_generator(tmp_path,decision):
    s=propose(tmp_path,select(tmp_path,session_gap(tmp_path),'redesign_workflow'))
    s=decide(tmp_path,s,decision)
    assert s['redesign_generation'] is None and not s['redesign_proposal']['requirement_changes_approved']
    if decision=='defer':assert decide(tmp_path,s,'accept')['redesign_generation']


def test_redesign_requires_current_choice_and_quotes_and_catalog(tmp_path):
    s=session_gap(tmp_path)
    with pytest.raises(ValueError):propose(tmp_path,s)
    s=select(tmp_path,s,'redesign_workflow');p=propose(tmp_path,s)
    a=p['redesign_proposal']['alternative']
    for bad in [{**a,'tool_ids':['made_up']},{**a,'changes':[{'original_quote':'없는말','proposed_change':'수정'}]},{**a,'changes':[]}]:
        with pytest.raises(ValueError):propose(tmp_path,p,bad)
    s=select(tmp_path,p,'create_tool')
    assert s['redesign_proposal'] is None and s['redesign_generation'] is None


def test_expired_owner_is_recovered_and_cannot_publish_late_success(tmp_path,monkeypatch):
    import agent_builder.tool_gap_jobs as jobs
    s=select(tmp_path,session_gap(tmp_path),'create_tool');claimed=job_claim(tmp_path,s);job=claimed['job']
    assert 'lease_token' not in job
    reopened=handle({'directory':str(tmp_path),'id':s['id'],'action':'session'})
    assert 'lease_token' not in reopened['tool_gap_job']
    future=job['lease_expires_at']+1;monkeypatch.setattr(jobs.time,'time',lambda:future)
    result=handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_recover'})
    assert result['recovered'][0]['status']=='interrupted' and not result['automatic_retry']
    with pytest.raises(ValueError):
        handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_finish',
          'job_id':job['id'],'lease_token':claimed['lease_token'],'result':job_result()})
    assert not job_claim(tmp_path,s)['claimed']
    fresh=select(tmp_path,s,'create_tool')
    assert job_claim(tmp_path,fresh)['claimed']


def test_job_finish_requires_owner_token(tmp_path):
    s=select(tmp_path,session_gap(tmp_path),'create_tool');claimed=job_claim(tmp_path,s)
    with pytest.raises(ValueError):
        handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_finish',
          'job_id':claimed['job']['id'],'lease_token':uid(),'result':job_result()})

def test_redesign_decision_retry_and_invalidated_after_upload(tmp_path):
    s=propose(tmp_path,select(tmp_path,session_gap(tmp_path),'redesign_workflow'))
    request=uid();old=s
    accepted=decide(tmp_path,s,'accept',request_id=request)
    assert decide(tmp_path,old,'accept',request_id=request)['revision']==accepted['revision']
    s=handle({'directory':str(tmp_path),'id':accepted['id'],'action':'upload','revision':accepted['revision'],
        'asset':{'name':'new.csv','kind':'table','columns':['phone'],'profile':{}},'rows':[{'phone':'12345678'}]})
    assert s['redesign_proposal'] is None and s['redesign_generation'] is None

def test_optional_capability_path_records_gap_and_default_off_does_not_call(tmp_path):
    from agent_builder.nodes.capability_check import make_capability_check
    from agent_builder.test_builder import initial
    state=initial();never=Fake({})
    assert make_capability_check('test',never,mode='off')(state)['capability_route']=='conditions'
    assert never.calls==0
    text=state['messages'][0]['content']
    fake=Fake({'decision':'missing_capability','tool_ids':[],'missing_conditions':[],
        'missing_actions':['외부 발송'],'reason':'목록에 없는 발송','user_quotes':[text]})
    result=make_capability_check('test',fake,mode='enforce')(state)
    assert result['status']=='unsupported' and result['capability_route']=='supervisor'
    assert not result['confirmed'] and result['draft']['unsupported']

def test_capability_input_fault_needs_no_model_transport(monkeypatch):
    from agent_builder.nodes.capability_check import make_capability_check
    monkeypatch.delenv('A4B_MAIN_PROVIDER',raising=False)
    result=make_capability_check('none',mode='enforce')({'source_health':{'db_status':'failed','messages_loaded':False},'resources':[]})
    assert result['capability_route']=='stop' and result['capability_assessment']['model_calls']==0


def test_native_schedule_metadata_is_passed_without_fake_tool_or_permission():
    from agent_builder.capability_judgement import APP_CAPABILITIES
    text='한국 시간 매주 화요일 8시에 표를 읽고 CSV로 저장해줘.'
    class Capture(Fake):
        def invoke(self,messages):
            self.messages=messages
            return super().invoke(messages)
    f=Capture({'decision':'existing_tools','tool_ids':['read_table','write_result'],
        'missing_conditions':[],'missing_actions':[],'reason':'앱 주간 예약과 등록 도구', 'user_quotes':[text]})
    result=assess_capability({'messages':[{'role':'user','content':text}],'resources':[]},f)
    body=json.loads(f.messages[1]['content'])
    assert body['app_capabilities']==APP_CAPABILITIES
    assert body['app_capabilities'][0]['supported_frequencies']==['daily','weekly']
    assert all(t['id']!='workflow_schedule' for t in body['tools'])
    assert not result['allows_generation'] and not result['allows_execution']
    assert result['tool_ids']==['read_table','write_result']

@pytest.mark.parametrize('label',['매주 예약 (weekly schedule)','매주 금요일 10시 주간 예약','daily schedule'])
def test_known_native_claim_is_removed_without_hiding_model_output(label):
    from agent_builder.capability_judgement import normalize_native_claims
    raw={'decision':'missing_capability','missing_actions':[label,'외부 Slack 전송'],'reason':'모델 설명'}
    checked=normalize_native_claims(raw)
    assert checked['missing_actions']==['외부 Slack 전송']
    assert checked['model_missing_actions']==raw['missing_actions']
    assert checked['native_app_claims_removed']==[label]
    assert checked['decision']=='missing_capability'
    assert normalize_native_claims({**raw,'missing_actions':[label]})['decision']=='manual_review'

@pytest.mark.parametrize('label',['매주 Slack 전송','월간 예약','매주 외부 자료 갱신','cron schedule','예약 활성화', '매일 예약과 이메일 발송'])
def test_native_claim_check_never_removes_other_unsupported_actions(label):
    from agent_builder.capability_judgement import normalize_native_claims
    raw={'decision':'missing_capability','missing_actions':[label],'reason':'모델 설명'}
    assert normalize_native_claims(raw)==raw
