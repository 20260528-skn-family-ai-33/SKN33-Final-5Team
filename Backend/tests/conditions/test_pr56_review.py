"""PR #56 review: valid requests must survive validation, saving and execution."""
from copy import deepcopy
import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import extract_plan
from agent_builder.design.table_step_order import filter_dedup_order
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import checked, proposal, state, workflow_draft
from tests.conditions.test_filter_dedup_order import (
    DEDUP_FIRST, ROWS, draft_for, requirements, seal, session,
)
from tests.workflow_engine.test_workflow import bind, edge, node
from workflow_engine.runner import run_workflow


def extracted(s, raw):
    raw=deepcopy(raw)
    for task in raw['tasks']:
        for key in ('id','depends_on','quote'):
            task.pop(key)
    return extract_plan(raw,s)


def ready(s):
    result=validate_node(s)
    assert result['issues']==[]
    assert result['status']=='ready'
    s.update(result,confirmed=True)
    require_save_ready(s)


def execute(s,tmp_path,rows):
    ready(s)
    store=Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0],rows)
        identifier=store.save_agent(s)
        return run_workflow(store,identifier,'실행','',file_ids=['table'])
    finally:
        store.close()


@pytest.mark.parametrize('enforced',[False,True])
@pytest.mark.parametrize('requirements_enabled',[False,True])
def test_keep_duplicates_remains_savable_and_preserves_rows(enforced,requirements_enabled,tmp_path,monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    s=state('비용 100만원 초과만 선별한 뒤 중복은 유지해 JSON으로 저장해줘. 빈 비용은 오류.')
    s['resources']=deepcopy(session()['resources'])
    s.update(condition_enforced=enforced,confirmed=True)
    s['condition_ledger']=checked(s,proposal(s))
    s['draft']=workflow_draft(s)
    s['draft']['document_ids']=['table']
    s['draft']['workflow']['nodes'][0]['settings']['file_ids']=['table']
    if requirements_enabled:
        raw=requirements(s)
        raw['tasks'][1]['spec'].update(missing_policy='error',duplicate_policy='keep',duplicate_field=None)
        seal(s,extracted(s,raw))
    assert filter_dedup_order(s) is None
    rows=[ROWS[0],{'거래ID':'A','비용':3000000},ROWS[1]]
    result=execute(s,tmp_path,rows)
    assert result['status']=='succeeded'
    assert json.loads(result['artifact']['content'])==rows[:2]


@pytest.mark.parametrize('followup',[
    '선별하지 말고 거래ID별 마지막 행 선택만 해줘.',
    '선별은 취소하고 거래ID별 마지막 행만 남겨줘.',
])
@pytest.mark.parametrize('enforced',[False,True])
def test_cancelled_filter_releases_old_order(followup,enforced):
    s=session()
    s['messages'].append(dict(role='user',content=followup))
    s['condition_enforced']=enforced
    # The current extracted condition contract retains the unchanged duplicate
    # policy and replaces the cancelled filter operation.
    s['condition_ledger']['operations']=['deduplicate']
    s['condition_ledger']['slots']={k:v for k,v in s['condition_ledger']['slots'].items()
                                  if not k.startswith('filter.')}
    s['draft']=draft_for(s)
    s['draft']['execution_plan']['filters']=[]
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters']=[]
    assert filter_dedup_order(s) is None
    ready(s)


@pytest.mark.parametrize('followup',[
    '중복은 유지해.', '중복 제거하지 말고 비용 선별만 해줘.',
])
def test_cancelled_deduplication_releases_old_order(followup):
    s=session()
    s['messages'].append(dict(role='user',content=followup))
    s['condition_enforced']=False
    s['draft']=workflow_draft(s)
    s['draft']['document_ids']=['table']
    s['draft']['workflow']['nodes'][0]['settings']['file_ids']=['table']
    assert filter_dedup_order(s) is None
    ready(s)


def three_steps(requirements_enabled=False):
    s=session('비용 100만원 초과만 선별한 뒤 거래ID별 마지막 행을 선택하고 상태가 완료인 행만 필터링해 JSON으로 저장해줘. 빈 비용은 오류.')
    s['resources'][0]['columns'].append('상태')
    # Multiple filters use the general requirement contract.
    s['condition_enforced']=False
    s['draft']=draft_for(s,'filter_first')
    plan=deepcopy(s['draft']['execution_plan'])
    plan.update(filters=[dict(field='상태',operator='eq',value='완료',compare_field=None,value_type='text')],
                duplicate_policy='keep',duplicate_field=None,missing_policy='keep')
    nodes=s['draft']['workflow']['nodes']
    nodes.insert(-1,node('status_filter','filter_sort',[bind('table','dedup','table')],execution_plan=plan))
    nodes[-1]['bindings']=[bind('table','status_filter','table')]
    s['draft']['workflow']['edges']=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]
    if requirements_enabled:
        raw=requirements(s)
        raw['tasks'].insert(2,dict(id='unused',depends_on=[],quote='',description='상태 선별',source_message=0,
                                  spec=dict(kind='filter',filters=plan['filters'],match='all')))
        seal(s,extracted(s,raw))
    return s


@pytest.mark.parametrize('requirements_enabled',[False,True])
def test_individual_filter_stages_keep_the_requested_position(requirements_enabled,tmp_path):
    s=three_steps(requirements_enabled)
    rows=[{'거래ID':'A','비용':2000000,'상태':'완료'},
          {'거래ID':'A','비용':3000000,'상태':'대기'},
          {'거래ID':'B','비용':4000000,'상태':'완료'},
          {'거래ID':'B','비용':100,'상태':'완료'}]
    result=execute(s,tmp_path,rows)
    assert result['status']=='succeeded'
    assert json.loads(result['artifact']['content'])==[rows[2]]


@pytest.mark.parametrize('requirements_enabled',[False,True])
@pytest.mark.parametrize('fault',['status_before_dedup','bypass_cost'])
def test_wrong_individual_filter_position_is_still_rejected(requirements_enabled,fault):
    s=three_steps(requirements_enabled)
    nodes=s['draft']['workflow']['nodes']
    if fault=='status_before_dedup':
        nodes[2]['bindings']=[bind('table','status_filter','table')]
        nodes[3]['bindings']=[bind('table','filter','table')]
        nodes[4]['bindings']=[bind('table','dedup','table')]
        order=['read','filter','status_filter','dedup','out']
        s['draft']['workflow']['edges']=[edge(a,b) for a,b in zip(order,order[1:])]
    else:
        nodes[2]['bindings']=[bind('table','read','table')]
    assert any('순서' in issue for issue in validate_node(s)['issues'])
    with pytest.raises(ValueError,match='순서'):
        require_save_ready(s)


def test_unused_correct_chain_cannot_hide_reversed_saved_result():
    s=session()
    s['draft']=draft_for(s,'filter_first')
    nodes=s['draft']['workflow']['nodes']
    nodes.insert(-1,node('bypass','filter_sort',[bind('table','read','table')],
                         execution_plan=deepcopy(s['draft']['execution_plan'])))
    nodes[-1]['bindings']=[bind('table','bypass','table')]
    s['draft']['workflow']['edges']=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]
    assert any('순서' in issue for issue in validate_node(s)['issues'])
    with pytest.raises(ValueError,match='순서'):
        require_save_ready(s)


def combined_policy(filter_first=True):
    s=session() if filter_first else session(DEDUP_FIRST)
    raw=requirements(s,filter_first)
    next(t for t in raw['tasks'] if t['spec']['kind']=='policy')['spec']['missing_policy']='error'
    seal(s,extracted(s,raw))
    s.update(compile_conditions(s))
    s.update(audit_conditions(s))
    return s


@pytest.mark.parametrize('filter_first',[True,False])
def test_combined_policy_covers_actual_stages_and_saved_execution(filter_first,tmp_path):
    s=combined_policy(filter_first)
    assert not s['trace'][-1]['issues']
    report=coverage_report(s,s['draft'])
    assert not report['issues']
    policy=next(t for t in report['tasks'] if t['kind']=='policy')
    assert len(policy['node_ids'])==(2 if filter_first else 1)
    result=execute(s,tmp_path,ROWS)
    assert result['status']=='succeeded'
    assert json.loads(result['artifact']['content'])==(ROWS[:1] if filter_first else [])


@pytest.mark.parametrize('fault',['missing_policy','wrong_duplicate','detached_policy','no_op_error'])
def test_split_policy_does_not_accept_missing_or_disconnected_guarantees(fault):
    s=combined_policy()
    nodes=s['draft']['workflow']['nodes']
    if fault=='wrong_duplicate':
        nodes[2]['settings']['execution_plan']['duplicate_policy']='first'
    else:
        nodes[1]['settings']['execution_plan']['missing_policy']='keep'
        if fault in ('detached_policy','no_op_error'):
            plan=deepcopy(nodes[1]['settings']['execution_plan'])
            plan['missing_policy']='error'
            if fault=='no_op_error':
                nodes[2]['settings']['execution_plan']['missing_policy']='error'
            else:
                nodes.append(node('detached','filter_sort',[bind('table','read','table')],execution_plan=plan))
    assert coverage_report(s,s['draft'])['issues']
    with pytest.raises(ValueError,match='요구사항'):
        require_save_ready(s)


def test_split_missing_error_is_enforced_at_runtime(tmp_path):
    s=combined_policy()
    result=execute(s,tmp_path,[{'거래ID':'A','비용':None},ROWS[0]])
    assert result['status']=='failed'


def test_no_op_keep_on_dedup_cannot_cover_a_wrong_filter_policy():
    s=combined_policy()
    s['messages'][0]['content']=s['messages'][0]['content'].replace('빈 비용은 오류','빈 비용은 유지')
    raw=requirements(s)
    raw['tasks'][1]['spec']['missing_policy']='keep'
    seal(s,extracted(s,raw))
    assert coverage_report(s,s['draft'])['issues']
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['missing_policy']='keep'
    assert not coverage_report(s,s['draft'])['issues']


def test_bounded_deep_schema_has_a_valid_unique_id_workflow(monkeypatch):
    from agent_builder.design.nodes import conversation
    from agent_builder.design.requirement_generation import generation_requirements
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE','bounded')
    s=combined_policy(False)
    s['node_library']=[]
    captured={}
    class Captured(Exception): pass
    def capture(**kwargs):
        captured.update(kwargs['response_format'].schema)
        raise Captured()
    monkeypatch.setattr(conversation,'create_deep_agent',capture)
    with pytest.raises(Captured):
        conversation.make_conversation('','fixture',lambda:object())(s)
    assert generation_requirements(s)[1]==['read_table','filter_sort','write_result']
    bounds=captured['$defs']['WorkflowDefinition']['properties']['nodes']
    assert bounds['minItems']==bounds['maxItems']==3
    graph=deepcopy(s['draft']['workflow'])
    ids={n['id']:n['tool']+'_1' for n in graph['nodes']}
    graph['entry']=ids[graph['entry']]
    variants=captured['$defs']['WorkflowNode']['anyOf']
    for n in graph['nodes']:
        n['id']=ids[n['id']]
        for binding in n['bindings']:binding['node_id']=ids[binding['node_id']]
        variant=next(v for v in variants if v['properties']['tool']['enum']==[n['tool']])
        n['settings']={k:n['settings'][k] for k in variant['properties']['settings']['required']}
    for e in graph['edges']:
        e['source']=ids[e['source']];e['target']=ids[e['target']]
    Draft202012Validator({'$ref':'#/$defs/WorkflowDefinition','$defs':captured['$defs']}).validate(graph)
    # The real planner restores compatibility defaults after decoding.
    for n in graph['nodes']:
        n['settings']={**conversation.SETTINGS_DEFAULTS,**n['settings']}
    s['draft']['workflow']=graph
    ready(s)


def test_quoted_cancellation_does_not_reset_order_and_new_order_can_restore_it():
    s=session()
    s['messages'].append(dict(role='user',content='문서의 “선별하지 말고 중복은 유지해”라는 문구는 예시야.'))
    assert filter_dedup_order(s)=='filter_first'
    s['messages'].append(dict(role='user',content='선별은 취소해.'))
    assert filter_dedup_order(s) is None
    s['messages'].append(dict(role='user',content=DEDUP_FIRST))
    assert filter_dedup_order(s)=='dedup_first'


def test_later_cancellation_in_same_turn_wins():
    s=session()
    s['messages'][0]['content']+=' 정정할게. 중복은 유지해.'
    assert filter_dedup_order(s) is None


def test_negated_keep_does_not_cancel_requested_deduplication():
    s=session()
    s['condition_enforced']=False
    s['messages'][0]['content']='비용 100만원 초과만 선별한 뒤 중복은 유지하지 말고 거래ID별 마지막 행을 선택해 JSON으로 저장해줘. 빈 비용은 오류.'
    assert filter_dedup_order(s)=='filter_first'
    s['draft']=draft_for(s)
    with pytest.raises(ValueError,match='순서'):
        require_save_ready(s)


@pytest.mark.parametrize('reverse_filters',[False,True])
def test_repeated_column_filters_bind_their_own_thresholds(reverse_filters):
    s=three_steps()
    s['messages'][0]['content']='비용 100만원 초과만 선별한 뒤 거래ID별 마지막 행을 선택하고 비용 300만원 미만만 필터링해 JSON으로 저장해줘. 빈 비용은 오류.'
    s['draft']['requirements'][0]['quote']=s['messages'][0]['content']
    nodes=s['draft']['workflow']['nodes']
    nodes[3]['settings']['execution_plan']['filters']=[dict(field='비용',operator='lt',value=3000000,
                                                          compare_field=None,value_type='number')]
    if reverse_filters:
        a=nodes[1]['settings']['execution_plan'];b=nodes[3]['settings']['execution_plan']
        a['filters'],b['filters']=b['filters'],a['filters']
        assert any('순서' in x for x in validate_node(s)['issues'])
    else:
        ready(s)


def test_repeated_ledger_stages_release_fixed_ids_and_shared_plan(monkeypatch):
    from agent_builder.design.nodes import conversation
    from agent_builder.design.planner_constraints import table_plan_scope
    from agent_builder.design.requirement_generation import generation_requirements
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE','bounded')
    s=session(DEDUP_FIRST)
    s['resources'][0]['columns'].append('상태')
    s['messages'][0]['content']+=' 상태가 완료인 행만 필터링해줘.'
    raw=requirements(s,False)
    second=dict(kind='filter',filters=[dict(field='상태',operator='eq',value='완료',
                                           compare_field=None,value_type='text')],match='all')
    raw['tasks'].insert(2,dict(id='unused',depends_on=[],quote='',description='상태 선별',source_message=0,spec=second))
    seal(s,extracted(s,raw))
    assert generation_requirements(s)[1]==['read_table','filter_sort','filter_sort','write_result']
    assert not table_plan_scope(s)
    s['node_library']=[]
    captured={}
    class Captured(Exception): pass
    def capture(**kwargs):
        captured.update(kwargs['response_format'].schema)
        raise Captured()
    monkeypatch.setattr(conversation,'create_deep_agent',capture)
    with pytest.raises(Captured):
        conversation.make_conversation('','fixture',lambda:object())(s)
    native=next(v for v in captured['$defs']['WorkflowNode']['anyOf']
                if v['properties']['tool']['enum']==['filter_sort'])
    assert 'enum' not in native['properties']['id']
    plan=deepcopy(workflow_draft(s)['execution_plan'])
    plan.update(filters=second['filters'],duplicate_policy='keep',duplicate_field=None,missing_policy='keep')
    Draft202012Validator({'$ref':'#/$defs/ExecutionPlan','$defs':captured['$defs']}).validate(plan)
