import json
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes import await_input
from agent_builder.design.requirement_ledger import make_requirement_check, grounded_schedule, validate_plan
from agent_builder.design.requirement_clarification import business_questions
from agent_builder.design.requirement_clarification import clarification_reply
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial


REQUEST='매일 오전에 gmail에서 안읽은 메일을 요약 정리해서 알려줄래?'


def current(content=REQUEST,mode='scheduled'):
    return {**initial(),'execution_mode':mode,'messages':[{'role':'user','content':content}]}


def raw(unresolved):
    return {'tasks':[{'source_message':0,'description':'메일 내용 요약','spec':{'kind':'other','action':'메일 내용을 요약'}}],
            'unresolved':unresolved}


class Extractor:
    def __init__(self,value):self.value=value;self.calls=[]
    def invoke(self,messages):
        self.calls.append(deepcopy(messages))
        return {'content':json.dumps(self.value,ensure_ascii=False)}


def test_exact_gmail_request_asks_actual_schedule_questions_instead_of_internal_credentials():
    state=current();state.update(gmail_connected=True,gmail_access_token='private-runtime-token')
    model=Extractor(raw(['gmail_access_token','gmail_account_id','summary_format_preference']))
    result=make_requirement_check('fixture',model,'enforce')(state)
    assert result['status']=='needs_clarification'
    assert result['requirement_route']=='await_input'
    assert [q['key'] for q in result['pending_questions']]==['schedule.time']
    assert result['pending_questions'][0]['response']['type']=='time'
    assert all(q['source']=='requirements' and len(q['id'])==24 for q in result['pending_questions'])
    visible=json.dumps(result,ensure_ascii=False)
    assert 'gmail_access_token' not in visible and 'gmail_account_id' not in visible
    assert 'summary_format_preference' not in visible and 'private-runtime-token' not in visible
    payload=json.loads(model.calls[0][1]['content'])
    assert payload['service_connections']['gmail']['connected'] is True
    assert 'private-runtime-token' not in json.dumps(model.calls)
    assert '9시' not in visible


def test_unrequested_summary_preference_and_credentials_do_not_block_manual_work():
    model=Extractor(raw(['gmail_access_token','gmail_account_id','summary_format_preference']))
    result=make_requirement_check('fixture',model,'enforce')(current('Gmail 메일 내용을 요약해줘.','manual'))
    assert result['requirement_route']=='plan'
    assert result['requirement_ledger']['unresolved']==[]


def test_explicit_summary_format_choice_has_customer_labels():
    questions,unknown=business_questions(['summary_format_preference'],current('메일 요약 형식을 선택하고 싶어요.','manual'))
    assert not unknown and questions[0]['response']=={'type':'single_choice'}
    assert questions[0]['options']==['메일별 핵심 요약','전체 메일을 묶어 요약']


def test_unknown_internal_name_is_retried_then_presented_as_a_normal_question():
    model=Extractor(raw(['invented_customer_internal_field']))
    result=make_requirement_check('fixture',model,'enforce')(current('메일 요약해줘.','manual'))
    assert len(model.calls)==2
    assert result['status']=='validation_failed'
    assert not result.get('pending_questions')
    assert result['requirement_failure']=={'kind':'processing_error','retryable':True}
    assert 'invented_customer_internal_field' not in json.dumps(result)


def test_real_natural_language_business_question_is_preserved():
    text='어떤 고객을 대상으로 보고서를 만들까요?'
    result=make_requirement_check('fixture',Extractor(raw([text])),'enforce')(current('고객 보고서를 만들어줘.','manual'))
    assert result['draft']['questions']==[text]
    assert result['pending_questions'][0]['text']==text


def test_declared_question_controls_and_groups_survive_without_domain_rewriting():
    state=current('보고서 입력을 확인하고 싶어요.','manual')
    declared=[{'key':f'field.{index}','text':f'조건 {index}을 선택하세요.',
               'options':['A','B'],'response':{'type':'single_choice'},'group':'보고서 조건','required':True}
              for index in range(5)]
    questions,unknown=business_questions(declared,state)
    assert not unknown and questions==declared
    result=clarification_reply(state,questions,1)
    assert len(result['pending_questions'])==5
    assert len(result['draft']['questions'])==3
    assert all(q['group']=='보고서 조건' and q['required'] is True for q in result['pending_questions'])


@pytest.mark.parametrize('required',[False,True])
def test_optional_skip_does_not_repeat_but_cannot_satisfy_a_required_question(required):
    state=current('보고서를 정리해줘.','manual')
    question={'key':'report.style','text':'보고서 형식을 선택할까요?','options':['요약','상세'],
              'response':{'type':'single_choice'},'required':required}
    message='선택사항 건너뛰기';turn=len(state['messages'])
    state['messages'].append({'role':'user','content':message})
    state['requirement_answers']=[{'questions':[question],'answer':message,'value':None,'skipped':True,
                                  'message_content':message,'turn':turn}]
    questions,unknown=business_questions([question],state)
    assert not unknown
    assert bool(questions) is required


def test_bad_extraction_keeps_technical_error_out_of_customer_message():
    errors=[]
    model=Extractor({'tasks':'bad_schema_field','unresolved':[]})
    result=make_requirement_check('fixture',model,'enforce',error_observer=errors.append)(current('보고서를 만들어줘.','manual'))
    assert errors and result['status']=='validation_failed'
    assert 'bad_schema_field' not in json.dumps(result)
    assert result['messages'][-1]['content']=='입력하신 조건은 보존했습니다. 업무 흐름을 구성하는 중 문제가 생겼습니다. 조건을 더 입력하지 않아도 같은 내용으로 다시 시도할 수 있습니다.'
    assert result['requirement_failure']=={'kind':'processing_error','retryable':True}


@pytest.mark.parametrize('hour',[0,9])
def test_numeric_schedule_answers_are_grounded_and_reextracted_with_question_context(monkeypatch,hour):
    state=current();model=Extractor(raw(['gmail_access_token','summary_format_preference']))
    check=make_requirement_check('fixture',model,'enforce')
    state={**state,**check(state)}
    for key,answer in [('schedule.time',f'{hour:02d}:00')]:
        question=next(q for q in state['pending_questions'] if q['key']==key)
        monkeypatch.setattr(await_input,'interrupt',lambda payload,q=question,a=answer:{'type':'message','content':a,'question_id':q['id']})
        state={**state,**await_input.await_input_node(state)}
        state={**state,**check(state)}
    expected={'frequency':'daily','hour':hour,'minute':0,'weekday':None,'monthday':None,'month_end':False,'timezone':'Asia/Seoul'}
    assert state['requirement_route']=='plan'
    assert state['messages'][0]['content']==REQUEST
    assert grounded_schedule(state)==expected
    assert validate_plan({'tasks':[{'id':'r1','source_message':0,'quote':REQUEST,'description':'예약 실행',
                                   'depends_on':[],'spec':{'kind':'schedule','schedule':expected}}],'unresolved':[]},state)
    last=json.loads(model.calls[-1][1]['content'])
    assert last['user_messages']==[REQUEST,f'{hour:02d}:00']
    assert last['clarification_answers'][0]['questions'][0]['key']=='schedule.time'
    assert last['clarification_answers'][0]['answer']==f'{hour:02d}:00'


def test_batch_schedule_answers_are_bound_to_the_aggregate_user_message():
    state=current();message='실행 시: 9\n실행 분: 0\n시간대: Asia/Seoul'
    turn=len(state['messages']);state['messages'].append({'role':'user','content':message})
    state['requirement_answers']=[{'questions':[{'key':key}],'answer':answer,'turn':turn,'message_content':message}
                                 for key,answer in [('schedule.hour','9'),('schedule.minute','0'),('schedule.timezone','Asia/Seoul')]]
    assert grounded_schedule(state)['hour']==9
    assert grounded_schedule(state)['minute']==0
    state['requirement_answers'][0]['message_content']='unrelated message'
    with pytest.raises(ValueError):grounded_schedule(state)


def test_requirement_question_interrupt_resumes_into_reextraction_and_original_planner(tmp_path):
    state=current('메일 내용을 요약해줘.','manual');model=Extractor(raw(['어떤 내용을 중심으로 요약할까요?']))
    planned=[]
    class ReachedPlanner(RuntimeError):pass
    def planner(value):planned.append(value);raise ReachedPlanner()
    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=planner,requirement_transport=model,requirement_mode='enforce',
        capability_mode='off',condition_mode='off',verifier_mode='off')
    config={'configurable':{'thread_id':state['session_id']}}
    try:
        first=graph.invoke(state,config)
        assert first['status']=='needs_clarification' and not planned
        records=first['__interrupt__'][0].value['question_records']
        assert records[0]['source']=='requirements'
        model.value=raw([])
        with pytest.raises(ReachedPlanner):
            graph.invoke(Command(resume={'type':'message','content':'긴급한 요청 중심','question_id':records[0]['id']}),config)
        assert planned[0]['messages'][0]['content']=='메일 내용을 요약해줘.'
        payload=json.loads(model.calls[-1][1]['content'])
        assert payload['user_messages']==['메일 내용을 요약해줘.','긴급한 요청 중심']
        assert payload['clarification_answers'][0]['questions'][0]['text']=='어떤 내용을 중심으로 요약할까요?'
    finally:
        connection.close();store.close()


GMAIL_MORNING_REQUEST = '매일 오전에 gmail에서 안읽은 메일 요약해줘 상위 5개만'


def morning_gmail_tasks(hour=9, minute=0):
    from agent_builder.design.gmail_summary import UNREAD_QUERY
    return [
        {'source_message':0,'description':'매일 예약 실행','spec':{'kind':'schedule','schedule':{
            'frequency':'daily','hour':hour,'minute':minute,'weekday':None,'monthday':None,
            'month_end':False,'timezone':'Asia/Seoul'}}},
        {'source_message':0,'description':'안 읽은 메일 5개 요약','spec':{
            'kind':'gmail_summary','request':GMAIL_MORNING_REQUEST,'max_results':5,'query':UNREAD_QUERY}},
    ]


@pytest.mark.parametrize('schedule_first',[True,False])
def test_separate_schedule_with_missing_time_asks_once_without_losing_gmail_conditions(schedule_first):
    state=current(GMAIL_MORNING_REQUEST,mode=None)
    before=deepcopy(state)
    tasks=morning_gmail_tasks()
    if not schedule_first:tasks.reverse()
    model=Extractor({'tasks':tasks,'unresolved':[]})
    errors=[]
    result=make_requirement_check('fixture',model,'enforce',error_observer=errors.append)(state)
    assert result['status']=='needs_clarification'
    assert result['requirement_route']=='await_input' and result['requirement_failure'] is None
    assert len(model.calls)==1 and not errors
    assert [q['key'] for q in result['pending_questions']]==['schedule.time']
    assert result['pending_questions'][0]['response']=={'type':'time'}
    assert result['messages'][0]['content']==GMAIL_MORNING_REQUEST
    assert state==before


@pytest.mark.parametrize('answer,hour,minute',[('09:00',9,0),('10:25',10,25)])
def test_morning_gmail_time_answer_resumes_graph_with_original_query_count_and_schedule(tmp_path,answer,hour,minute):
    from agent_builder.design.gmail_summary import UNREAD_QUERY
    from tests.workflow_engine.test_gmail_scheduling import gmail_config
    state=current(GMAIL_MORNING_REQUEST,mode=None)
    model=Extractor({'tasks':morning_gmail_tasks(),'unresolved':[]})
    planned=[]

    def planner(value):
        planned.append(deepcopy(value))
        draft=gmail_config()
        schedule=grounded_schedule(value)
        draft.update(name='안 읽은 메일 5개 요약',purpose=GMAIL_MORNING_REQUEST,
                     instructions=GMAIL_MORNING_REQUEST,schedule=schedule,
                     requirements=[{'description':'안 읽은 메일 5개 요약','source_id':'user','quote':GMAIL_MORNING_REQUEST}])
        draft['workflow']['nodes'][0]['settings']['arguments_json']=json.dumps({'query':UNREAD_QUERY,'max_results':5})
        draft['workflow']['nodes'][1]['settings']['instructions']=GMAIL_MORNING_REQUEST
        return {'draft':draft}

    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=planner,requirement_transport=model,requirement_mode='enforce',
        capability_mode='off',condition_mode='off',verifier_mode='off')
    config={'configurable':{'thread_id':state['session_id']}}
    try:
        first=graph.invoke(state,config)
        assert first['status']=='needs_clarification' and not planned
        question,=first['__interrupt__'][0].value['question_records']
        assert question['key']=='schedule.time' and question['response']=={'type':'time'}
        model.value={'tasks':morning_gmail_tasks(hour,minute),'unresolved':[]}
        result=graph.invoke(Command(resume={'type':'message','content':answer,
            'question_answers':[{'question_id':question['id'],'value':answer}]}),config)
        assert result['status']=='ready',result.get('issues')
        assert result['__interrupt__'][0].value['kind']=='review'
        assert len(planned)==1 and result['messages'][0]['content']==GMAIL_MORNING_REQUEST
        tasks=result['requirement_ledger']['tasks']
        mail=next(task['spec'] for task in tasks if task['spec']['kind']=='gmail_summary')
        schedule=next(task['spec']['schedule'] for task in tasks if task['spec']['kind']=='schedule')
        assert mail['query']==UNREAD_QUERY and mail['max_results']==5 and mail['request']==GMAIL_MORNING_REQUEST
        assert schedule==grounded_schedule(result)==result['draft']['schedule']
        assert (schedule['frequency'],schedule['hour'],schedule['minute'])==('daily',hour,minute)
        assert result['pending_questions']==[] and not store.agents()
        payload=json.loads(model.calls[-1][1]['content'])
        assert payload['user_messages']==[GMAIL_MORNING_REQUEST,answer]
        assert payload['clarification_answers'][-1]['value']==answer
    finally:
        connection.close();store.close()
