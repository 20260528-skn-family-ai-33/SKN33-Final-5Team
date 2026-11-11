from copy import deepcopy
import json

import pytest
from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import (make_requirement_check, ledger_fingerprint,
                                                     plan_digest, validate_plan, extraction_schema)
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial, draft
from tests.workflow_engine.test_workflow import node, bind, edge

REQUEST=('qty에 10을 곱한 amount 열을 만들고 amount가 20 이상인 행만 남겨 '
         'amount 숫자 내림차순으로 정렬한 뒤 JSON으로 저장해줘. 빈 값 오류, 중복 유지.')


def task(id,kind,depends=None,**spec):
    return dict(id=id,source_message=0,quote=REQUEST,description=id+' '+kind,
                depends_on=depends or [],spec=dict(kind=kind,**spec))


def seed():
    plan=dict(filters=[dict(field='amount',operator='gte',value=20,compare_field=None,value_type='number')],
              sort=[dict(field='amount',direction='descending',value_type='number')],match='all',
              missing_policy='error',duplicate_policy='keep',duplicate_field=None,output_format='json')
    workflow=dict(entry='read',nodes=[node('read','read_table'),
        node('calc','calculate_columns',[bind('table','read','table')],table_operation='compute',
             calculation_formula='qty * 10',output_column='amount'),
        node('filter','filter_sort',[bind('table','calc','table')],execution_plan=plan),
        node('out','write_result',[bind('table','filter','table')],format='json')],
        edges=[edge('read','calc'),edge('calc','filter'),edge('filter','out')])
    ledger=dict(tasks=[task('r1','compute',formula='qty*10',output_column='amount'),
        task('r2','filter',['r1'],filters=deepcopy(plan['filters']),match='all'),
        task('r3','sort',['r2'],sort=deepcopy(plan['sort'])),
        task('r4','export',['r3'],format='json'),
        task('r5','policy',missing_policy='error',duplicate_policy='keep',duplicate_field=None)],unresolved=[])
    raw={**draft(), 'name':'판매액 처리','instructions':REQUEST,'purpose':REQUEST,
         'tool_ids':['read_table','calculate_columns','filter_sort','write_result'],
         'workflow':workflow,'execution_plan':plan,
         'requirements':[dict(description='계산·선별·정렬·저장',source_id='user',quote=REQUEST)]}
    state={**initial(),'messages':[dict(role='user',content=REQUEST)],'requirement_required':True,
           'requirement_ledger':ledger,'draft':AgentDraft.model_validate(raw).model_dump()}
    state.update(requirement_fingerprint=ledger_fingerprint(state),requirement_digest=plan_digest(ledger))
    return state


def test_full_flow_covers_separate_tasks_even_when_filter_and_sort_share_one_node():
    state=seed();result=coverage_report(state,state['draft'])
    assert not result['issues']
    assert all(t['status']=='covered' for t in result['tasks'])
    assert result['tasks'][1]['node_ids']==result['tasks'][2]['node_ids']==['filter']
    assert validate_node(state)['status']=='ready'


@pytest.mark.parametrize('change,missing',[
    ('omit_filter','r2'),('omit_sort','r3'),('wrong_formula','r1'),
    ('wrong_boundary','r2'),('wrong_direction','r3'),('wrong_format','r4'),('wrong_policy','r5')])
def test_partially_implemented_flow_is_not_ready(change,missing):
    state=seed();nodes=state['draft']['workflow']['nodes'];plan=nodes[2]['settings']['execution_plan']
    if change=='omit_filter':plan['filters']=[]
    if change=='omit_sort':plan['sort']=[]
    if change=='wrong_formula':nodes[1]['settings']['calculation_formula']='qty + 10'
    if change=='wrong_boundary':plan['filters'][0]['operator']='gt'
    if change=='wrong_direction':plan['sort'][0]['direction']='ascending'
    if change=='wrong_format':nodes[3]['settings']['format']='csv'
    if change=='wrong_policy':plan['duplicate_policy']='first'
    result=validate_node(state)
    assert result['status']!='ready'
    assert any(missing in x for x in result['issues'])


def test_processing_nodes_cannot_be_disconnected_from_the_saved_result():
    state=seed()
    state['draft']['workflow']['nodes'][-1]['bindings']=[bind('table','read','table')]
    report=coverage_report(state,state['draft'])
    assert any('데이터 연결' in x for x in report['issues'])


def test_registered_operation_can_replace_native_calculation_with_equivalent_formula():
    state=seed();calc=state['draft']['workflow']['nodes'][1]
    calc['settings']={'operation':'compute','arguments_json':json.dumps({'formula':'10 * qty','output_column':'amount'})}
    assert not coverage_report(state,state['draft'])['issues']


def joined():
    state=seed()
    request=('판매수량.csv를 왼쪽, 단가.csv를 오른쪽으로 item 기준 left join. 오른쪽 키는 유일, '
             'null 키 오류, 최대 100행. qty * price로 amount를 만들고 item별 amount 합계를 '
             'item 오름차순으로 JSON 저장.')
    state['messages']=[dict(role='user',content=request)]
    state['resources']=[dict(id='left-file',name='판매수량.csv',kind='table',columns=['item','qty']),
                        dict(id='right-file',name='단가.csv',kind='table',columns=['item','price'])]
    tasks=[task('r1','read',file_ids=['left-file']),task('r2','read',file_ids=['right-file']),
           task('r3','join',['r1','r2'],keys=['item'],how='left',cardinality='many_to_one',
                null_keys='error',max_rows=100,left_sources=['left-file'],right_sources=['right-file']),
           task('r4','compute',['r3'],formula='qty*price',output_column='amount'),
           task('r5','aggregate',['r4'],fields=['item'],aggregation='sum',sum_field='amount'),
           task('r6','sort',['r5'],sort=[dict(field='item',direction='ascending',value_type='text')]),
           task('r7','export',['r6'],format='json')]
    for t in tasks:t['quote']=request
    state['requirement_ledger']=dict(tasks=tasks,unresolved=[])
    wf=state['draft']['workflow']
    wf['nodes']=[node('left','read_table',file_ids=['left-file']),node('right','read_table',file_ids=['right-file']),
        node('join','join_tables',[bind('left','left','table'),bind('right','right','table')],operation='join',
             arguments_json=json.dumps(dict(keys=['item'],how='left',cardinality='many_to_one',null_keys='error',max_rows=100))),
        node('calc','calculate_columns',[bind('table','join','table')],table_operation='compute',
             calculation_formula='qty*price',output_column='amount'),
        node('aggregate','aggregate_table',[bind('table','calc','table')],fields=['item'],aggregation='sum',sum_field='amount'),
        node('sort','filter_sort',[bind('table','aggregate','table')],execution_plan=dict(filters=[],
             sort=[dict(field='item',direction='ascending',value_type='text')],match='all',
             missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json')),
        node('out','write_result',[bind('table','sort','table')],format='json')]
    wf['entry']='left';wf['edges']=[edge(a,b) for a,b in zip(['left','right','join','calc','aggregate','sort'],['right','join','calc','aggregate','sort','out'])]
    state.update(requirement_fingerprint=ledger_fingerprint(state),requirement_digest=plan_digest(state['requirement_ledger']))
    return state


@pytest.mark.parametrize('change,missing',[('none',None),('count_instead_of_sum','r5'),
    ('inner_instead_of_left','r3'),('reversed_sources','r3'),('bypass_aggregation','데이터 연결')])
def test_join_and_grouped_sum_criteria_and_dataflow(change,missing):
    state=joined();nodes=state['draft']['workflow']['nodes']
    if change=='count_instead_of_sum':nodes[4]['settings']['aggregation']='count'
    if change=='inner_instead_of_left':
        args=json.loads(nodes[2]['settings']['arguments_json']);args['how']='inner'
        nodes[2]['settings']['arguments_json']=json.dumps(args)
    if change=='reversed_sources':
        nodes[2]['bindings']=[bind('left','right','table'),bind('right','left','table')]
    if change=='bypass_aggregation':nodes[6]['bindings']=[bind('table','calc','table')]
    report=coverage_report(state,state['draft'])
    if missing:assert any(missing in issue for issue in report['issues'])
    else:assert not report['issues']


def test_canonical_sum_operation_is_accepted_without_enforcing_native_tool_settings():
    state=joined();aggregate=state['draft']['workflow']['nodes'][4]
    aggregate['settings']=dict(operation='sum',arguments_json=json.dumps(dict(group_by=['item'],value_column='amount',null_policy='error')))
    assert not coverage_report(state,state['draft'])['issues']


def test_uploaded_join_decoder_separates_bound_inputs_from_constants(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants
    from agent_builder.design.requirement_generation import constrain_uploaded_join_inputs
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema();base=deepcopy(schema['$defs']['WorkflowNode'])
    schema['$defs']['WorkflowNode']={'anyOf':operation_variants(base,schema['$defs']['Binding'],{'join_tables'})}
    original=deepcopy(schema);state=joined()
    constrained=constrain_uploaded_join_inputs(schema,state)
    assert schema==original
    assert [v['properties']['settings']['properties']['operation']['enum']
            for v in constrained['$defs']['WorkflowNode']['anyOf']]==[['join']]
    candidate=deepcopy(state['draft']['workflow']['nodes'][2])
    candidate['settings']={k:candidate['settings'].get(k) for k in ('operation','arguments_json','output_port')}
    candidate['settings']['arguments_json']=json.loads(candidate['settings']['arguments_json'])
    validator=Draft202012Validator({'$ref':'#/$defs/WorkflowNode','$defs':constrained['$defs']})
    validator.validate(candidate)
    duplicate=deepcopy(candidate)
    duplicate['settings']['arguments_json'].update(left={'rows':[]},right={'rows':[]})
    assert not validator.is_valid(duplicate)
    missing=deepcopy(candidate);missing['bindings']=[]
    assert not validator.is_valid(missing)
    # Direction/policy values are still authored by the model in baseline;
    # later requirement validation must reject a direction contrary to the user.
    inner=deepcopy(candidate);inner['settings']['arguments_json']['how']='inner'
    validator.validate(inner)
    unsealed=deepcopy(state);unsealed['requirement_digest']='invalid'
    assert constrain_uploaded_join_inputs(schema,unsealed)==original
    unresolved=deepcopy(state);unresolved['requirement_ledger']['unresolved']=['자료가 모호함']
    unresolved['requirement_digest']=plan_digest(unresolved['requirement_ledger'])
    assert constrain_uploaded_join_inputs(schema,unresolved)==original


def test_generation_schema_fixes_join_criteria_but_never_duplicates_bound_tables(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants
    from agent_builder.design.requirement_generation import constrain_generation,generation_requirements
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema();base=deepcopy(schema['$defs']['WorkflowNode'])
    variants=operation_variants(base,schema['$defs']['Binding'],{'join_tables'})
    schema['$defs']['WorkflowNode']={'anyOf':variants}
    original=deepcopy(schema);state=joined()
    _,steps=generation_requirements(state)
    assert steps==['read_table','read_table','join_tables','calculate_columns','aggregate_table','filter_sort','write_result']
    constrained=constrain_generation(schema,state)
    join=next(v for v in constrained['$defs']['WorkflowNode']['anyOf']
              if v['properties']['settings']['properties']['operation']['enum']==['join'])
    args_schema=join['properties']['settings']['properties']['arguments_json']
    args=dict(keys=['item'],how='left',cardinality='many_to_one',null_keys='error',max_rows=100)
    Draft202012Validator(args_schema).validate(args)
    assert list(Draft202012Validator(args_schema).iter_errors({**args,'left':{'rows':[]}}))
    assert list(Draft202012Validator(args_schema).iter_errors({**args,'cardinality':'one_to_one'}))
    assert schema==original


def test_ordered_decoder_has_a_required_sort_slot_and_exact_source_file_ids(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants
    from agent_builder.design.requirement_generation import constrain_generation,constrain_ordered_steps
    from agent_builder.design.operation_steps import schema_for
    from agent_builder.design.native_settings import expand_node_settings
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema();base=deepcopy(schema['$defs']['WorkflowNode']);variants=[]
    for tool in ('read_table','calculate_columns','aggregate_table','filter_sort','write_result'):
        variant=deepcopy(base);variant['properties']['tool']={'enum':[tool]}
        variant['properties']['settings']=deepcopy(schema['$defs']['NodeSettings'])
        variant['properties']['settings']['properties']['operation']={'type':'null'}
        variants.append(variant)
    variants+=operation_variants(base,schema['$defs']['Binding'],{'join_tables'})
    schema['$defs']['WorkflowNode']={'anyOf':variants};state=joined()
    bounded=constrain_ordered_steps(schema_for(constrain_generation(schema,state)),state)
    workflow=expand_node_settings(state['draft'])['workflow'];indices={n['id']:i for i,n in enumerate(workflow['nodes'],1)}
    steps=[]
    for n in workflow['nodes']:
        settings=deepcopy(n['settings'])
        if settings.get('operation'):
            settings={k:settings[k] for k in ('operation','arguments_json','output_port')}
            settings['arguments_json']=json.loads(settings['arguments_json'])
        steps.append(dict(tool=n['tool'],label=n['label'],settings=settings,
            bindings=[dict(input=b['input'],step=indices[b['node_id']],output=b['output']) for b in n['bindings']]))
    first=steps[0];first.pop('bindings')
    wire=dict(first_step=first,next_steps={'step_'+str(i):step for i,step in enumerate(steps[1:],2)})
    validator=Draft202012Validator({'$ref':'#/$defs/WorkflowDefinition','$defs':bounded['$defs']})
    validator.validate(wire)
    from agent_builder.design.operation_steps import compile_workflow
    compiled=compile_workflow(wire)
    assert [n['tool'] for n in compiled['nodes']]==[n['tool'] for n in workflow['nodes']]
    omitted=deepcopy(wire);omitted['next_steps'].pop('step_6')
    assert list(validator.iter_errors(omitted))
    with pytest.raises(ValueError,match='빠짐없이'):compile_workflow(omitted)
    swapped=deepcopy(wire);swapped['next_steps']['step_6']['tool']='write_result'
    assert list(validator.iter_errors(swapped))
    wrong_source=deepcopy(wire);wrong_source['first_step']['settings']['file_ids']=['right-file']
    assert list(validator.iter_errors(wrong_source))


@pytest.mark.parametrize('change',['user','resource','ledger'])
def test_request_changes_or_generator_removing_a_requirement_invalidates_the_seal(change):
    state=seed()
    if change=='user':state['messages'].append(dict(role='user',content='이번에는 30 이상으로 해줘.'))
    if change=='resource':state['resources']=[dict(id='new-source',kind='table',columns=['qty'])]
    if change=='ledger':state['requirement_ledger']['tasks'].pop(1)
    assert coverage_report(state,state['draft'])['issues']


def test_unverifiable_tasks_are_preserved_and_held_instead_of_ignored():
    state=seed();ledger=state['requirement_ledger']
    ledger['tasks'].append(task('r6','other',action='자유 형식 요약'))
    state['requirement_digest']=plan_digest(ledger)
    result=coverage_report(state,state['draft'])
    assert result['tasks'][-1]['status']=='unverified'
    assert any('r6' in x for x in result['issues'])


def test_supervisor_skips_only_an_unverifiable_requirement_on_the_current_plan():
    from agent_builder.design.nodes.supervisor import supervisor_node
    current = seed()
    ledger = current['requirement_ledger']
    ledger['tasks'].append(task('r6','other',action='자유 형식 요약'))
    current['requirement_digest'] = plan_digest(ledger)
    current.update(validate_node(current), repair_count=1)
    held = {**current, **supervisor_node(current)}
    assert held['route'] == 'invalid' and held['status'] == 'validation_failed'
    assert held['issues'] == current['issues'] and held['draft'] == current['draft']
    assert 'r6' in held['trace'][-1]['detail']
    assert supervisor_node({**current, 'requirement_digest':'stale'})['route'] == 'repair'
    assert supervisor_node({**current, 'status':'unsupported'})['route'] == 'unsupported'
    question = {**current['draft'], 'questions':['사용자가 결정할 실제 조건은 무엇인가요?']}
    assert supervisor_node({**current, 'draft':question})['route'] == 'await_input'

    # A known task with recoverable settings must still reach the repair path.
    known = seed()
    known['draft']['workflow']['nodes'][2]['settings']['execution_plan']['sort'] = []
    known.update(validate_node(known), repair_count=1)
    assert supervisor_node(known)['route'] == 'repair'


def test_even_a_model_pass_and_user_confirmation_cannot_save_a_missing_sort(tmp_path):
    state=seed();state['draft']['workflow']['nodes'][2]['settings']['execution_plan']['sort']=[]
    state.update(confirmed=True,status='ready',verification_required=True,verification_route='continue_review')
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError,match='r3'):store.save_agent(state)
        assert not store.agents()
    finally:store.close()


def test_enabling_checks_cannot_approve_an_old_session_without_a_ledger(monkeypatch):
    state=seed();state.pop('requirement_required');state.pop('requirement_ledger')
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','enforce')
    assert validate_node(state)['status']!='ready'


def test_pending_questions_cannot_bypass_the_save_gate(tmp_path):
    state=seed();state.update(confirmed=True,status='ready')
    state['draft']['questions']=['진행할까요?']
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError,match='질문'):store.save_agent(state)
        assert not store.agents()
    finally:store.close()


def test_extraction_decoder_can_only_select_existing_sources_and_user_turns():
    from jsonschema import Draft202012Validator
    state=joined();wire={**state['requirement_ledger'],
        'tasks':[{k:v for k,v in t.items() if k not in ('id','depends_on','quote')} for t in state['requirement_ledger']['tasks']]}
    validator=Draft202012Validator(extraction_schema(1,['left-file','right-file']))
    validator.validate(wire)
    invalid=deepcopy(wire);invalid['tasks'][2]['spec']['left_sources']=['invented-id']
    assert list(validator.iter_errors(invalid))
    invalid=deepcopy(wire);invalid['tasks'][0]['source_message']=1
    assert list(validator.iter_errors(invalid))


def test_guessed_numeric_criteria_are_not_promoted_to_confirmed_request_values():
    state=seed();plan=deepcopy(state['requirement_ledger'])
    plan['tasks'][0]['spec']['formula']='qty * 999'
    with pytest.raises(ValueError,match='숫자'):validate_plan(plan,state)
    plan=deepcopy(state['requirement_ledger']);plan['tasks'][1]['spec']['filters'][0]['value']=999
    with pytest.raises(ValueError,match='숫자'):validate_plan(plan,state)


def test_experimental_generator_format_is_not_enabled_by_default(monkeypatch):
    from agent_builder.design.requirement_generation import generation_mode,output_budget
    monkeypatch.delenv('A4B_REQUIREMENT_GENERATION_MODE',raising=False)
    assert generation_mode()=='baseline'
    assert output_budget(['tool']*7,generation_mode())==4096
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE','bounded')
    assert output_budget(['tool']*7,generation_mode())==8192
    assert output_budget(['tool']*15,generation_mode())==8192
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE','invalid')
    with pytest.raises(ValueError):generation_mode()


class Extraction:
    def __init__(self,plan):self.plan=plan;self.calls=0
    def invoke(self,messages):
        payload=json.loads(messages[-1]['content'])
        assert {'user_messages','resources'}<=set(payload)
        if 'temporal_context' in payload:
            assert {'user_messages','resources','temporal_context','request_meaning',
                    'conversation','pending_questions','previous_requirements'}<=set(payload)
            assert set(payload)<={'user_messages','resources','temporal_context','request_meaning',
                                 'conversation','pending_questions','previous_requirements','service_connections',
                                 'available_capabilities','clarification_answers','execution_choice','confirmed_conditions'}
            assert payload['user_messages']==[m['content'] for m in payload['conversation'] if m['role']=='user']
            assert payload['temporal_context']['reference_time']
            assert payload['request_meaning'] is None
        assert 'current_draft' not in payload
        self.calls+=1
        wire={**self.plan,'tasks':[{k:v for k,v in t.items() if k not in ('id','depends_on','quote')} for t in self.plan['tasks']]}
        return dict(content=json.dumps(wire,ensure_ascii=False),metrics={'done_reason':'stop'})


def test_forward_references_are_ordered_without_losing_conditions_or_dependencies():
    state=seed();plan=deepcopy(state['requirement_ledger'])
    plan['tasks']=[plan['tasks'][1],plan['tasks'][0],*plan['tasks'][2:]]
    result=validate_plan(plan,state)
    assert result['tasks'][0]['id']=='r1'
    assert {t['id']:t for t in result['tasks']}=={t['id']:t for t in plan['tasks']}


def test_circular_requirements_and_constant_plus_column_are_rejected():
    state=seed();plan=deepcopy(state['requirement_ledger'])
    plan['tasks'][0]['depends_on']=['r3']
    with pytest.raises(ValueError,match='순환'):validate_plan(plan,state)
    plan=deepcopy(state['requirement_ledger'])
    plan['tasks'][1]['spec']['filters'][0]['compare_field']='amount'
    with pytest.raises(ValueError,match='하나만'):validate_plan(plan,state)


def test_decoder_rejects_mixed_constant_and_column_before_model_response_is_accepted():
    from jsonschema import Draft202012Validator
    schema=extraction_schema();plan=deepcopy(seed()['requirement_ledger'])
    for task in plan['tasks']:
        task.pop('id');task.pop('depends_on');task.pop('quote')
    Draft202012Validator(schema).validate(plan)
    plan['tasks'][1]['spec']['filters'][0]['compare_field']='amount'
    assert list(Draft202012Validator(schema).iter_errors(plan))


def test_graph_keeps_independent_requirements_through_repair_and_explicit_save(tmp_path):
    state=seed();correct=deepcopy(state['draft']);transport=Extraction(state['requirement_ledger'])
    # Start fresh; only the request extractor may create this ledger.
    for k in list(state):
        if k.startswith('requirement_'):state.pop(k)
    state['draft']=None
    def planner(s):
        raw=deepcopy(correct)
        if not s.get('repair_count'):raw['workflow']['nodes'][2]['settings']['execution_plan']['sort']=[]
        assert s['requirement_ledger']['tasks'][2]['spec']['kind']=='sort'
        return dict(draft=raw,repair_count=s.get('repair_count',0)+1)
    store=Storage(tmp_path);graph,connection=build_graph(store,conversation=planner,capability_mode='off',
        condition_mode='off',verifier_mode='off',requirement_mode='enforce',requirement_transport=transport)
    try:
        cfg={'configurable':{'thread_id':state['session_id']}}
        result=graph.invoke(state,cfg)
        assert result['status']=='ready' and result['repair_count']==2
        assert transport.calls==1
        assert result['requirement_ledger']==transport.plan
        saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
        assert saved['status']=='saved' and store.agents()
    finally:connection.close();store.close()


@pytest.mark.parametrize('failure',['invalid_source','partial_response','exception'])
def test_failed_extraction_stops_before_generation(failure):
    state=seed();plan=deepcopy(state['requirement_ledger'])
    if failure=='invalid_source':plan['tasks'][0]['source_message']=99
    for key in ('requirement_ledger','requirement_fingerprint','requirement_digest'):state.pop(key)
    transport=Extraction(plan)
    if failure=='partial_response':transport.invoke=lambda messages:dict(content=json.dumps(plan),metrics={'done_reason':'length'})
    if failure=='exception':transport.invoke=lambda messages:(_ for _ in ()).throw(ValueError('unavailable'))
    result=make_requirement_check('fixture',transport,mode='enforce')(state)
    assert result['requirement_route']=='stop' and result['status']=='validation_failed'
    assert result['requirement_ledger'] is None and not result['confirmed']


def test_empty_policy_is_rejected_and_extraction_format_repair_is_bounded():
    state=seed();plan=deepcopy(state['requirement_ledger']);transport=Extraction(plan)
    valid=transport.invoke([dict(content=json.dumps({'user_messages':[REQUEST],'resources':[]}))])
    invalid=json.loads(valid['content']);invalid['tasks'][-1]['spec'].update(missing_policy=None,duplicate_policy=None)
    class Repair:
        calls=0
        def invoke(self,messages):
            self.calls+=1
            return dict(content=json.dumps(invalid)) if self.calls==1 else valid
    active=Repair()
    for k in ('requirement_ledger','requirement_fingerprint','requirement_digest'):state.pop(k)
    result=make_requirement_check('fixture',active,mode='enforce')(state)
    assert result['requirement_route']=='plan' and active.calls==2
    assert result['trace'][-1]['model_calls']==2
