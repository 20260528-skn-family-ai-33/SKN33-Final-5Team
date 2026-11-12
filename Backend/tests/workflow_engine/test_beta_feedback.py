"""Run feedback must describe the saved plan and its actual output."""
import pytest
from agent_builder.conditions.input_requirements import table_run_request_notice
from workflow_engine.run_answer import empty_table_notice
from workflow_engine.workflow import workflow_errors
from service.worker import handle
from tests.workflow_engine.test_workflow import node, bind, edge, saved


def table_flow():
    return {'entry': 'read', 'nodes': [node('read', 'read_table'),
            node('save', 'write_result', [bind('table', 'read', 'table')], format='csv')],
            'edges': [edge('read', 'save')]}


def test_additional_criteria_apply_only_to_this_run_and_survive_reopen(tmp_path):
    store, identifier, _ = saved(tmp_path, table_flow())
    original = store.agent(identifier)
    store.close()
    job = dict(directory=str(tmp_path), id=identifier)
    uploaded = handle({**job, 'action': 'run_upload', 'asset': {
        'name': 'orders.csv', 'kind': 'table', 'format': 'csv', 'columns': ['상태']},
        'rows': [{'상태': '완료'}, {'상태': '취소'}]})['asset']
    run = handle({**job, 'action': 'test', 'content': '완료 주문만 추출해줘', 'file_ids': [uploaded['id']]})
    assert run['status'] == 'succeeded'
    assert run['rows'] == [{'상태': '완료'}]
    assert '이번 실행에 적용했습니다' in run['answer']
    reopened = handle({**job, 'action': 'run', 'id': run['id']})['run']
    assert reopened['answer'] == run['answer']
    from infrastructure.persistence.storage import Storage
    with_store = Storage(tmp_path)
    try:
        assert with_store.agent(identifier) == original
    finally:
        with_store.close()
    plain = handle({**job, 'action':'test', 'content':'', 'file_ids':[uploaded['id']]})
    assert len(plain['rows']) == 2


def test_run_additions_combine_threshold_status_and_sort_without_a_model(tmp_path):
    store, identifier, _ = saved(tmp_path, table_flow())
    store.close()
    job = dict(directory=str(tmp_path), id=identifier)
    uploaded = handle({**job, 'action':'run_upload', 'asset':dict(name='orders.csv', kind='table', format='csv',
        columns=['주문번호','상태','금액']), 'rows':[
            {'주문번호':'002','상태':'완료','금액':200000},
            {'주문번호':'001','상태':'완료','금액':100000},
            {'주문번호':'003','상태':'취소','금액':300000},
            {'주문번호':'004','상태':'완료','금액':1000} ]})['asset']
    run = handle({**job, 'action':'test', 'file_ids':[uploaded['id']],
        'content':'상태 = 완료; 금액 100,000 이상; 주문번호 오름차순'})
    assert run['status'] == 'succeeded', run.get('error')
    assert [r['주문번호'] for r in run['rows']] == ['001', '002']
    assert run['config_snapshot']['workflow']['nodes'][-1]['settings']['execution_plan']['filters'][1]['value'] == 100000


@pytest.mark.parametrize('content', ['매출이 높은 주문만', '금액 >= 100000 그리고 원본을 삭제해줘',
                                     '없는열 = 완료', '금액을 수량으로 나눠 계산해줘', '금액 >= 1,23'])
def test_unrecognized_runtime_changes_fail_before_running(tmp_path, content):
    store, identifier, _ = saved(tmp_path, table_flow())
    store.close()
    job = dict(directory=str(tmp_path), id=identifier)
    asset = handle({**job, 'action':'run_upload', 'asset':dict(name='a.csv',kind='table',format='csv',columns=['금액']),
                    'rows':[{'금액':10}]})['asset']
    with pytest.raises(ValueError, match='추가 실행 조건'):
        handle({**job, 'action':'test', 'content':content, 'file_ids':[asset['id']]})


def test_additional_conditions_survive_failed_step_retry_without_rereading(tmp_path):
    from workflow_engine.runner import run_workflow, execute_node
    from workflow_engine.runtime_request import apply_table_request
    store, identifier, _ = saved(tmp_path, table_flow())
    store.add_asset(dict(id='orders',name='orders.csv',kind='table',format='csv',columns=['상태']),
                    [{'상태':'완료'},{'상태':'취소'}])
    agent, applied = apply_table_request(store.agent(identifier), '완료 주문만', store, ['orders'])
    calls = []
    def executor(n, *args):
        calls.append(n['id'])
        if n['id'] == 'save' and calls.count('save') == 1:
            raise RuntimeError('temporary output failure')
        return execute_node(n, *args)
    try:
        failed = run_workflow(store, identifier, '완료 주문만', '', ['orders'], executor=executor,
                              agent_snapshot=agent, run_tags={'runtime_request_applied':applied})
        assert failed['status'] == 'failed'
        done = run_workflow(store, identifier, failed['request'], '', run_id=failed['id'], retry=True, executor=executor)
        assert done['status'] == 'succeeded'
        assert done['rows'] == [{'상태':'완료'}]
        assert calls == ['read','runtime_conditions','save','save']
        assert '이번 실행에 적용했습니다' in done['answer']
    finally:
        store.close()


@pytest.mark.parametrize('text', ['', ' ', '실행', '다시 실행해줘', 'run'])
def test_existing_launch_commands_still_work(text):
    assert table_run_request_notice({'workflow': table_flow()}, text) == ''


def test_ai_and_nested_workflow_request_contracts_are_preserved():
    flow = table_flow()
    flow['nodes'].append(node('answer', 'compose_answer'))
    assert table_run_request_notice({'workflow': flow}, '매출을 설명해줘') == ''
    parent = {'workflow': {'nodes': [node('child', 'saved_workflow')], 'entry': 'child', 'edges': []}}
    assert '설정 수정' in table_run_request_notice(parent, '금액 100원 이상만', {'child': {'config': {'workflow': table_flow()}}})


def test_zero_row_notice_uses_final_artifact_not_an_unrelated_empty_table():
    flow = table_flow()
    artifact = dict(content='id\n', format='csv')
    outputs = {'read': {'table': {'rows': [], 'columns': ['id']}}, 'save': {'artifact': artifact}}
    assert '0행' in empty_table_notice(flow, outputs)
    outputs['read']['table']['rows'] = [{'id': 1}]
    outputs['unrelated'] = {'table': {'rows': []}}
    assert not empty_table_notice(flow, outputs)
    flow['nodes'].append(node('finish', 'finish', [bind('artifact', 'save', 'artifact')]))
    flow['edges'].append(edge('save', 'finish'))
    outputs['finish'] = {'artifact': artifact}
    outputs['read']['table']['rows'] = []
    assert '0행' in empty_table_notice(flow, outputs)


def test_empty_table_success_includes_notice_and_preserves_download(tmp_path):
    flow = table_flow()
    store, identifier, _ = saved(tmp_path, flow)
    store.close()
    job = dict(directory=str(tmp_path), id=identifier)
    uploaded = handle({**job, 'action': 'run_upload', 'asset': {
        'name': 'empty.csv', 'kind': 'table', 'format': 'csv', 'columns': ['id']}, 'rows': []})['asset']
    run = handle({**job, 'action': 'test', 'file_ids': [uploaded['id']], 'content': ''})
    assert run['status'] == 'succeeded', run.get('error')
    assert '0행' in run['answer']
    assert run['artifact']


def test_calculation_error_names_missing_and_available_columns():
    flow = table_flow()
    calc = node('step_4', 'calculate_columns', [bind('table', 'read', 'table')],
                table_operation='compute', calculation_formula='price*qty', output_column='total')
    flow['nodes'] = [flow['nodes'][0], calc]
    flow['nodes'][0]['settings']['file_ids'] = ['table']
    flow['edges'] = [edge('read', 'step_4')]
    errors = workflow_errors(flow, ['read_table', 'calculate_columns'],
                             [{'id': 'table', 'kind': 'table', 'columns': ['qty']}])
    message = next(e for e in errors if '앞 단계에 없는 열' in e)
    assert 'price' in message and '단계: step_4' in message and '사용 가능한 열: qty' in message


def test_empty_record_export_is_reported_without_examining_document_contents():
    from tests.workflow_engine.test_workflow import flow
    workflow = flow()
    assert '0행' in empty_table_notice(workflow, {'read':{'documents':[]},'extract':{'records':[]},'out':{'artifact':{'content':'[]'}}})


def test_runtime_filter_cannot_silently_keep_unknown_status(tmp_path):
    store, identifier, _ = saved(tmp_path, table_flow())
    store.close()
    job = dict(directory=str(tmp_path), id=identifier)
    asset = handle({**job, 'action':'run_upload', 'asset':dict(name='orders.csv',kind='table',format='csv',columns=['상태']),
                    'rows':[{'상태':'완료'}, {'상태':None}]})['asset']
    result = handle({**job,'action':'test','content':'완료 주문만','file_ids':[asset['id']]})
    assert result['status'] == 'failed' and '빈 값' in result['error']


@pytest.mark.parametrize('memo_source', ['analysis', 'review'])
def test_contract_review_pauses_once_then_final_memo_receives_review(tmp_path, memo_source):
    from workflow_engine.runner import run_workflow, execute_node
    workflow = {'entry':'read', 'nodes':[
        node('read','read_document'),
        node('analysis','compose_answer',[bind('documents','read','documents')],instructions='중요 조항을 분석'),
        node('review','ask_user',[bind('text','analysis','text')],question='검토 의견을 입력하세요.'),
        node('memo','compose_answer',[bind('documents','read','documents'),bind('text',memo_source,'text'),bind('answer','review','answer')],instructions='최종 메모')],
        'edges':[edge('read','analysis'),edge('analysis','review'),edge('review','memo')]}
    store, identifier, asset = saved(tmp_path,workflow)
    called = []
    def execute(n, state, *args):
        called.append(n['id'])
        if n['id'] == 'analysis':
            return {'text':'5개 조항 분석 결과'}
        if n['id'] == 'memo':
            assert state['outputs']['review']['answer'] == '위약금 조항을 다시 확인하세요'
            assert state['outputs']['analysis']['text'] == '5개 조항 분석 결과'
            assert '위약금 조항을 다시 확인하세요' in state['request']
            return {'text':'최종 메모: 담당자 의견과 계약서 사실을 구분했습니다.'}
        return execute_node(n,state,*args)
    try:
        paused = run_workflow(store,identifier,'계약서를 분석해줘','',[asset['id']],executor=execute,llm=object())
        assert paused['status'] == 'needs_input' and 'memo' not in called, paused.get('error')
        assert paused['question']['preview'] == [{'text':'5개 조항 분석 결과'}]
        done = run_workflow(store,identifier,paused['request'],'',run_id=paused['id'],
                            answer='위약금 조항을 다시 확인하세요',executor=execute,llm=object())
        assert done['status'] == 'succeeded'
        assert called == ['read','analysis','review','review','memo']
        assert done['answer'] == '최종 메모: 담당자 의견과 계약서 사실을 구분했습니다.'
        assert done['outputs']['analysis']['text'] == '5개 조항 분석 결과'
    finally:
        store.close()


@pytest.mark.parametrize('operation,port', [('write_csv','rows'),('serialize','value')])
@pytest.mark.parametrize('actual', [[], [{'id':1}]])
def test_empty_csv_notice_uses_literal_argument_not_unrelated_empty_binding(operation, port, actual):
    import json
    writer = node('out','export_result',[bind(port,'source','records')],operation=operation,
                  arguments_json=json.dumps({port:actual,'format':'csv','path':'result.csv'}))
    workflow = dict(entry='out',nodes=[writer],edges=[])
    outputs = {'source':{'records':[]},'out':{'artifact':{'content':'csv'}}}
    assert bool(empty_table_notice(workflow,outputs)) == (actual == [])


def test_memo_model_and_grounding_check_both_receive_bound_analysis_and_opinion(monkeypatch):
    import json
    from types import SimpleNamespace
    from workflow_engine.executors.document.compose_answer import execute
    context = dict(text='중요 조항 5개 분석',answer='위약금 조항 재확인')
    checked = []
    def compose(payload, *args):
        message = payload['messages'][0]['content']
        assert context['text'] in message and context['answer'] in message
        checked.append('compose')
        return {'structured_response':dict(text='최종 검토 메모',citations=[])}
    monkeypatch.setattr('workflow_engine.executors.agent_factory.create_deep_agent',
                        lambda **kwargs:SimpleNamespace(invoke=compose))
    class Reviewer:
        def with_structured_output(self, *args, **kwargs):return self
        def invoke(self, messages):
            payload = json.loads(messages[-1]['content'])
            assert payload['bound_context'] == context
            checked.append('verify')
            return payload['draft']
    memo = node('memo','compose_answer',[bind('documents','read','documents'),bind('text','analysis','text'),
                                       bind('answer','review','answer')],instructions='최종 메모')
    state = dict(request='계약 검토',messages=[],outputs={
        'read':{'documents':[dict(source_id='doc:1',file_name='계약서',page=1,text='계약 조항 원문')]},
        'analysis':{'text':context['text']},'review':{'answer':context['answer']}})
    result = execute(memo,state,None,{'instructions':'문서 사실과 검토 의견을 구분하세요.'},Reviewer())
    assert result['text'] == '최종 검토 메모' and checked == ['compose','verify']
