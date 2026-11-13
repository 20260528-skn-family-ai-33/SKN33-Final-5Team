"""PR 103 final review: conjunctions, delivery nouns, and missing-item scope."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.output_selection_request import output_selection, literal_numeric_filters, literal_or_filters
from agent_builder.design.numeric_filter_proof import guarantees_filter, numeric_filter_issues
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.request_guards import review_workflow_issues
from tests.design.test_feedback_requirement_extraction import state, packet
from tests.design.test_feedback_review_followups import review_flow
from tests.design.test_beta_feedback import request, DOC
from tests.design.test_builder import initial, draft
from tests.design.test_feedback_service_scenarios import order_draft, COLUMNS, ROWS, RULES
from tests.design.test_feedback_final_review import _run_csv
from tests.workflow_engine.test_workflow import bind, edge, saved


def sealed(current, raw):
    ledger=extract_plan(raw,current)
    return {**current,'requirement_required':True,'requirement_ledger':ledger,
            'requirement_fingerprint':ledger_fingerprint(current),'requirement_digest':plan_digest(ledger)}


@pytest.mark.parametrize('reply',['파일만 저장해줘','결과만 저장해줘','결과물만 저장해주세요'])
def test_delivery_only_reply_preserves_columns_and_csv(tmp_path,monkeypatch,reply):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()};current['messages'].append(dict(role='user',content=reply))
    current=sealed(current,packet())
    assert output_selection(current)==['주문번호','주문금액']
    fields,rows=_run_csv(tmp_path,current,deepcopy(order_draft('table-source')),COLUMNS,ROWS)
    assert fields==['주문번호','주문금액']
    assert [r['주문번호'] for r in rows]==['ORD-001','ORD-003']


@pytest.mark.parametrize('name',['파일','결과','산출물'])
def test_delivery_noun_can_still_name_a_real_column(name):
    current=deepcopy(state());current['resources'][0]['columns'].append(name)
    current['messages'].append(dict(role='user',content=f'{name}만 저장해줘'))
    assert output_selection(current)==[name]


@pytest.mark.parametrize('reply',['주문번호 열만 저장해줘','주문금액만 저장해줘'])
def test_explicit_and_previously_computed_columns_remain_selectable(reply):
    current=state();current['messages'].append(dict(role='user',content=reply))
    assert output_selection(current)==[reply.split()[0].removesuffix('만')]


def test_all_columns_only_is_not_a_column_called_all():
    current=state();current['messages'].append(dict(role='user',content='전체 열만 저장해줘'))
    assert output_selection(current) is None
    raw=packet();raw['tasks'][4]['spec']['fields']=COLUMNS+['주문금액']
    assert extract_plan(raw,current)
    with pytest.raises(ValueError,match='전체 열'):
        extract_plan(packet(),current)


def test_or_candidate_cannot_weaken_mandatory_threshold():
    raw=packet();raw['tasks'][2]['spec']['match']='any'
    with pytest.raises(ValueError,match='숫자 선별 조건'):
        extract_plan(raw,state())


@pytest.mark.parametrize('required',[False,True])
@pytest.mark.parametrize('change',['or','bypass','literal','overwrite'])
def test_actual_export_cannot_bypass_a_required_predicate(required,change,monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=sealed({**initial(),**state()},packet());current['requirement_required']=required
    config=deepcopy(order_draft('table-source'));nodes=config['workflow']['nodes']
    if change=='or':nodes[2]['settings']['execution_plan']['match']='any'
    elif change=='bypass':nodes[3]['bindings']=[bind('value','compute','table')]
    elif change=='literal':
        args=json.loads(nodes[-1]['settings']['arguments_json'])
        args['rows']=[{'주문번호':'WRONG','주문금액':1}]
        nodes[-1]['settings']['arguments_json']=json.dumps(args)
    else:
        overwrite=deepcopy(nodes[1]);overwrite['id']='overwrite'
        overwrite['bindings']=[bind('table','filter','table')]
        overwrite['settings']['calculation_formula']='수량*단가'
        nodes.insert(3,overwrite);nodes[4]['bindings']=[bind('value','overwrite','table')]
        config['workflow']['edges']=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]
    assert numeric_filter_issues(current,config)
    result=validate_node({**current,'draft':config})
    assert result['status']!='ready'
    assert any('최종 저장 경로' in issue for issue in result['issues'])


def test_single_predicate_any_is_equivalent_to_all():
    rule=RULES[1]
    assert guarantees_filter(dict(filters=[rule],match='any'),rule)
    assert guarantees_filter(dict(filters=[rule,rule],match='any'),rule)
    assert not guarantees_filter(dict(filters=RULES,match='any'),rule)


@pytest.mark.parametrize('use_or',[False,True])
def test_requested_boolean_semantics_reach_real_csv(tmp_path,monkeypatch,use_or):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()};raw=packet();config=deepcopy(order_draft('table-source'))
    if use_or:
        original=current['messages'][-1]['content']
        text=original.replace('완료 주문만 대상으로 매출은 수량×단가, 주문금액 100000 이상,',
                              '매출은 수량×단가, 완료 주문이거나 주문금액 100000 이상인 주문을 대상으로,')
        current['messages'][-1]['content']=text
        config['instructions']=text;config['requirements'][-1]['quote']=text
        raw['tasks'][2]['spec']['match']='any'
        config['execution_plan']['match']='any'
        config['workflow']['nodes'][2]['settings']['execution_plan']['match']='any'
    current=sealed(current,raw)
    fields,rows=_run_csv(tmp_path,current,config,COLUMNS,ROWS)
    assert fields==['주문번호','주문금액']
    assert [r['주문번호'] for r in rows]==(['ORD-001','ORD-002','ORD-003','ORD-004'] if use_or else ['ORD-001','ORD-003'])


@pytest.mark.parametrize('explicit',[False,True])
def test_explicit_later_or_revision_replaces_mandatory_threshold(tmp_path,monkeypatch,explicit):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current={**initial(),**state()};raw=packet();config=deepcopy(order_draft('table-source'))
    text=('제공하는 실행 조건: '+json.dumps(dict(selection=RULES,match='any'),ensure_ascii=False) if explicit
          else '완료 주문이거나 주문금액 100000 이상인 주문을 대상으로')
    current['messages'].append(dict(role='user',content=text))
    raw['tasks'][2].update(source_message=2,spec={**raw['tasks'][2]['spec'],'match':'any'})
    assert literal_numeric_filters(current)==[]
    current=sealed(current,raw)
    config['execution_plan']['match']='any'
    config['workflow']['nodes'][2]['settings']['execution_plan']['match']='any'
    fields,rows=_run_csv(tmp_path,current,config,COLUMNS,ROWS)
    assert [r['주문번호'] for r in rows]==['ORD-001','ORD-002','ORD-003','ORD-004']


@pytest.mark.parametrize('reply', [
    '"완료 주문이거나 주문금액 100000 이상인 주문을 대상으로"라는 예시를 설명해줘',
    '완료 주문이거나 주문금액 100000 이상인 주문을 대상으로 하는 조건은 뭐야?',
])
def test_or_example_or_question_does_not_cancel_mandatory_threshold(reply):
    current=state();current['messages'].append(dict(role='user',content=reply))
    assert literal_numeric_filters(current)==[RULES[1]]


@pytest.mark.parametrize('mistake',['stale_value','omitted','and'])
def test_or_revision_still_checks_the_new_numeric_operand_and_connective(mistake):
    current=state();current['messages'].append(dict(role='user',content='완료 주문이거나 주문금액 200000 이상인 주문을 대상으로'))
    raw=packet();spec=raw['tasks'][2]['spec'];spec['match']='any'
    spec['filters'][1]['value']=200000
    if mistake=='stale_value':spec['filters'][1]['value']=100000
    elif mistake=='omitted':spec['filters'].pop()
    else:spec['match']='all'
    with pytest.raises(ValueError,match='OR 숫자 조건'):
        extract_plan(raw,current)


def test_new_mandatory_threshold_replaces_earlier_alternative():
    current=state();current['messages'].append(dict(role='user',content='완료 주문이거나 주문금액 200000 이상인 주문을 대상으로'))
    current['messages'].append(dict(role='user',content='주문금액 100000 이상'))
    assert literal_numeric_filters(current)==[RULES[1]]
    assert extract_plan(packet(),current)


def test_later_cancellation_in_same_reply_removes_the_or_numeric_operand():
    current=state();current['messages'].append(dict(role='user',content=
        '완료 주문이거나 주문금액 200000 이상인 주문을 대상으로; 주문금액 조건을 취소해줘'))
    assert literal_numeric_filters(current)==[]
    assert literal_or_filters(current)==[]
    raw=packet();raw['tasks'][2]['spec']['filters'].pop()
    assert extract_plan(raw,current)


@pytest.mark.parametrize('bad_branch',[False,True])
def test_numeric_proof_checks_each_export_branch(bad_branch):
    from tests.design.test_feedback_nov13 import _branched_output
    config=_branched_output(COLUMNS+['주문금액'])['draft']
    if bad_branch:
        writer=next(n for n in config['workflow']['nodes'] if n['id']=='out_no')
        args=json.loads(writer['settings']['arguments_json'])
        args['rows']=[dict(주문번호='WRONG',주문금액=1)]
        writer['settings']['arguments_json']=json.dumps(args)
    assert bool(numeric_filter_issues(state(),config))==bad_branch


@pytest.mark.parametrize('text', ['누락된 항목이 있는 경우에만 사람 검토',
                                '누락된 모든 필드가 있는 경우에만 사람 검토',
                                '누락된 조항 또는 담당자가 있는 경우에만 사람 검토'])
def test_all_or_either_field_scope_is_ready(text,monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=request(text,resources=[DOC]);config=draft();workflow=review_flow(['조항','담당자'])
    config.update(instructions=text,document_ids=[DOC['id']],workflow=workflow,
                  tool_ids=[n['tool'] for n in workflow['nodes']],
                  requirements=[dict(description='조건부 검토',source_id='user',quote=text)])
    result=validate_node({**current,'draft':config})
    assert result['status']=='ready',result['issues']


@pytest.mark.parametrize('text,fields,field',[
    ('누락된 조항 또는 담당자가 있는 경우에만 사람 검토',['조항','담당자','주소'],None),
    ('누락된 조항 또는 담당자가 있는 경우에만 사람 검토',['조항','담당자'],'조항'),
    ('누락된 조항 또는 주소가 있는 경우에만 사람 검토',['조항','담당자'],None),
    ('누락된 조항과 담당자가 모두 있는 경우에만 사람 검토',['조항','담당자'],None),
    ('누락된 항목이 있는 경우에만 사람 검토',['항목','담당자'],None),
    ('누락된 조항이 있는 경우에만 사람 검토',['조항','담당자'],None),
])
def test_subset_unknown_and_conjunction_scopes_never_widen_to_all(text,fields,field):
    assert review_workflow_issues(request(text),dict(workflow=review_flow(fields,field)))


@pytest.mark.parametrize('text', ['누락된 항목이 있는 경우에만 사람 검토',
                                '누락된 조항 또는 담당자가 있는 경우에만 사람 검토'])
@pytest.mark.parametrize('missing',[(False,False),(False,True),(True,False),(True,True)])
def test_generic_review_executes_either_missing_field_once(tmp_path,text,missing):
    from workflow_engine.runner import run_workflow,execute_node
    workflow=review_flow(['조항','담당자'])
    assert not review_workflow_issues(request(text),dict(workflow=workflow))
    store,identifier,asset=saved(tmp_path,workflow)
    def executor(n,s,st,c,llm):
        if n['id']=='analysis':
            return {'records':[dict(field=f,value=None if absent else '확인됨',source_id=None,quote=None)
                               for f,absent in zip(['조항','담당자'],missing)]}
        return execute_node(n,s,st,c,llm)
    try:
        run=run_workflow(store,identifier,'실행','',file_ids=[asset['id']],executor=executor,llm=object())
        assert run['status']==('needs_input' if any(missing) else 'succeeded'),run.get('error')
        if any(missing):
            run=run_workflow(store,identifier,'실행','',run_id=run['id'],answer='확인 완료',executor=executor,llm=object())
            assert run['status']=='succeeded',run.get('error')
            assert sum(s['node']=='review' for s in run['steps'])==1
        else:assert not any(s['node']=='review' for s in run['steps'])
    finally:store.close()
