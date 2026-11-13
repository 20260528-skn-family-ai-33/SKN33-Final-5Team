"""Remaining PR 103 review cases and the production S5 routing failure."""
import pytest
from copy import deepcopy
from agent_builder.design.request_guards import review_request, review_workflow_issues, unary_filter_issue
from agent_builder.design.question_help import question_help
from tests.design.test_beta_feedback import request, review_draft
from tests.design.test_builder import draft
from tests.workflow_engine.test_workflow import node, bind, edge


@pytest.mark.parametrize('predicate,verb,expected', [
    ('빈 값인', '남겨줘', 'is_empty'), ('빈 값인', '제외해줘', 'not_empty'),
    ('비어 있지 않은', '남겨줘', 'not_empty'), ('비어 있지 않은', '제외해줘', 'is_empty'),
    ('값이 있는', '삭제해줘', 'is_empty'), ('빈 값이 아닌', '제거해줘', 'is_empty'),
])
def test_empty_selection_truth_table(predicate, verb, expected):
    text = f'이메일이 {predicate} 행은 {verb}'
    for op in ('is_empty','not_empty'):
        assert (unary_filter_issue(dict(field='이메일',operator=op),text) is None) == (op==expected)


@pytest.mark.parametrize('text', ['사람 검토는 필요 없고 결과만 저장해줘',
    '사람이 직접 확인한 주문만 CSV로 저장해줘', '사람이 검토한 계약만 정리해줘'])
def test_review_is_not_required_for_negation_or_already_reviewed_rows(text):
    assert review_request(request(text)) == (False,False)


def test_keyless_question_help_preserves_waiting_state():
    state=request('주문을 선별해줘',draft=draft(['선별할 기준 값은 얼마인가요?']),pending_questions=[])
    before=deepcopy(state)
    assert '기준값은' in (question_help(state,'기준값이 뭐야?') or '')
    assert state==before


def test_keyless_help_resumes_the_graph_without_answering_the_question(tmp_path):
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from langgraph.types import Command
    calls=[]
    def planner(s):
        calls.append(s)
        return {'draft':draft(['선별할 기준 값은 얼마인가요?'])}
    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=planner,capability_mode='off',condition_mode='off',requirement_mode='off',verifier_mode='off')
    state=request('주문을 선별해줘');cfg={'configurable':{'thread_id':state['session_id']}}
    try:
        first=graph.invoke(state,cfg)
        helped=graph.invoke(Command(resume=dict(type='message',content='기준값이 뭐야?')),cfg)
        assert len(calls)==1
        assert helped['status']=='needs_clarification' and helped['draft']==first['draft']
        assert not helped.get('condition_answers') and not helped.get('requirement_answers')
        assert '기준값은' in helped['messages'][-1]['content']
        assert helped['__interrupt__']
    finally:connection.close();store.close()


def test_ambiguous_help_never_becomes_a_condition_answer():
    state=request('처리해줘',draft=draft(['어떤 파일인가요?','언제 실행할까요?']),pending_questions=[])
    assert '어떤 질문' in (question_help(state,'기준값이 뭐야?') or '')


def test_reviewed_records_must_reach_the_final_memo():
    state=request('사람 검토 후 최종 메모를 작성해줘')
    d=review_draft();nodes=d['workflow']['nodes']
    nodes[1]=node('analysis','extract_information',[bind('documents','read','documents')],fields=['조항'])
    nodes[2]['bindings']=[bind('records','analysis','records')]
    nodes[3]['bindings']=[bind('documents','read','documents'),bind('answer','review','answer')]
    assert review_workflow_issues(state,d)
    for source in ('analysis','review'):
        nodes[3]['bindings'].append(bind('records',source,'records'))
        assert review_workflow_issues(state,d)==[]
        nodes[3]['bindings'].pop()


@pytest.mark.parametrize('review_on', ['none','yes','both'])
def test_unconditional_review_covers_every_branch(review_on):
    state=request('결과는 사람 검토 후 저장해줘')
    nodes=[node('read','read_document'),node('analysis','extract_information',[bind('documents','read','documents')],fields=['조항']),
           node('check','condition',[bind('records','analysis','records')],condition_operator='has_missing')]
    edges=[edge('read','analysis'),edge('analysis','check')]
    for branch in ('yes','no'):
        end=branch+'_end';nodes.append(node(end,'finish'))
        if review_on=='both' or review_on==branch:
            rid=branch+'_review';nodes.append(node(rid,'ask_user',[bind('records','check','records')],question='검토 의견?'))
            edges += [edge('check',rid,branch),edge(rid,end)]
        else:edges.append(edge('check',end,branch))
    d={**draft(),'workflow':dict(entry='read',nodes=nodes,edges=edges)}
    assert bool(review_workflow_issues(state,d)) == (review_on!='both')
    state['messages'][0]['content']='누락된 조항이 있는 경우에만 사람 검토 후 저장해줘'
    # "Only when missing" requires the yes branch and forbids a review on no.
    assert bool(review_workflow_issues(state,d)) == (review_on!='yes')


def test_s5_calculation_bypasses_single_filter_checker_even_when_capability_misses_compute():
    from agent_builder.conditions.condition_check import make_condition_check
    from tests.design.test_feedback_service_scenarios import ORDER_REQUEST,ORDER_ANSWER,COLUMNS
    state=request(ORDER_REQUEST,resources=[dict(id='orders',kind='table',columns=COLUMNS)],
        capability_assessment=dict(decision='existing_tools',tool_ids=['read_table','filter_sort','write_result']),
        condition_ledger=dict(decision='clarify',operations=['filter'],slots={}),condition_enforced=True)
    state['messages'].append(dict(role='user',content=ORDER_ANSWER))
    class Transport:
        def invoke(self,messages):pytest.fail('Calculation must not enter the single-filter contract.')
    update=make_condition_check(mode='enforce',transport=Transport())(state)
    assert update['condition_route']=='conversation'
    assert update['condition_ledger'] is None and not update['condition_enforced']


def test_missing_optional_duplicate_field_does_not_invent_a_question():
    from tests.conditions.test_condition_readiness import state,proposal,checked
    s=state('비용이 100만원 초과인 행을 JSON으로 저장해줘.')
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000',
                    'output.format':'json','duplicate.field':None,'duplicate.policy':None})
    result=checked(s,raw)
    assert result['decision']=='ready'
    assert not result['questions']


def test_condition_error_keeps_codes_without_provider_body():
    from agent_builder.conditions.condition_check import make_condition_check
    from tests.conditions.test_condition_readiness import state,proposal,Fake
    s=state();raw=proposal(s);raw['conditions'].append(deepcopy(raw['conditions'][0]))
    result=make_condition_check(mode='enforce',transport=Fake([raw,raw]))(s)
    assert result['condition_ledger']['diagnostics']==[
        dict(attempt=1,code='duplicate_condition_key'),dict(attempt=2,code='duplicate_condition_key')]
    assert 'content' not in str(result['condition_ledger']['diagnostics'])


@pytest.mark.parametrize('text,computed', [
    ('주문금액은 수량×단가, 주문금액 100000 이상',True),
    ('수량 * 단가를 계산해줘',True), ('수량을 단가로 나누어 정리해줘',True),
    ('단가 * 1.1을 새 금액으로 저장',True),
    ('2026-11-12 이후 주문만 남겨줘',False), ('100-200 사이 주문을 남겨줘',False),
    ('계산하지 말고 완료 주문만 남겨줘',False),
])
def test_compute_scope_does_not_confuse_dates_ranges_or_cancellation(text,computed):
    from agent_builder.conditions.condition_check import computed_table_request
    s=request(text,resources=[dict(id='orders',kind='table',columns=['수량','단가'])])
    assert computed_table_request(s) is computed
