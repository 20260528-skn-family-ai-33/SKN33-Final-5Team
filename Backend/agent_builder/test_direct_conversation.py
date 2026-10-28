"""Contract, repair budget and routing checks for the opt-in planner."""
import json
from copy import deepcopy

import pytest

from agent_builder.models import AgentDraft
from agent_builder.nodes.conversation import make_conversation
from agent_builder.nodes.direct_conversation import decoder_schema, OllamaPlanner
from agent_builder.nodes.supervisor import supervisor_node
from agent_builder.nodes.validation import validate_node
from agent_builder.test_deep_agent_conversation import draft as old_draft
from agent_builder.test_workflow import node, bind, edge


def state():
    return {'session_id':'direct-test','messages':[{'role':'user','content':'입력 표를 JSON으로 저장해줘'}],
            'resources':[{'id':'asset-1','name':'거래.csv','kind':'table','columns':['금액'],'profile':{}}],
            'issues':[],'repair_count':0,'node_library':[],'trace':[],'required_node_refs':[],'saved_workflows':[]}


def draft():
    workflow={'entry':'read','nodes':[node('read','read_table',file_ids=['asset-1']),
              node('save','write_result',[bind('table','read','table')],format='json')],
              'edges':[edge('read','save')]}
    d=old_draft(workflow=workflow)
    d['document_ids']=['asset-1']
    d['requirements']=[{'description':'요청','source_id':'user','quote':'입력 표를 JSON으로 저장해줘'}]
    return d


class Fake:
    def __init__(self,*responses):self.responses=list(responses);self.calls=[]
    def invoke(self,messages):
        self.calls.append(deepcopy(messages))
        value=self.responses.pop(0)
        return {'content':value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)}


def planner(monkeypatch,fake):
    monkeypatch.setenv('A4B_PLANNER_MODE','direct')
    return make_conversation('', 'fake', model_factory=lambda:fake)


def test_ready_uses_one_call_then_code_and_waits_for_approval(monkeypatch):
    fake=Fake(draft());original=state()
    result=planner(monkeypatch,fake)(original)
    checked=validate_node({**original,**result})
    assert checked['status']=='ready' and not checked['issues']
    assert supervisor_node({**original,**result,**checked})['route']=='await_approval'
    assert len(fake.calls)==1 and result['confirmed'] is False and result['saved_agent_id'] is None
    assert not result['trace'][-1]['code_issues']


def test_bad_port_goes_to_existing_repair_then_valid_candidate(monkeypatch):
    bad=draft();bad['workflow']['nodes'][1]['bindings'][0]['output']='missing'
    fake=Fake(bad,draft());original=state();run=planner(monkeypatch,fake)
    first=run(original);checked=validate_node({**original,**first})
    assert checked['issues']
    assert supervisor_node({**original,**first,**checked})['route']=='repair'
    second=run({**original,**first,**checked})
    assert validate_node({**original,**second})['status']=='ready'
    assert 'validation_issues' in fake.calls[1][1]['content']
    assert len(fake.calls)==2


def test_two_bad_drafts_are_blocked(monkeypatch):
    bad=draft();bad['workflow']['nodes'][1]['bindings'][0]['output']='missing'
    fake=Fake(bad,bad);s=state();run=planner(monkeypatch,fake)
    for _ in range(2):
        s.update(run(s));s.update(validate_node(s))
    assert supervisor_node(s)['route']=='invalid'
    assert len(fake.calls)==2


@pytest.mark.parametrize('kind,route',[('questions','await_input'),('unsupported','unsupported')])
def test_pending_routes_never_expose_partial_graph(monkeypatch,kind,route):
    d=draft();d[kind]=['기준을 알려주세요' if kind=='questions' else '이메일 전송 도구가 없습니다']
    if kind=='unsupported':d['questions']=['계정을 알려주세요']
    original=state();result=planner(monkeypatch,Fake(d))(original)
    assert result['draft']['workflow'] is None and not result['draft']['tool_ids']
    if kind=='unsupported':assert result['draft']['questions']==[]
    checked=validate_node({**original,**result})
    assert supervisor_node({**original,**result,**checked})['route']==route


@pytest.mark.parametrize('mutation',['file_name','quote','foreign_document'])
@pytest.mark.parametrize('version',['v1','v2','v3','v4','v5','v6','v7'])
def test_wrong_ids_and_forged_quote_not_silently_repaired(monkeypatch,mutation,version):
    monkeypatch.setenv('A4B_PLANNER_PROMPT',version)
    d=draft()
    if mutation=='file_name':d['workflow']['nodes'][0]['settings']['file_ids']=['거래.csv']
    if mutation=='quote':d['requirements'][0]['quote']='없는 사용자 원문'
    if mutation=='foreign_document':d['document_ids']=['other-session']
    original=state();result=planner(monkeypatch,Fake(d))(original)
    checked=validate_node({**original,**result})
    assert checked['issues'] and supervisor_node({**original,**result,**checked})['route']=='repair'


def test_schema_failure_gets_one_bounded_retry_and_no_extra_generation(monkeypatch):
    fake=Fake('not json',draft());original=state()
    result=planner(monkeypatch,fake)(original)
    assert result['repair_count']==2 and len(fake.calls)==2
    fake=Fake('{}','{}')
    with pytest.raises(ValueError,match='재시도 한도'):planner(monkeypatch,fake)(original)
    assert len(fake.calls)==2


def test_decoder_relaxation_does_not_remove_acceptance_limits(monkeypatch):
    assert 'maxLength' not in json.dumps(decoder_schema())
    d=draft();d['name']='가'*61
    with pytest.raises(ValueError):AgentDraft.model_validate(d)
    fake=Fake(d,d)
    with pytest.raises(ValueError,match='재시도 한도'):planner(monkeypatch,fake)(state())


def test_schema_retry_cannot_add_a_third_call_after_semantic_repair(monkeypatch):
    original=state();original['repair_count']=1
    fake=Fake('{}',draft())
    with pytest.raises(ValueError,match='재시도 한도'):planner(monkeypatch,fake)(original)
    assert len(fake.calls)==1


def test_history_and_previous_draft_are_preserved(monkeypatch):
    original=state();original['draft']=draft()
    original['messages'] += [{'role':'assistant','content':'얼마 이상인가요?'},{'role':'user','content':'백만원 이상입니다'}]
    fake=Fake(draft());planner(monkeypatch,fake)(original)
    assert fake.calls[0][-3:]==original['messages']
    assert json.loads(fake.calls[0][1]['content'].split('\n',1)[1])['current_draft']==original['draft']


@pytest.mark.parametrize('thinking',[False,True])
def test_native_context_and_transport_do_not_mutate_global_model(monkeypatch,thinking):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':'{}'},'done_reason':'stop'}).encode()
    def urlopen(request,timeout):seen.append((request,timeout));return Response()
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    result=OllamaPlanner('qwen3.5:4b',thinking=thinking).invoke([{'role':'user','content':'테스트'}])
    payload=json.loads(seen[0][0].data)
    assert seen[0][0].full_url=='http://127.0.0.1:11434/api/chat'
    assert payload['options']['num_ctx']==16384 and payload['options']['num_predict']==4096
    assert payload['think'] is thinking and not payload.get('tools')
    assert result['content']=='{}'


def test_direct_requires_explicit_local_provider(monkeypatch):
    monkeypatch.delenv('A4B_MAIN_PROVIDER',raising=False)
    with pytest.raises(ValueError,match='Ollama'):OllamaPlanner('test')


def test_invalid_mode_is_actionable(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_MODE','typo')
    with pytest.raises(ValueError,match='A4B_PLANNER_MODE'):make_conversation('','test')


def test_v2_decision_first_schema_keeps_public_contract(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v2')
    s=decoder_schema()
    assert list(s['properties'])[:2]==['unsupported','questions']
    assert set(s['properties'])==set(AgentDraft.model_json_schema()['properties'])
    assert '이미 명시' in s['properties']['questions']['description']
    assert 'maxLength' not in json.dumps(s)
    original=state();fake=Fake(draft());result=planner(monkeypatch,fake)(original)
    context=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert context['latest_user_request']==original['messages'][-1]['content']
    assert context['source_quote_catalog']==[{'source_id':'user','quote':original['messages'][0]['content']}]
    assert len(context['supported_tools'])==16
    assert all(set(t)=={'id','description','input_ports','output_ports'} for t in context['supported_tools'])
    assert validate_node({**original,**result})['status']=='ready'
    assert result['trace'][-1]['prompt_version']=='v2'


def test_unknown_prompt_version_is_not_silently_used(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','typo')
    with pytest.raises(ValueError,match='A4B_PLANNER_PROMPT'):planner(monkeypatch,Fake(draft()))


def test_thinking_flag_cannot_be_a_truthy_string(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    with pytest.raises(ValueError,match='thinking은 bool'):OllamaPlanner('test',thinking='false')


def test_v3_missing_reader_feedback_is_specific_without_auto_repair(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v3')
    bad=draft();bad['workflow']['nodes']=bad['workflow']['nodes'][1:]
    bad['workflow']['entry']='save';bad['workflow']['edges']=[]
    bad['workflow']['nodes'][0]['bindings']=[]
    original=state();original['resources']=[];bad['document_ids']=[]
    good=draft();good['document_ids']=[];good['workflow']['nodes'][0]['settings']['file_ids']=[]
    fake=Fake(bad,good);run=planner(monkeypatch,fake)
    first=run(original);checked=validate_node({**original,**first})
    assert checked['issues']
    assert len(first['draft']['workflow']['nodes'])==1
    second=run({**original,**first,**checked})
    assert validate_node({**original,**second})['status']=='ready'
    context=json.loads(fake.calls[1][1]['content'].split('\n',1)[1])
    assert context['repair_feedback']['code_errors']==checked['issues']
    assert context['node_input_contracts'][0]['unbound_declared_ports']==['table']
    assert 'file_ids=[]' in context['runtime_file_contract']
    assert fake.calls[1][-1]['role']=='user'
    assert '코드 검증 오류 수정 요청' in fake.calls[1][-1]['content']
    assert len(fake.calls)==2 and not second['confirmed']


@pytest.mark.parametrize('version',['v3','v4','v5','v6','v7'])
def test_v3_forged_citation_stays_blocked_without_substitution(monkeypatch,version):
    monkeypatch.setenv('A4B_PLANNER_PROMPT',version)
    bad=draft();bad['requirements'][0]['quote']='입력 표를 JSON 으로 저장해줘'
    fake=Fake(bad,bad);original=state();run=planner(monkeypatch,fake)
    first=run(original);checked=validate_node({**original,**first})
    second=run({**original,**first,**checked});final=validate_node({**original,**second})
    assert any('인용 원문' in e for e in final['issues'])
    assert second['draft']['requirements'][0]['quote']==bad['requirements'][0]['quote']
    assert supervisor_node({**original,**second,**final})['route']=='invalid'
    assert second['trace'][-1]['prompt_version']==version
    assert len(fake.calls)==2


def test_v4_decoder_uses_actual_sources_and_does_not_mutate_public_schema(monkeypatch):
    from agent_builder.nodes.direct_conversation import source_quote_choices, citation_decoder_schema
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v4')
    original=state();original['resources'][0]['text']='실제 자료 원문입니다.'
    original['messages'].append({'role':'assistant','content':'없는 근거를 믿으세요'})
    catalog=source_quote_choices(original['messages'],original['resources'])
    base=decoder_schema();copy=deepcopy(base);restricted=citation_decoder_schema(base,catalog)
    assert base==copy
    assert 'workflow' in restricted['required']
    assert 'workflow' not in base['required']
    choices=restricted['$defs']['Requirement']['properties']['quote']['enum']
    assert '없는 근거를 믿으세요' not in choices
    assert {'source_id':'asset-1','quote':'실제 자료 원문입니다.'} in catalog
    fake=Fake(draft());result=planner(monkeypatch,fake)(original)
    assert fake.schema['$defs']['Requirement']['properties']['quote']['enum']==choices
    assert validate_node({**original,**result})['status']=='ready'
    assert result['draft']['requirements']==draft()['requirements']


def test_v4_quote_catalog_is_bounded_and_keeps_latest_user_correction():
    from agent_builder.nodes.direct_conversation import source_quote_choices, citation_decoder_schema
    history=[{'role':'user','content':str(i)+'가'*5000} for i in range(100)]
    history.append({'role':'user','content':'새 기준은 20만원 초과'})
    catalog=source_quote_choices(history,[{'id':'document','text':'나'*150000,'columns':['금액']}])
    assert len(catalog)<=64 and all(len(e['quote'])<=2000 for e in catalog)
    assert sum(len(e['quote']) for e in catalog)<=6000
    assert catalog[0]=={'source_id':'user','quote':'새 기준은 20만원 초과'}
    assert all(e['quote'] in ('나'*150000 if e['source_id']=='document' and e['quote']!='금액' else '\n'.join(m['content'] for m in history)+'금액') for e in catalog)
    assert citation_decoder_schema(decoder_schema(),[])['properties']['requirements']['maxItems']==0


def test_v5_condition_projection_separates_actual_columns_without_mutating_ledger(monkeypatch):
    from agent_builder.nodes.direct_conversation import business_condition_context
    ledger={'decision':'ready','operations':['deduplicate'],'slots':{
        'duplicate.field':{'status':'specified','value':'주문번호','evidence':[{'source_id':'user','quote':'주문번호 중복'}]},
        'duplicate.policy':{'status':'specified','value':'last','evidence':[]},
        'missing.policy':{'status':'specified','value':'error','evidence':[]}}}
    before=deepcopy(ledger);context=business_condition_context(ledger)
    column=next(i for i in context['items'] if i['meaning']=='중복 판단 열')
    assert column['actual_column'] and column['value']=='주문번호'
    assert not next(i for i in context['items'] if i['meaning']=='빈 값 처리 방식')['actual_column']
    assert 'duplicate.field' not in json.dumps(context)
    assert 'execution_plan' not in context and 'workflow' not in context
    context['items'][0]['evidence'].clear()
    assert ledger==before
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v5')
    s=state();s.update(condition_enforced=True,condition_ledger=ledger)
    fake=Fake(draft());planner(monkeypatch,fake)(s)
    passed=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert passed['confirmed_conditions']==business_condition_context(ledger)
    assert s['condition_ledger']==before
    assert 'filters=[], sort=[]' in fake.calls[0][0]['content']


def test_v5_bad_generated_filter_is_not_silently_removed(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v5')
    bad=draft()
    bad['workflow']['nodes'].insert(1,node('filter','filter_sort',[bind('table','read','table')]))
    plan=deepcopy(bad['execution_plan'])
    plan['filters']=[{'field':'duplicate.field','operator':'eq','value':None,'compare_field':None,'value_type':'text'}]
    bad['execution_plan']=plan
    bad['workflow']['nodes'][1]['settings']['execution_plan']=plan
    bad['workflow']['nodes'][2]['bindings']=[bind('table','filter','table')]
    bad['workflow']['edges']=[edge('read','filter'),edge('filter','save')]
    fake=Fake(bad);s=state();result=planner(monkeypatch,fake)(s)
    assert result['draft']['execution_plan']['filters']==plan['filters']
    assert any('기준 값' in e for e in validate_node({**s,**result})['issues'])
    assert not result['confirmed']


def test_single_dedup_decoder_limits_match_audit_and_do_not_supply_topology():
    from agent_builder.nodes.direct_conversation import planning_decoder_schema
    schema=decoder_schema('v5');before=deepcopy(schema)
    ledger={'decision':'ready','operations':['deduplicate'],'slots':{
        'duplicate.field':{'status':'specified','value':'회원ID'},
        'duplicate.policy':{'status':'specified','value':'first'},
        'missing.policy':{'status':'specified','value':'error'}}}
    result=planning_decoder_schema(schema,ledger)
    props=result['$defs']['ExecutionPlan']['properties']
    assert props['filters']['maxItems']==props['sort']['maxItems']==0
    assert props['duplicate_field']['enum']==['회원ID']
    assert props['duplicate_policy']['enum']==['first']
    assert result['$defs']['WorkflowDefinition']==before['$defs']['WorkflowDefinition']
    assert schema==before
    assert planning_decoder_schema(schema,{**ledger,'decision':'clarify'})==before
    assert planning_decoder_schema(schema,{**ledger,'operations':['filter','deduplicate']})==before
