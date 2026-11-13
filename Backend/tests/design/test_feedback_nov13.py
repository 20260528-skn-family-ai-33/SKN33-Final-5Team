"""Keep later turns, negations and branch outcomes in PR 103 request guards."""
from copy import deepcopy
import json
import pytest

from agent_builder.conditions.condition_check import computed_table_request
from agent_builder.design.document_rows_template import document_rows_reply, document_rows_applies
from agent_builder.design.request_guards import unary_filter_issue, review_workflow_issues
from agent_builder.design.nodes.validation import validate_node
from tests.design.test_beta_feedback import request
from tests.design.test_document_rows_template import DOC
from tests.design.test_feedback_final_review import cancelled_state
from tests.design.test_feedback_requirement_extraction import state,packet
from tests.design.test_feedback_service_scenarios import order_draft,COLUMNS
from tests.workflow_engine.test_workflow import node,bind,edge


@pytest.mark.parametrize('code',['SKU-100','ABC-100','SKU+100','SKU/100'])
def test_quoted_cell_codes_never_bypass_condition_checks(code):
    current=request(f'SKU가 "{code}"인 행만 남겨서 CSV로 저장해줘',
                    resources=[dict(id='t',kind='table',columns=['SKU'])])
    assert not computed_table_request(current)


def test_quoted_column_identifiers_still_allow_real_computation():
    current=request('"수량" * "단가"를 계산해줘',resources=[dict(id='t',kind='table',columns=['수량','단가'])])
    assert computed_table_request(current)


@pytest.mark.parametrize('command',['중복 제거','중복 제거 후 저장','알 수 없는 별도 처리'])
def test_semicolon_instruction_stays_with_general_planner(command):
    current=request('첨부한 문서를 표로 정리해줘. 추출 항목: 품명, 금액; '+command,resources=[DOC])
    assert not document_rows_applies(current)
    assert document_rows_reply(current) is None


@pytest.mark.parametrize('text,expected',[
    ('이메일이 빈 행을 제외하지 말고 빈 행만 남겨줘','is_empty'),
    ('이메일이 빈 행을 제거하지 말고 빈 행만 남겨줘','is_empty'),
    ('이메일이 비어 있지 않은 행을 제외하지 말고 비어 있지 않은 행만 남겨줘','not_empty'),
    ('이메일이 빈 행을 남기지 말고 빈 행은 제외해줘','not_empty'),
])
def test_negated_selection_verb_cannot_invert_the_filter(text,expected):
    for operator in ('is_empty','not_empty'):
        assert (unary_filter_issue(dict(field='이메일',operator=operator),text) is None)==(operator==expected)


def _order_state(all_columns=False):
    from tests.design.test_builder import initial
    current=cancelled_state() if all_columns else {**initial(),**state()}
    config=deepcopy(order_draft('table-source'))
    config['instructions']=current['messages'][-1]['content']
    config['requirements'][-1]['quote']=current['messages'][-1]['content']
    current['draft']=config
    return current


@pytest.mark.parametrize('all_columns',[True,False])
@pytest.mark.parametrize('followup',['주문번호 오름차순으로 정렬해줘','출력 열이 뭐야?','주문금액 열은 무슨 뜻이야?'])
def test_followup_does_not_erase_output_requirements(all_columns,monkeypatch,followup):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=_order_state(all_columns)
    if not all_columns:
        current['draft']['workflow']['nodes'][-1]['settings']['arguments_json']=json.dumps(dict(columns=['주문번호'],path='out.csv'))
    assert validate_node(current)['status']!='ready'
    current['messages'] += [dict(role='assistant',content='정렬 순서는 어떻게 할까요?'),dict(role='user',content=followup)]
    assert validate_node(current)['status']!='ready'


@pytest.mark.parametrize('columns',[['주문번호'],['주문금액','주문번호']])
@pytest.mark.parametrize('enforce',[False,True])
def test_named_output_checks_writer_headers(columns,enforce,monkeypatch):
    from agent_builder.design.requirement_ledger import extract_plan,ledger_fingerprint,plan_digest
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=_order_state()
    ledger=extract_plan(packet(),current)
    current.update(requirement_required=enforce,requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),requirement_digest=plan_digest(ledger))
    current['draft']['workflow']['nodes'][-1]['settings']['arguments_json']=json.dumps(dict(columns=columns,path='out.csv'))
    assert validate_node(current)['status']!='ready'


@pytest.mark.parametrize('selected',[COLUMNS+['주문금액'],['주문번호','주문금액']])
def test_every_csv_branch_checks_the_output_columns(selected,monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=_branched_output(selected)
    result=validate_node(current)
    assert (result['status']=='ready')==(len(selected)==5), result['issues']


def _branched_output(selected):
    current=_order_state(True); workflow=current['draft']['workflow']; nodes=workflow['nodes']
    nodes[-2]['settings']['arguments_json']=json.dumps(dict(columns=selected))
    writer=nodes.pop(); writer['settings']['arguments_json']=json.dumps(dict(columns=selected,path='yes.csv'))
    other=deepcopy(writer); other['id']='out_no'; other['settings']['arguments_json']=json.dumps(dict(columns=selected,path='no.csv'))
    nodes += [node('check','condition',[bind('records','columns','records')],condition_operator='has_missing'),writer,other]
    workflow['edges'][-1]=edge('columns','check')
    workflow['edges'] += [edge('check','out','yes'),edge('check','out_no','no')]
    current['draft']['tool_ids'].append('condition')
    return current


@pytest.mark.parametrize('operator',['has_missing','empty'])
def test_full_columns_csv_executes_on_both_branch_outcomes(tmp_path,monkeypatch,operator):
    from tests.design.test_feedback_final_review import _run_csv
    from tests.design.test_feedback_service_scenarios import ROWS
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current=_branched_output(COLUMNS+['주문금액'])
    next(n for n in current['draft']['workflow']['nodes'] if n['id']=='check')['settings']['condition_operator']=operator
    fields,rows=_run_csv(tmp_path,current,current['draft'],COLUMNS,ROWS)
    assert fields==COLUMNS+['주문금액']
    assert [r['주문번호'] for r in rows]==['ORD-001','ORD-003']


@pytest.mark.parametrize('predicate,operator,expected', [('빈','is_empty',''),('비어 있지 않은','not_empty','user@example.com')])
def test_negated_removal_reaches_correct_csv_rows(tmp_path,predicate,operator,expected):
    from tests.design.test_builder import initial,draft
    from tests.design.test_feedback_final_review import _run_csv
    text=f'이메일이 {predicate} 행을 제외하지 말고 {predicate} 행만 남겨줘. CSV로 저장해줘.'
    current=initial(); columns=['이메일']
    current.update(messages=[dict(role='user',content=text)],resources=[dict(id='table-source',name='orders.csv',kind='table',columns=columns)])
    config=draft(); config.update(instructions=text,document_ids=['table-source'],requirements=[dict(description='선별',source_id='user',quote=text)])
    config['execution_plan'].update(missing_policy='keep',filters=[dict(field='이메일',operator=operator,value=None,compare_field=None,value_type='text')])
    nodes=[node('read','read_table',file_ids=['table-source']),node('filter','filter_sort',[bind('table','read','table')],execution_plan=config['execution_plan']),node('write','write_result',[bind('table','filter','table')],format='csv')]
    config['workflow']=dict(entry='read',nodes=nodes,edges=[edge('read','filter'),edge('filter','write')])
    _,rows=_run_csv(tmp_path,current,config,columns,[{'이메일':''},{'이메일':'user@example.com'}])
    assert rows==[{'이메일':expected}]


def test_output_projection_keeps_its_source_turn_and_accepts_explicit_revision():
    from agent_builder.design.requirement_ledger import extract_plan
    from agent_builder.design.output_selection_request import output_selection
    current=state(); raw=packet(); del raw['tasks'][4]
    current['messages'].append(dict(role='user',content='주문번호 오름차순으로 정렬해줘'))
    selected=next(t for t in extract_plan(raw,current)['tasks'] if t['spec']['kind']=='select_fields')
    assert selected['source_message']==1 and '주문번호·주문금액' in selected['quote']
    current['messages'].append(dict(role='user',content='주문번호·주문상태를 상태.csv로 저장해줘'))
    assert output_selection(current)==['주문번호','주문상태']


@pytest.mark.parametrize('review_branch',['yes','no'])
def test_conditional_review_tracks_the_truth_branch(review_branch):
    from tests.design.test_builder import draft
    current=request('누락된 조항이 있는 경우에만 사람 검토 후 저장해줘')
    nodes=[node('read','read_document'),node('analysis','extract_information',[bind('documents','read','documents')],fields=['조항']),
           node('check','condition',[bind('records','analysis','records')],condition_operator='has_missing'),
           node('review','ask_user',[bind('records','check','records')],question='검토 의견?'),node('end','finish')]
    workflow=dict(entry='read',nodes=nodes,edges=[edge('read','analysis'),edge('analysis','check'),
        edge('check','review',review_branch),edge('check','end','no' if review_branch=='yes' else 'yes'),edge('review','end')])
    assert bool(review_workflow_issues(current,{**draft(),'workflow':workflow}))==(review_branch!='yes')
