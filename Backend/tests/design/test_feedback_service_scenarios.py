"""Original beta scenarios across extraction, checkpoints, save and real CSV tools.

Model responses are controlled; database, graph validation and file execution are real.
"""
import base64
import csv
import io
import json
from copy import deepcopy

import pytest

from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_builder import draft
from tests.workflow_engine.test_workflow import node, bind, edge


ORDER_REQUEST = '주문내역.csv에서 매출이 높은 주문만 정리해서 저장해줘.'
ORDER_ANSWER = '완료 주문만 대상으로 매출은 수량×단가, 주문금액 100000 이상, 주문번호 오름차순, 주문번호·주문금액을 고액주문.csv로 저장해줘.'
COLUMNS = ['주문번호','주문상태','수량','단가']
ROWS = [dict(zip(COLUMNS,row)) for row in [
    ['ORD-003','완료',3,50000],['ORD-001','완료',2,50000],
    ['ORD-002','취소',10,50000],['ORD-004','완료',1,50000]]]
RULES = [dict(field='주문상태',operator='eq',value='완료',compare_field=None,value_type='text'),
         dict(field='주문금액',operator='gte',value=100000,compare_field=None,value_type='number')]
SORT = [dict(field='주문번호',direction='ascending',value_type='text')]


def order_draft(identifier):
    result = draft()
    plan = {**result['execution_plan'],'filters':RULES,'sort':SORT,'missing_policy':'error'}
    nodes = [node('read','read_table',file_ids=[identifier]),
        node('compute','calculate_columns',[bind('table','read','table')],table_operation='compute',
             calculation_formula='수량*단가',output_column='주문금액'),
        node('filter','filter_sort',[bind('table','compute','table')],execution_plan=plan),
        node('rows','json_transform',[bind('value','filter','table')],operation='lookup',
             arguments_json=json.dumps(dict(path=['rows'])),output_port='records'),
        node('columns','filter_sort',[bind('rows','rows','records')],operation='select_columns',
             arguments_json=json.dumps(dict(columns=['주문번호','주문금액']))),
        node('out','export_result',[bind('rows','columns','records')],operation='write_csv',
             arguments_json=json.dumps(dict(columns=['주문번호','주문금액'],path='고액주문.csv')))]
    result.update(name='피드백 주문 검증',purpose=ORDER_REQUEST,instructions=ORDER_REQUEST+'\n'+ORDER_ANSWER,
        execution_plan=plan,document_ids=[identifier],tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),
        requirements=[dict(description='주문 처리',source_id='user',quote=ORDER_REQUEST),
                      dict(description='확정 조건',source_id='user',quote=ORDER_ANSWER)],
        workflow=dict(entry='read',nodes=nodes,edges=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]))
    return result


def order_specs(identifier):
    return [dict(kind='read',file_ids=[identifier]),dict(kind='compute',formula='수량*단가',output_column='주문금액'),
        dict(kind='filter',filters=RULES,match='all'),dict(kind='sort',sort=SORT),
        dict(kind='select_fields',fields=['주문번호','주문금액']),dict(kind='export',format='csv')]


@pytest.mark.parametrize('no_matches',[False,True])
@pytest.mark.parametrize('miss_compute',[False,True])
def test_order_clarification_reopen_save_and_csv_result(tmp_path, monkeypatch, no_matches, miss_compute):
    import service.handlers.design as worker
    real = worker.build_graph
    extracted = []
    identifier = None
    class Capabilities:
        def invoke(self,messages):
            return {'content':json.dumps(dict(decision='existing_tools',reason='표 계산 및 CSV 저장',
                tool_ids=['read_table','filter_sort','write_result'] if miss_compute and extracted else ['read_table','calculate_columns','filter_sort','json_transform','export_result'],
                missing_conditions=[],missing_actions=[],user_quotes=[ORDER_REQUEST]))}
    class Requirements:
        def invoke(self,messages):
            payload = json.loads(messages[-1]['content'])
            extracted.append(payload)
            if len(payload['user_messages']) == 1:
                packet = dict(tasks=[dict(source_message=0,description='결과 저장',spec=dict(kind='export',format='csv'))],
                    unresolved=[dict(key='filter.value',text='매출 계산식과 높은 주문의 기준을 알려주세요.')])
            else:
                packet = dict(tasks=[dict(source_message=1,description=s['kind'],spec=s) for s in order_specs(identifier)],unresolved=[])
            return {'content':json.dumps(packet,ensure_ascii=False)}
    def build(*args,**kwargs):
        return real(*args,**{**kwargs,'capability_mode':'enforce','capability_transport':Capabilities(),
            'requirement_mode':'enforce','requirement_transport':Requirements(),'condition_mode':'enforce',
            'condition_transport':UnexpectedConditionCall(),'verifier_mode':'off'})
    monkeypatch.setattr(worker,'build_graph',build)
    base = dict(directory=str(tmp_path))
    session = handle({**base,'action':'new_session','model':'fixture','credential_id':'fixture'})
    result = handle({**base,'action':'upload','id':session['id'],'revision':0,
        'asset':dict(name='주문내역.csv',kind='table',format='csv',columns=COLUMNS),
        'rows':[] if no_matches else ROWS})
    identifier = result['resources'][0]['id']
    def action(kind, **extra):
        return handle({**base,'action':kind,'id':session['id'],'revision':result['revision'],**extra},
                      conversation=lambda state:dict(draft=order_draft(identifier)))
    result = action('message',content=ORDER_REQUEST)
    assert result['status'] == 'needs_clarification', result.get('issues')
    question = result['pending_questions'][0]['id']
    result = action('message',content=ORDER_ANSWER,question_id=question)
    assert result['status'] == 'ready', result.get('issues')
    before = deepcopy(result)
    result = action('session')
    assert result['status'] == 'ready' and result['draft'] == before['draft']
    assert result['capability_enforced'] and result['messages'] == before['messages']
    result = action('confirm')
    assert result['status'] == 'saved', result.get('issues')
    run = handle({**base,'action':'test','id':result['saved_agent_id'],'content':''})
    assert run['status'] == 'succeeded', run.get('error')
    artifact = run['artifact']
    data = base64.b64decode(artifact['content']).decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(data))
    assert reader.fieldnames == ['주문번호','주문금액']
    assert list(reader) == ([] if no_matches else [
        {'주문번호':'ORD-001','주문금액':'100000'}, {'주문번호':'ORD-003','주문금액':'150000'}])
    if no_matches:
        assert '0행' in run['answer']
    assert extracted[-1]['user_messages'] == [ORDER_REQUEST,ORDER_ANSWER]


class UnexpectedConditionCall:
    def invoke(self, messages):
        pytest.fail('Extended computation must stay with the full requirement planner.')


def test_condition_contract_failure_can_retry_without_changing_source_request(tmp_path, monkeypatch):
    from tests.conditions.test_condition_readiness import state, proposal, workflow_draft, Fake
    import service.handlers.design as worker
    original = state()
    text = original['messages'][0]['content']
    transport = Fake([{'bad':'private-model-body'}, {'bad':'private-model-body'}, proposal(original)])
    real = worker.build_graph
    monkeypatch.setattr(worker, 'build_graph', lambda *args, **kwargs: real(*args, **{
        **kwargs, 'capability_mode':'off', 'condition_mode':'enforce', 'condition_transport':transport,
        'requirement_mode':'off', 'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    session = handle({**base,'action':'new_session','model':'fixture','credential_id':'fixture'})
    result = handle({**base,'action':'upload','id':session['id'],'revision':0,
        'asset':dict(name='비용.csv',kind='table',format='csv',columns=['비용']), 'rows':[{'비용':2000000}]})
    identifier = result['resources'][0]['id']
    candidate = workflow_draft(original)
    candidate['document_ids'] = [identifier]
    candidate['workflow']['nodes'][0]['settings']['file_ids'] = [identifier]
    def send(content):
        return handle({**base,'action':'message','id':session['id'],'revision':result['revision'], 'content':content},
            conversation=lambda _:dict(draft=deepcopy(candidate)))
    result = send(text)
    assert result['status'] == 'validation_failed'
    assert result['condition_ledger']['error_type'] == 'ConditionContractError'
    assert '같은 조건으로 다시 시도' in result['messages'][-1]['content']
    assert 'private-model-body' not in str(result)
    result = send('다시 시도')
    assert result['status'] == 'ready', result.get('issues')
    assert [m['content'] for m in result['messages'] if m['role']=='user'] == [text]
    assert transport.calls == 3


CONTRACT_REQUEST = (
    '피드백검증-S7-20261112. 연결한 feedback-contract.txt 한 개로 계약 검토 워크플로우 초안만 만들어줘. '
    '계약 분석(LLM) → 사람 검토(Human Review) → 최종 메모(LLM) 순서다. '
    '계약 분석은 중요 조항 5개를 번호/핵심 내용/위험/확인 사항 열의 Markdown 표로 작성해줘. '
    '사람 검토 단계에서는 필수 텍스트 입력인 검토 의견을 받고 정확히 한 번 멈춰야 해. '
    '최종 메모는 앞의 분석 결과와 제출한 검토 의견을 함께 사용하고, 계약서 사실과 담당자 의견을 구분해서 '
    '요약/주요 발견/검토 의견/후속 조치 제목으로 작성해줘. 외부 전송, 웹 검색, 분기, 반복은 넣지 마.')


def test_live_contract_prompt_reaches_full_planner_and_saves_review_flow(tmp_path, monkeypatch):
    from tests.design.test_beta_feedback import review_draft
    from agent_builder.design.requirement_generation import generation_requirements
    import service.handlers.design as worker
    real = worker.build_graph
    class Capabilities:
        def invoke(self, messages):
            return {'content':json.dumps(dict(decision='existing_tools',reason='계약서 분석과 사람 검토',
                tool_ids=['read_document','compose_answer','ask_user'],missing_conditions=[],missing_actions=[],
                user_quotes=[CONTRACT_REQUEST]))}
    class Requirements:
        def invoke(self, messages):
            return {'content':json.dumps(dict(tasks=[
                dict(source_message=0,description='계약 분석',spec=dict(kind='document_analysis')),
                dict(source_message=0,description='사람 검토',spec=dict(kind='human_review'))],unresolved=[]))}
    monkeypatch.setattr(worker,'build_graph',lambda *args,**kwargs:real(*args,**{
        **kwargs,'capability_mode':'enforce','capability_transport':Capabilities(),
        'condition_mode':'enforce','condition_transport':UnexpectedConditionCall(),
        'requirement_mode':'enforce','requirement_transport':Requirements(),'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    session = handle({**base,'action':'new_session','model':'fixture','credential_id':'fixture'})
    result = handle({**base,'action':'upload','id':session['id'],'revision':0,
        'asset':dict(name='feedback-contract.txt',kind='document',text='합성 계약서 제1조 목적: 교육용 소프트웨어 제공.',columns=[]),
        'rows':[]})
    identifier = result['resources'][0]['id']
    candidate = review_draft()
    candidate.update(tool_ids=['read_document','compose_answer','ask_user'],document_ids=[identifier],
        instructions=CONTRACT_REQUEST,requirements=[dict(description='계약 검토',source_id='user',quote=CONTRACT_REQUEST)])
    candidate['workflow']['nodes'][0]['settings']['file_ids'] = [identifier]
    candidate['workflow']['nodes'][-1]['bindings'].append(bind('documents','read','documents'))
    for n in candidate['workflow']['nodes']:
        if n['tool']=='compose_answer':
            n['settings']['instructions'] = CONTRACT_REQUEST
    called = []
    def planner(state):
        assert not called, (state.get('issues'), state.get('semantic_issues'), state.get('requirement_coverage'))
        called.append(True)
        assert generation_requirements(state)[1] == ['read_document','compose_answer','ask_user','compose_answer']
        return dict(draft=deepcopy(candidate))
    result = handle({**base,'action':'message','id':session['id'],'revision':result['revision'],
        'content':CONTRACT_REQUEST},conversation=planner)
    assert called and result['status']=='ready', result.get('issues')
    assert not result['draft']['questions']
    result = handle({**base,'action':'confirm','id':session['id'],'revision':result['revision']},conversation=planner)
    assert result['status']=='saved',result.get('issues')
