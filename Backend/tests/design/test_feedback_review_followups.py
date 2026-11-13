"""PR 103: preserve criteria across replies and bind missing-value review scope."""
import json
from copy import deepcopy

import pytest

from agent_builder.design.output_selection_request import output_selection, literal_numeric_filters, literal_output_compute
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.request_guards import review_workflow_issues
from tests.design.test_builder import initial
from tests.design.test_beta_feedback import request
from tests.design.test_feedback_requirement_extraction import state, packet
from tests.design.test_feedback_service_scenarios import order_draft, COLUMNS, ROWS
from tests.design.test_feedback_final_review import _run_csv
from tests.workflow_engine.test_workflow import node, bind, edge, saved


@pytest.mark.parametrize('reply', ['주문번호 오름차순으로 정렬해줘', '기준값이 뭐야?',
                                  '주문번호 열만 저장해줘', '전체 열을 저장해줘'])
def test_later_reply_cannot_erase_numeric_filter(reply):
    current=state(); current['messages'].append(dict(role='user',content=reply))
    bad=packet(); bad['tasks'][2]['spec']['filters'].pop()
    if reply=='주문번호 열만 저장해줘':
        bad['tasks'][4].update(source_message=2, spec=dict(kind='select_fields',fields=['주문번호']))
    elif reply=='전체 열을 저장해줘':
        del bad['tasks'][4]
    with pytest.raises(ValueError,match='숫자 선별 조건'):
        extract_plan(bad,current)


@pytest.mark.parametrize('reply', ['주문번호 열만 저장해줘', '주문번호만 저장해줘',
                                  '주문번호만 결과.csv로 저장해줘'])
def test_single_column_revision_replaces_previous_selection(reply):
    current=state(); current['messages'].append(dict(role='user',content=reply))
    assert output_selection(current)==['주문번호']
    good=packet(); good['tasks'][4].update(source_message=2,spec=dict(kind='select_fields',fields=['주문번호']))
    assert extract_plan(good,current)
    with pytest.raises(ValueError,match='최종 출력 열'):
        extract_plan(packet(),current)
    # The projected task must cite the new instruction, not the old two-column list.
    del good['tasks'][4]
    projected=next(t for t in extract_plan(good,current)['tasks'] if t['spec']['kind']=='select_fields')
    assert projected['source_message']==2 and projected['quote']==reply


def test_explicit_new_threshold_replaces_old_value():
    current=state(); current['messages'].append(dict(role='user',content='주문금액 200000 이상'))
    good=packet(); good['tasks'][2]['spec']['filters'][1]['value']=200000
    assert literal_numeric_filters(current)[0]['value']==200000
    assert extract_plan(good,current)
    with pytest.raises(ValueError,match='숫자 선별 조건'):
        extract_plan(packet(),current)


@pytest.mark.parametrize('reply', ['금액 조건은 무슨 뜻이야?', '"주문금액 200000 이상"이라는 예시를 설명해줘'])
def test_question_or_quoted_example_does_not_replace_threshold(reply):
    current=state(); current['messages'].append(dict(role='user',content=reply))
    assert literal_numeric_filters(current)[0]['value']==100000
    assert literal_output_compute(current)==dict(kind='compute',formula='수량*단가',output_column='주문금액')


def test_explicit_cancellation_does_not_restore_old_numeric_requirement():
    current=state(); current['messages'].append(dict(role='user',content='주문금액 조건을 취소해줘'))
    current['messages'].append(dict(role='user',content='주문번호 오름차순으로 정렬해줘'))
    assert literal_numeric_filters(current)==[]
    raw=packet(); raw['tasks'][2]['spec']['filters'].pop()
    assert extract_plan(raw,current)


def test_new_task_does_not_inherit_previous_numeric_contract():
    current=state(); current['messages'].append(dict(role='user',content='새 작업: 주문번호·주문상태를 상태.csv로 저장해줘'))
    assert output_selection(current)==['주문번호','주문상태']
    assert literal_numeric_filters(current)==[]


@pytest.mark.parametrize('reply', ['주문번호만 저장하지 마.', '알수없는열만 저장해줘'])
def test_unknown_or_cancelled_single_selection_never_restores_two_columns(reply):
    current=state(); current['messages'].append(dict(role='user',content=reply))
    assert output_selection(current)!=['주문번호','주문금액']


@pytest.mark.parametrize('required',[False,True])
def test_old_two_column_writer_cannot_pass_single_column_revision(monkeypatch,required):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()}
    current['messages'].append(dict(role='user',content='주문번호 열만 저장해줘'))
    raw=packet(); raw['tasks'][4].update(source_message=2,spec=dict(kind='select_fields',fields=['주문번호']))
    ledger=extract_plan(raw,current)
    current.update(requirement_required=required,requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),requirement_digest=plan_digest(ledger))
    result=validate_node({**current,'draft':deepcopy(order_draft('table-source'))})
    assert result['status']!='ready'
    assert any('출력 열' in issue for issue in result['issues'])


def test_all_columns_revision_sends_retained_threshold_to_model():
    from agent_builder.design.requirement_ledger import make_requirement_check
    current=state(); current['messages'].append(dict(role='user',content='전체 열을 저장해줘'))
    raw=packet(); del raw['tasks'][4]
    class Transport:
        def invoke(self,messages):
            context=json.loads(messages[-1]['content'])
            assert context['explicit_numeric_filters'][0]['value']==100000
            return {'content':json.dumps(raw,ensure_ascii=False)}
    assert make_requirement_check('fixture',Transport(),'enforce')(current)['requirement_route']=='plan'


@pytest.mark.parametrize('reply',['주문번호 열만 저장해줘','전체 열을 저장해줘'])
def test_hidden_computed_filter_column_keeps_formula_and_name(reply):
    from agent_builder.design.output_selection_request import constrain_literal_selection
    from agent_builder.design.requirement_ledger import extraction_schema
    from jsonschema import Draft202012Validator
    current=state();current['messages'].append(dict(role='user',content=reply))
    assert literal_output_compute(current)==dict(kind='compute',formula='수량*단가',output_column='주문금액')
    schema=constrain_literal_selection(extraction_schema(3,['table-source']),current)
    validator=Draft202012Validator(schema['$defs']['Compute'])
    assert validator.is_valid(dict(kind='compute',formula='수량*단가',output_column='주문금액'))
    assert not validator.is_valid(dict(kind='compute',formula='수량*단가',output_column='매출'))


@pytest.mark.parametrize('single',[False,True])
def test_followup_survives_save_and_csv_execution(tmp_path,monkeypatch,single):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()}
    reply='주문번호 열만 저장해줘' if single else '주문번호 오름차순으로 정렬해줘'
    current['messages'].append(dict(role='user',content=reply))
    selected=['주문번호'] if single else ['주문번호','주문금액']
    raw=packet(); raw['tasks'][4].update(source_message=2 if single else 1,spec=dict(kind='select_fields',fields=selected))
    ledger=extract_plan(raw,current)
    current.update(requirement_required=True,requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),requirement_digest=plan_digest(ledger))
    config=deepcopy(order_draft('table-source'))
    if single:
        config['instructions']+='\n'+reply
        config['requirements'].append(dict(description='출력 변경',source_id='user',quote=reply))
    for n in config['workflow']['nodes'][-2:]:
        args=json.loads(n['settings']['arguments_json']); args['columns']=selected
        n['settings']['arguments_json']=json.dumps(args)
    bad=deepcopy(config); bad['execution_plan']['filters'].pop()
    assert validate_node({**current,'draft':bad})['status']!='ready'
    fields,rows=_run_csv(tmp_path,current,config,COLUMNS,ROWS)
    assert fields==selected
    assert [r['주문번호'] for r in rows]==['ORD-001','ORD-003']


def filtered_before_compute_packet():
    raw=packet(); tasks=raw['tasks']
    categorical=deepcopy(tasks[2]); categorical['spec']['filters']=categorical['spec']['filters'][:1]
    tasks[2]['spec']['filters']=tasks[2]['spec']['filters'][1:]
    raw['tasks']=[tasks[0],categorical,*tasks[1:]]
    return raw


def test_real_model_filter_before_compute_keeps_projection_decoder(monkeypatch):
    from contracts.models import AgentDraft
    from tests.design.test_beta_feedback import native_schema
    from agent_builder.design.operation_steps import schema_for
    from agent_builder.design.requirement_generation import required_operation_variants
    from agent_builder.design.projected_table_decoder import constrain_projected_table
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    current=state(); ledger=extract_plan(filtered_before_compute_packet(),current)
    current.update(requirement_required=True,requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),requirement_digest=plan_digest(ledger))
    specs=[t['spec'] for t in ledger['tasks']]
    base=native_schema(); original=AgentDraft.model_json_schema()
    base['$defs']['WorkflowNode']['anyOf']+=required_operation_variants(specs,original['$defs']['WorkflowNode'],original['$defs']['Binding'],set(COLUMNS))
    constrained=constrain_projected_table(schema_for(base),current)
    slots=constrained['$defs']['WorkflowDefinition']['properties']['next_steps']['properties']
    variants=[slot['anyOf'][0]['properties'] for slot in slots.values()]
    assert [v['tool']['enum'][0] for v in variants]==[
        'filter_sort','calculate_columns','filter_sort','filter_sort','json_transform','filter_sort','export_result']
    from jsonschema import Draft202012Validator
    from contracts.models import ExecutionPlan
    checker=Draft202012Validator(variants[0]['settings']['properties']['execution_plan'])
    first=ExecutionPlan.model_validate(dict(filters=specs[1]['filters'],match='all',sort=[],
        missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json')).model_dump(mode='json')
    assert checker.is_valid(first)
    first['filters']=specs[3]['filters']
    assert not checker.is_valid(first)
    assert variants[-2]['settings']['properties']['operation']['enum']==['select_columns']
    assert variants[-1]['settings']['properties']['operation']['enum']==['write_csv']


def test_filter_before_compute_executes_without_touching_excluded_bad_operands(tmp_path,monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()}; ledger=extract_plan(filtered_before_compute_packet(),current)
    current.update(requirement_required=True,requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),requirement_digest=plan_digest(ledger))
    config=deepcopy(order_draft('table-source')); workflow=config['workflow']; nodes=workflow['nodes']
    before=deepcopy(nodes[2]); before['id']='completed'
    before['bindings']=[bind('table','read','table')]
    before['settings']['execution_plan']['filters']=before['settings']['execution_plan']['filters'][:1]
    before['settings']['execution_plan']['sort']=[]
    nodes[1]['bindings']=[bind('table','completed','table')]
    nodes[2]['settings']['execution_plan']['filters']=nodes[2]['settings']['execution_plan']['filters'][1:]
    nodes.insert(1,before)
    workflow['edges']=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]
    rows=deepcopy(ROWS); next(r for r in rows if r['주문상태']=='취소')['수량']='계산 불가'
    fields,actual=_run_csv(tmp_path,current,config,COLUMNS,rows)
    assert fields==['주문번호','주문금액']
    assert [r['주문번호'] for r in actual]==['ORD-001','ORD-003']


def review_flow(fields,field=None):
    nodes=[node('read','read_document'),
           node('analysis','extract_information',[bind('documents','read','documents')],fields=fields),
           node('check','condition',[bind('records','analysis','records')],condition_operator='has_missing',condition_field=field),
           node('review','ask_user',[bind('records','check','records')],question='검토 의견?'),node('end','finish')]
    return dict(entry='read',nodes=nodes,edges=[edge('read','analysis'),edge('analysis','check'),
                edge('check','review','yes'),edge('check','end','no'),edge('review','end')])


@pytest.mark.parametrize('fields,field,valid',[
    (['조항','담당자'],None,False), (['조항','담당자'],'조항',True),
    (['조항','담당자'],'담당자',False), (['조항'],None,True),
    (['조항','항'],'항',False), (['담당자'],None,False),
])
def test_missing_review_checks_requested_field_scope(fields,field,valid):
    current=request('누락된 조항이 있는 경우에만 사람 검토')
    assert (not review_workflow_issues(current,dict(workflow=review_flow(fields,field))))==valid


@pytest.mark.parametrize('field',[None,'담당자','조항'])
def test_field_scope_is_checked_at_design_validation(field,monkeypatch):
    from tests.design.test_builder import draft
    from tests.design.test_beta_feedback import DOC
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    text='누락된 조항이 있는 경우에만 사람 검토'
    current=request(text,resources=[DOC])
    config=draft(); workflow=review_flow(['조항','담당자'],field)
    config.update(instructions=text,document_ids=[DOC['id']],workflow=workflow,
                  tool_ids=[n['tool'] for n in workflow['nodes']],
                  requirements=[dict(description='조건부 검토',source_id='user',quote=text)])
    result=validate_node({**current,'draft':config})
    assert (result['status']=='ready')==(field=='조항'),result['issues']


@pytest.mark.parametrize('clause_missing,owner_missing',[(False,True),(True,False),(True,True),(False,False)])
def test_review_pauses_for_clause_only(tmp_path,clause_missing,owner_missing):
    from workflow_engine.runner import run_workflow,execute_node
    workflow=review_flow(['조항','담당자'],'조항')
    assert not review_workflow_issues(request('누락된 조항이 있는 경우에만 사람 검토'),dict(workflow=workflow))
    store,identifier,asset=saved(tmp_path,workflow)
    def executor(n,s,st,c,llm):
        if n['id']=='analysis':
            return {'records':[dict(field=f,value=None if missing else '확인됨',source_id=None,quote=None)
                               for f,missing in [('조항',clause_missing),('담당자',owner_missing)]]}
        return execute_node(n,s,st,c,llm)
    try:
        run=run_workflow(store,identifier,'실행','',file_ids=[asset['id']],executor=executor,llm=object())
        assert run['status']==('needs_input' if clause_missing else 'succeeded'),run.get('error')
        if clause_missing:
            run=run_workflow(store,identifier,'실행','',run_id=run['id'],answer='조항 확인',executor=executor,llm=object())
            assert run['status']=='succeeded',run.get('error')
            assert sum(s['node']=='review' for s in run['steps'])==1
        else:
            assert not any(s['node']=='review' for s in run['steps'])
    finally:store.close()
