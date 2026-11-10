"""Generation contracts retained from the 9B validation snapshot, without replacing stored data."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from contracts.draft_outcome import outcome_schema, validate_generated_outcome, DraftOutcomeError
from contracts.models import AgentDraft
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.requirement_ledger import validate_plan, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_coverage import coverage_report, _same_filters
from tests.design.test_deep_agent_conversation import draft, state
from tests.design.test_requirement_coverage import seed, joined, task


@pytest.mark.parametrize('mode',['workflow','questions','unsupported'])
def test_generated_outcome_accepts_exactly_one_complete_result(mode):
    from tests.workflow_engine.test_workflow import node
    raw=draft(**{mode: {'entry':'read','nodes':[node('read','read_table')],'edges':[]}
                 if mode=='workflow' else ['확인할 사항']})
    raw['requirements']=[]
    schema=outcome_schema(AgentDraft.model_json_schema())
    Draft202012Validator(schema).validate(raw)
    validate_generated_outcome(raw)
    empty=deepcopy(raw);empty['instructions']=''
    assert not Draft202012Validator(schema).is_valid(empty)
    with pytest.raises(DraftOutcomeError):validate_generated_outcome(empty)


def test_incomplete_or_ambiguous_generated_results_are_not_silently_repaired():
    raw=draft();before=deepcopy(raw)
    with pytest.raises(DraftOutcomeError):validate_generated_outcome(raw)
    assert raw==before
    raw=draft(questions=[' '])
    with pytest.raises(DraftOutcomeError):validate_generated_outcome(raw)
    raw=draft(questions=['확인'],unsupported=['미지원'])
    with pytest.raises(DraftOutcomeError):validate_generated_outcome(raw)
    # Legacy pending drafts retain their stored shape; stricter rules apply at generation.
    raw=draft();raw.update(instructions='',requirements=[])
    assert AgentDraft.model_validate(raw).instructions==''


def test_outcome_alternatives_survive_real_deepagent_model_binding():
    import httpx
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from infrastructure.llm.ollama_chat_model import OllamaChatOpenAI
    calls=[];answer=draft(questions=['어떤 조건인가요?']);answer['requirements']=[]
    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'chat-test','object':'chat.completion','created':0,
            'model':'final664','choices':[{'index':0,'message':{'role':'assistant',
                'content':json.dumps(answer)},'finish_reason':'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        model=OllamaChatOpenAI(model='final664',base_url='http://example.test/v1',api_key='test',http_client=client)
        agent=create_deep_agent(model=model,tools=[],response_format=ProviderStrategy(
            outcome_schema(AgentDraft.model_json_schema()),strict=True))
        result=agent.invoke({'messages':[{'role':'user','content':'확인 질문을 작성해줘'}]})
    assert result['structured_response']==answer and len(calls)==1
    wire=calls[0]['response_format']['json_schema']['schema']
    assert len(wire['anyOf'])==3
    Draft202012Validator(wire).validate(answer)
    assert not Draft202012Validator(wire).is_valid(draft())


def test_ollama_retries_blank_instructions_with_original_request(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    calls=[]
    def factory(**options):
        assert '최상위 instructions' in options['system_prompt']
        class Agent:
            def invoke(self,payload,*args):
                calls.append(payload)
                result=draft(questions=['결과 형식은 무엇인가요?'])
                if len(calls)==1:result['instructions']='  '
                return {'structured_response':result}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    original=state([{'role':'user','content':'표를 정리해줘'}])
    result=make_conversation('','final664',model_factory=lambda:object())(original)
    assert len(calls)==2 and result['draft']['instructions'].strip()
    assert calls[0]['messages']==calls[1]['messages'][:-1]
    assert 'instructions' in calls[1]['messages'][-1]['content']


def test_join_decoder_keeps_input_and_step_requirements_in_ordered_format(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants
    from agent_builder.design.requirement_generation import constrain_uploaded_join_inputs
    from agent_builder.design.operation_steps import schema_for
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    original=AgentDraft.model_json_schema()
    base=deepcopy(original['$defs']['WorkflowNode'])
    original['$defs']['WorkflowNode']={'anyOf':operation_variants(base,original['$defs']['Binding'],{'join_tables','read_table','calculate_columns','aggregate_table','write_result'})}
    schema=schema_for(constrain_uploaded_join_inputs(original,joined()))
    assert schema['$defs']['WorkflowDefinition']['properties']['next_steps']['minItems']==6
    variants=schema['$defs']['WorkflowNode']['anyOf']
    joins=[v for v in variants if v['properties']['tool'].get('enum')==['join_tables']]
    assert joins
    for v in joins:
        spec=v['properties']['settings']['properties']['arguments_json']
        assert {'keys','how','cardinality','null_keys','max_rows'}<=set(spec['required'])
        assert not {'left','right'} & set(spec['properties'])


def test_date_requirement_is_checked_against_actual_date_operation():
    from tests.workflow_engine.test_workflow import node,bind,edge
    s=seed();request='date 열은 %Y-%m-%d 형식이며 Asia/Seoul로 변환해서 JSON으로 저장해줘.'
    s['messages']=[dict(role='user',content=request)]
    spec=dict(fields=['date'],input_format='%Y-%m-%d',timezone='Asia/Seoul')
    tasks=[task('r1','parse_dates',**spec),task('r2','export',['r1'],format='json')]
    for t in tasks:t['quote']=request
    plan=validate_plan(dict(tasks=tasks,unresolved=[]),s)
    s.update(requirement_ledger=plan,requirement_fingerprint=ledger_fingerprint(s),requirement_digest=plan_digest(plan))
    s['draft']['workflow']=dict(entry='read',nodes=[node('read','read_table'),
        node('dates','calculate_columns',[bind('table','read','table')],table_operation='parse_dates',
             fields=['date'],date_input_format='%Y-%m-%d',date_timezone='Asia/Seoul'),
        node('out','write_result',[bind('table','dates','table')],format='json')],edges=[edge('read','dates'),edge('dates','out')])
    assert not coverage_report(s,s['draft'])['issues']
    s['draft']['workflow']['nodes'][1]['settings']['date_timezone']='UTC'
    assert coverage_report(s,s['draft'])['issues']
    tasks[0]['spec']['timezone']='not/a/timezone'
    with pytest.raises(ValueError,match='시간대'):validate_plan(dict(tasks=tasks,unresolved=[]),s)


def test_explicit_user_criteria_cannot_be_dropped_or_read_from_an_attachment():
    from agent_builder.design.explicit_table_criteria import explicit_criteria
    s=seed()
    request=s['messages'][0]['content']+'\n제공하는 실행 조건: '+json.dumps({
        'calculations':[{'formula':'qty*10','output_column':'amount'}]},ensure_ascii=False)
    s['messages'][0]['content']=request
    assert validate_plan(s['requirement_ledger'],s)
    missing=deepcopy(s['requirement_ledger']);missing['tasks'][0]['spec']={'kind':'other','action':'계산'}
    with pytest.raises(ValueError,match='compute'):validate_plan(missing,s)
    assert explicit_criteria({'messages':[{'role':'assistant','content':request}]}) is None
    assert explicit_criteria({'messages':[{'role':'user','content':'첨부자료를 읽어줘'}],
                              'resources':[{'text':request}]}) is None


def test_single_predicate_match_is_equivalent_without_relaxing_multi_predicates():
    f={'field':'status','operator':'eq','value':'완료','compare_field':None,'value_type':'text'}
    assert _same_filters([f],[f],'all','any')
    assert not _same_filters([f,f],[f,f],'all','any')


def test_one_table_reader_preserves_selected_file_among_multiple_uploads(tmp_path):
    from infrastructure.persistence.storage import Storage
    from infrastructure.persistence.ids import uid
    from workflow_engine.executors.sources import source_file_ids
    from tests.workflow_engine.test_workflow import node
    store=Storage(tmp_path)
    try:
        a=store.add_asset({'id':uid(),'name':'orders.csv','kind':'table','columns':['qty']},[{'qty':2}])
        b=store.add_asset({'id':uid(),'name':'reference.csv','kind':'table','columns':['qty']},[{'qty':999}])
        reader=node('read','read_table',file_ids=[a['id']])
        config={'workflow':{'nodes':[reader]},'document_ids':[a['id']]}
        assert source_file_ids(reader,[a['id'],b['id']],store,config)==[a['id']]
        assert source_file_ids(reader,[b['id']],store,config)==[b['id']]
    finally:store.close()


def test_explicit_policy_checks_effective_node_settings_not_overridden_defaults():
    from agent_builder.design.explicit_table_criteria import explicit_draft_issues
    from workflow_engine.executors.table.filter_sort import execute
    s=seed();d=s['draft'];n=d['workflow']['nodes'][2]
    s['messages'][-1]['content']='제공하는 실행 조건: '+json.dumps(dict(missing_policy='error',selection=d['execution_plan']['filters'],sort=d['execution_plan']['sort']))
    d['execution_plan']['missing_policy']='keep'
    assert n['settings']['execution_plan']['missing_policy']=='error'
    assert not explicit_draft_issues(s,d)
    with pytest.raises(ValueError):
        execute(n,{'outputs':{'calc':{'table':{'columns':['amount'],'rows':[{'amount':None}]}}}},None,d,None)
    n['settings']['execution_plan']=None
    assert explicit_draft_issues(s,d)  # now the effective fallback is keep
    d['execution_plan']['missing_policy']='error'
    assert not explicit_draft_issues(s,d)


def test_explicit_conditions_use_execution_order_without_an_artifact():
    from agent_builder.design.explicit_table_criteria import explicit_draft_issues
    from itertools import permutations
    s=seed();d=s['draft'];w=d['workflow']
    w['nodes']=w['nodes'][:3];w['edges']=w['edges'][:2]
    s['messages'][-1]['content']='제공하는 실행 조건: '+json.dumps({'selection':d['execution_plan']['filters']})
    for order in permutations(w['nodes']):
        w['nodes']=list(order)
        assert not explicit_draft_issues(s,d)
    # A filter merely present in the array is not on the executable result path.
    w['edges']=w['edges'][:1]
    assert explicit_draft_issues(s,d)
