"""Controlled model replies must not lose requested work or pending previews."""
from copy import deepcopy
import json

import pytest

from contracts.models import AgentDraft, ExecutionPlan, NodeSettings
from agent_builder.capabilities.capability_gate import input_fingerprint, require_save_ready
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.processing_guard import processing_issues
from agent_builder.design.service_connections import reconcile_connection_questions, service_connection_context
from infrastructure.persistence.storage import Storage


REQUEST = 'Gmail에서 계약 관련 메일을 찾아 해지 조건을 요약해줘.'


def node(identifier, tool, bindings=None, **settings):
    defaults=dict(file_ids=[],query=None,fields=[],instructions=None,format=None,
                  question=None,condition_field=None,condition_operator=None)
    return dict(id=identifier,tool=tool,label=identifier,bindings=bindings or [],
                settings=NodeSettings(**{**defaults,**settings}).model_dump())


def draft(*, summary=True, questions=None):
    nodes=[node('mail','email.list',operation='read_messages',
                arguments_json=json.dumps({'query':'계약','max_results':100}))]
    edges=[]
    if summary:
        nodes.append(node('summary','compose_answer',
                          [dict(input='documents',node_id='mail',output='documents')],instructions=REQUEST))
        edges=[dict(source='mail',target='summary',branch='next')]
    return AgentDraft(name='계약 메일 해지 조건 요약',purpose=REQUEST,instructions=REQUEST,
                      tool_ids=[n['tool'] for n in nodes],document_ids=[],
                      requirements=[dict(description='메일 조회와 요약',source_id='user',quote=REQUEST)],
                      questions=questions or [],question_options=[[] for _ in questions or []],unsupported=[],
                      explanation='초안',workflow=dict(entry='mail',nodes=nodes,edges=edges),
                      execution_plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',
                                                   duplicate_policy='keep',duplicate_field=None,output_format='json')).model_dump()


def state(*, summary=True, questions=None):
    result=dict(session_id='pending-test',messages=[dict(role='user',content=REQUEST)],resources=[],
                issues=[],trace=[],repair_count=0,node_library=[],required_node_refs=[],saved_workflows=[],
                draft=draft(summary=summary,questions=questions),confirmed=False)
    result.update(capability_enforced=True,capability_assessment=dict(
        decision='existing_tools',tool_ids=['email.list','compose_answer'],missing_conditions=[],missing_actions=[],
        reason='메일 조회와 요약',user_quotes=[REQUEST]))
    result['capability_fingerprint']=input_fingerprint(result)
    return result


def test_read_only_gmail_cannot_save_a_requested_summary_when_ledger_mode_is_off(monkeypatch,tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    value=state(summary=False)
    assert processing_issues(value,value['draft'])
    checked=validate_node(value)
    assert checked['status']!='ready' and any('요약' in issue for issue in checked['issues'])
    with pytest.raises(ValueError,match='요약'):
        require_save_ready({**value,'confirmed':True,'status':'ready'})
    store=Storage(tmp_path)
    try:
        session=store.new_session('fixture','fixture')
        with pytest.raises(ValueError,match='요약'):
            store.save_agent({**value,'session_id':session['id'],'confirmed':True,'status':'ready'})
        assert not store.agents()
    finally:store.close()


def test_unrelated_processor_does_not_cover_the_requested_mail_summary(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    value=state(summary=False)
    value['draft']['workflow']['nodes'].append(node('transform','text_hash',
        operation='execute',arguments_json='{}'))
    assert processing_issues(value,value['draft'])


def test_actual_mail_summary_is_accepted_and_read_only_request_is_not_forced_to_summarize(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    value=state()
    assert not processing_issues(value,value['draft'])
    assert validate_node(value)['status']=='ready'
    source_only=state(summary=False)
    source_only['capability_assessment']['tool_ids']=['email.list']
    assert not processing_issues(source_only,source_only['draft'])


def test_question_preview_is_never_saved_even_without_semantic_or_capability_gates(monkeypatch,tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    value=state(questions=['대상 기간은 어떻게 할까요?'])
    for key in ['capability_enforced','capability_assessment','capability_fingerprint']:
        value.pop(key,None)
    assert validate_node(value)['status']=='needs_clarification'
    with pytest.raises(ValueError,match='질문'):
        require_save_ready({**value,'confirmed':True,'status':'ready'})
    store=Storage(tmp_path)
    try:
        session=store.new_session('fixture','fixture')
        with pytest.raises(ValueError,match='질문'):
            store.save_agent({**value,'session_id':session['id'],'confirmed':True})
        assert not store.agents()
    finally:store.close()


@pytest.mark.parametrize('connected',[True,False,None])
def test_connection_context_is_metadata_only(connected):
    context=service_connection_context({'gmail_connected':connected,'gmail_access_token':'do-not-expose'})
    assert context['gmail']['connected'] is connected
    assert 'do-not-expose' not in json.dumps(context)


def test_only_contradicted_connection_question_is_removed_and_business_question_is_kept():
    value=draft(questions=['Gmail 계정을 연결해 주세요.','대상 기간은 어떻게 할까요?'])
    value['question_options']=[['연결'],['지난주','이번주']]
    changed=reconcile_connection_questions(value,{'gmail_connected':True})
    assert changed['questions']==['대상 기간은 어떻게 할까요?']
    assert changed['question_options']==[['지난주','이번주']]
    assert len(changed['workflow']['nodes'])==2
    assert reconcile_connection_questions(value,{'gmail_connected':None})==value
    assert reconcile_connection_questions(value,{'gmail_connected':False})==value
    switched=draft(questions=['다른 Gmail 계정을 연결해 주세요.'])
    assert reconcile_connection_questions(switched,{'gmail_connected':True})==switched


class DirectReply:
    def __init__(self,value):self.responses=deepcopy(value if isinstance(value,list) else [value]);self.calls=[]
    def invoke(self,messages):
        self.calls.append(deepcopy(messages))
        return {'content':json.dumps(self.responses.pop(0),ensure_ascii=False)}


def planner_result(monkeypatch,mode,value,current):
    from agent_builder.design.nodes.conversation import make_conversation
    monkeypatch.setenv('A4B_PLANNER_MODE',mode)
    if mode=='direct':
        fake=DirectReply(value)
        result=make_conversation('', 'fixture', model_factory=lambda:fake)(current)
        contexts=[json.loads(call[1]['content'].split('\n',1)[1]) for call in fake.calls]
    else:
        calls=[]
        responses=deepcopy(value if isinstance(value,list) else [value])
        for response in responses:
            response['requirements']=[dict(description='메일 조회와 요약',evidence_id='e1')]
        class Agent:
            def invoke(self,payload,config):
                calls.append(deepcopy(payload['messages']))
                return {'structured_response':responses.pop(0)}
        monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
        result=make_conversation('', 'fixture', model_factory=lambda:object())(current)
        contexts=[json.loads(call[0]['content'].split('\n',1)[1]) for call in calls]
    return result,contexts


@pytest.mark.parametrize('mode',['direct','deepagent'])
def test_model_read_only_draft_is_rejected_in_both_planners_without_ledger(monkeypatch,mode,tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=state(summary=False)
    result,_=planner_result(monkeypatch,mode,current['draft'],current)
    checked=validate_node({**current,**result})
    assert checked['status']!='ready'
    with pytest.raises(ValueError,match='요약'):
        require_save_ready({**current,**result,'confirmed':True})


@pytest.mark.parametrize('mode',['direct','deepagent'])
def test_question_keeps_model_nodes_and_remains_pending_in_both_planners(monkeypatch,mode):
    current=state(questions=['대상 기간은 어떻게 할까요?'])
    result,_=planner_result(monkeypatch,mode,current['draft'],current)
    assert len(result['draft']['workflow']['nodes'])==2
    assert result['draft']['questions']==['대상 기간은 어떻게 할까요?']
    assert validate_node({**current,**result})['status']=='needs_clarification'
    with pytest.raises(ValueError,match='질문'):
        require_save_ready({**current,**result,'confirmed':True})


@pytest.mark.parametrize('mode',['direct','deepagent'])
def test_connected_gmail_question_cannot_erase_a_valid_model_graph(monkeypatch,mode):
    current={**state(questions=['Gmail 계정을 연결해 주세요.']),'gmail_connected':True}
    result,contexts=planner_result(monkeypatch,mode,current['draft'],current)
    assert len(result['draft']['workflow']['nodes'])==2 and not result['draft']['questions']
    assert validate_node({**current,**result})['status']=='ready'
    assert contexts[0]['service_connections']['gmail']['connected'] is True


@pytest.mark.parametrize('mode',['direct','deepagent'])
def test_connection_question_without_a_graph_uses_existing_bounded_retry(monkeypatch,mode):
    current={**state(),'gmail_connected':True}
    pending=draft(questions=['Gmail 계정을 먼저 연결해주세요.'])
    pending.update(workflow=None,tool_ids=[])
    result,contexts=planner_result(monkeypatch,mode,[pending,draft()],current)
    assert len(contexts)==2
    assert len(result['draft']['workflow']['nodes'])==2
    assert not result['draft']['questions']
    assert validate_node({**current,**result})['status']=='ready'
