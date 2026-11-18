"""Output intent exercised through the local service, with fixed model responses."""
from copy import deepcopy
import base64
import csv
import io
import json
from pathlib import Path

import pytest

from agent_builder.design.requirement_ledger import make_requirement_check, validate_plan
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_builder import draft, initial
from tests.design.test_feedback_service_scenarios import (
    COLUMNS, ORDER_ANSWER, ORDER_REQUEST, ROWS, UnexpectedConditionCall, order_draft, order_specs,
)
from tests.workflow_engine.test_workflow import bind, edge, node
from workflow_engine.runner import run_workflow


@pytest.fixture
def table_session(tmp_path, monkeypatch):
    import service.handlers.design as design

    build = design.build_graph
    directory = str(tmp_path)
    packet = None
    require_reuse = False
    enforce_conditions = False

    class Capabilities:
        def invoke(self, messages):
            return {'content': json.dumps(dict(decision='existing_tools', reason='표 선별 및 파일 저장',
                tool_ids=['read_table', 'calculate_columns', 'filter_sort', 'json_transform', 'export_result'],
                missing_conditions=[], missing_actions=[], user_quotes=[ORDER_REQUEST]), ensure_ascii=False)}

    class Requirements:
        def invoke(self, messages):
            if require_reuse:
                pytest.fail('형식 변경만 요청한 턴에서 전체 요구사항 추출을 다시 호출했습니다.')
            return {'content': json.dumps(packet, ensure_ascii=False)}

    monkeypatch.setattr(design, 'build_graph', lambda *args, **kwargs: build(*args, **{
        **kwargs, 'capability_mode': 'enforce' if enforce_conditions else 'off',
        'capability_transport': Capabilities(),
        'condition_mode': 'enforce' if enforce_conditions else 'off',
        'condition_transport': UnexpectedConditionCall(), 'verifier_mode': 'off',
        'requirement_mode': 'enforce', 'requirement_transport': Requirements(),
    }))

    def start(name, columns, rows, *, enforce=False):
        nonlocal enforce_conditions
        enforce_conditions = enforce
        design_states = []
        result = handle(dict(directory=directory, action='new_session', model='fixture', credential_id='fixture'))
        session_id = result['id']
        result = handle(dict(directory=directory, action='upload', id=session_id, revision=0,
                             asset=dict(name=name, kind='table', format='csv', columns=columns), rows=rows))
        identifier = result['resources'][0]['id']

        def send(content, specs, candidate, *, source=0, output_source=None, unresolved=(), reuse=False, **answers):
            nonlocal packet, require_reuse, result
            require_reuse = reuse
            tasks = []
            for spec in specs:
                task_source = output_source if output_source is not None and spec['kind'] == 'export' else source
                tasks.append(dict(source_message=task_source, description=spec['kind'], spec=spec))
            packet = dict(tasks=tasks, unresolved=list(unresolved))

            def plan(state):
                design_states.append(deepcopy(state))
                return dict(draft=deepcopy(candidate), repair_count=state.get('repair_count', 0) + 1)

            result = handle(dict(directory=directory, action='message', id=session_id,
                                 revision=result['revision'], content=content, **answers), conversation=plan)
            return result

        def save_and_run():
            saved = handle(dict(directory=directory, action='confirm', id=session_id, revision=result['revision']))
            assert saved['status'] == 'saved', saved.get('issues')
            run = handle(dict(directory=directory, action='test', id=saved['saved_agent_id'], content=''))
            assert run['status'] == 'succeeded', run.get('error')
            return run

        return identifier, send, save_and_run, design_states

    return start


def artifact_text(artifact):
    if artifact.get('encoding') == 'base64':
        return base64.b64decode(artifact['content']).decode('utf-8-sig')
    return artifact['content'].lstrip('\ufeff')


@pytest.mark.parametrize('replacement', [
    '같은 열을 JSON으로 저장해줘.',
    'CSV 말고 JSON으로 저장해줘.',
    '저장 형식을 JSON으로 변경해줘.',
    'JSON으로 바꿔줘.',
])
def test_format_replacement_and_acknowledgement_keep_the_original_rows(table_session, replacement):
    identifier, send, save_and_run, design_states = table_session('주문내역.csv', COLUMNS, ROWS)
    csv_specs = order_specs(identifier)
    original = order_draft(identifier)
    pending = send(ORDER_REQUEST, csv_specs[-1:], original, unresolved=[
        dict(key='filter.value', text='계산식과 높은 주문의 기준을 알려주세요.'),
    ])
    assert pending['status'] == 'needs_clarification', pending.get('issues')
    ready = send(ORDER_ANSWER, csv_specs, original, source=1, question_id=pending['pending_questions'][0]['id'])
    assert ready['status'] == 'ready', ready.get('issues')
    original_tasks = design_states[-1]['requirement_ledger']['tasks']

    replacement_draft = deepcopy(original)
    writer = replacement_draft['workflow']['nodes'][-1]
    writer['settings'].update(operation='write_json', arguments_json=json.dumps(dict(path='고액주문.json')))
    writer['bindings'][0]['input'] = 'value'
    updated_specs = [*csv_specs[:-1], dict(kind='export', format='json')]
    reuse = replacement == 'JSON으로 바꿔줘.'
    ready = send(replacement, updated_specs, replacement_draft, source=1, output_source=2, reuse=reuse)
    assert ready['status'] == 'ready', ready.get('issues')
    if reuse:
        assert design_states[-1]['requirement_ledger']['tasks'][:-1] == original_tasks[:-1]
    ready = send('네.', updated_specs, replacement_draft, source=1, output_source=2)
    assert ready['status'] == 'ready', ready.get('issues')
    export = [t for t in design_states[-1]['requirement_ledger']['tasks'] if t['spec']['kind'] == 'export']
    assert [t['spec']['format'] for t in export] == ['json']
    assert export[0]['quote'] == replacement

    run = save_and_run()
    assert run['artifact']['name'] == '고액주문.json'
    assert json.loads(artifact_text(run['artifact'])) == [
        {'주문번호': 'ORD-001', '주문금액': 100000},
        {'주문번호': 'ORD-003', '주문금액': 150000},
    ]


@pytest.mark.parametrize('selection', [
    '주문금액이 150000 이상인 행만 남기고 이유도 설명해줘.',
    '주문금액이 150000 이상인 주문만 필터링해줘.',
    '주문금액이 150000 이상인 행만 남기고 왜 그런지도 설명해줘.',
    '주문금액이 150000 이상인 행만 남기되 이유도 설명해줘.',
])
def test_format_change_with_filter_revision_saves_only_the_new_selection(table_session, selection):
    identifier, send, save_and_run, states = table_session('주문내역.csv', COLUMNS, ROWS, enforce=True)
    csv_specs = deepcopy(order_specs(identifier))
    original = order_draft(identifier)
    pending = send(ORDER_REQUEST, csv_specs[-1:], original, unresolved=[
        dict(key='filter.value', text='계산식과 높은 주문의 기준을 알려주세요.'),
    ])
    assert pending['status'] == 'needs_clarification', pending.get('issues')
    ready = send(ORDER_ANSWER, csv_specs, original, source=1, question_id=pending['pending_questions'][0]['id'])
    assert ready['status'] == 'ready', ready.get('issues')

    updated_specs = deepcopy(csv_specs)
    updated_specs[2]['filters'][1]['value'] = 150000
    updated_specs[-1]['format'] = 'json'
    candidate = deepcopy(original)
    writer = candidate['workflow']['nodes'][-1]
    writer['settings'].update(operation='write_json', arguments_json=json.dumps(dict(path='고액주문.json')))
    writer['bindings'][0]['input'] = 'value'
    candidate['execution_plan']['filters'][1]['value'] = 150000
    candidate['workflow']['nodes'][2]['settings']['execution_plan']['filters'][1]['value'] = 150000
    revision = 'JSON으로 바꿔줘. ' + selection
    ready = send(revision, updated_specs, candidate, source=1, output_source=2)
    assert ready['status'] == 'ready', ready.get('issues')

    current = states[-1]
    active = current['requirement_ledger']
    assert active['tasks'][2]['spec']['filters'][1]['value'] == 150000
    assert active['tasks'][2]['spec']['filters'][0] == csv_specs[2]['filters'][0]
    for index in (0, 1, 3, 4):
        assert active['tasks'][index]['spec'] == csv_specs[index]
    stale = deepcopy(active)
    stale['tasks'][2]['spec'] = csv_specs[2]
    with pytest.raises(ValueError, match='숫자 선별 조건'):
        validate_plan(stale, current)
    wrong_graph = deepcopy(candidate)
    wrong_graph['workflow']['nodes'][2]['settings']['execution_plan'] = deepcopy(
        original['workflow']['nodes'][2]['settings']['execution_plan'])
    assert validate_node({**current, 'draft': wrong_graph})['issues']

    run = save_and_run()
    assert run['artifact']['name'] == '고액주문.json'
    assert json.loads(artifact_text(run['artifact'])) == [{'주문번호': 'ORD-003', '주문금액': 150000}]


SIMPLE_ROWS = [dict(amount=75, note='below'), dict(amount=100, note='boundary'), dict(amount=180, note='above')]
AMOUNT_FILTER = dict(field='amount', operator='gte', value=100, compare_field=None, value_type='number')


def simple_plan(identifier, source_request, output_format, *, restrict_rows=False):
    candidate = draft()
    specs = [dict(kind='read', file_ids=[identifier])]
    nodes = [node('read', 'read_table', file_ids=[identifier])]
    parent = bind('table', 'read', 'table')
    if restrict_rows:
        specs.extend([dict(kind='filter', filters=[AMOUNT_FILTER], match='all'),
                      dict(kind='select_fields', fields=['amount'])])
        nodes.extend([
            node('filter', 'filter_sort', [parent], execution_plan={
                **candidate['execution_plan'], 'filters': [AMOUNT_FILTER], 'missing_policy': 'error',
            }),
            node('rows', 'json_transform', [bind('value', 'filter', 'table')], operation='lookup',
                 arguments_json=json.dumps(dict(path=['rows'])), output_port='records'),
            node('columns', 'filter_sort', [bind('rows', 'rows', 'records')], operation='select_columns',
                 arguments_json=json.dumps(dict(columns=['amount']))),
        ])
        parent = bind('rows', 'columns', 'records')
    if output_format:
        specs.append(dict(kind='export', format=output_format))
        if restrict_rows:
            nodes.append(node('out', 'export_result', [parent], operation='write_csv',
                              arguments_json=json.dumps(dict(path='결과.csv', columns=['amount']))))
        else:
            nodes.append(node('out', 'write_result', [parent], format=output_format))
    candidate.update(name='표 결과 정리', purpose=source_request, instructions=source_request, document_ids=[identifier],
                     requirements=[dict(description='표 조회 및 결과 제공', source_id='user', quote=source_request)],
                     tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),
                     workflow=dict(entry='read', nodes=nodes, edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])]))
    return specs, candidate


@pytest.mark.parametrize('reply', [
    'CSV 저장은 취소해줘. 출력은 amount 열만 표시해줘.',
    '파일 저장만 취소해줘. 결과는 amount 열만 보여줘.',
])
def test_cancel_only_file_and_keep_the_filtered_column_preview(table_session, reply):
    identifier, send, save_and_run, design_states = table_session('input.csv', ['amount', 'note'], SIMPLE_ROWS)
    request = '첨부 표에서 amount가 100 이상인 행을 선별해줘. 출력은 amount 열만 표시해줘. CSV로 저장해줘.'
    specs, candidate = simple_plan(identifier, request, 'csv', restrict_rows=True)
    ready = send(request, specs, candidate)
    assert ready['status'] == 'ready', ready.get('issues')

    specs, candidate = simple_plan(identifier, request, None, restrict_rows=True)
    for text in (reply, '네.'):
        ready = send(text, specs, candidate)
        assert ready['status'] == 'ready', ready.get('issues')
    current = design_states[-1]
    plan = current['requirement_ledger']
    assert [task['spec']['kind'] for task in plan['tasks']] == ['read', 'filter', 'select_fields']
    missing_projection = deepcopy(plan)
    missing_projection['tasks'].pop()
    with pytest.raises(ValueError, match='출력 열'):
        validate_plan(missing_projection, current)

    unprojected_preview = deepcopy(candidate)
    workflow = unprojected_preview['workflow']
    workflow['nodes'].append(node('raw_preview', 'read_table', file_ids=[identifier]))
    workflow['edges'].append(edge('columns', 'raw_preview'))
    rejected = validate_node({**current, 'draft': unprojected_preview})
    assert any('출력 열' in issue for issue in rejected['issues'])

    run = save_and_run()
    assert run['artifact'] is None
    assert run['rows'] == [dict(amount=100), dict(amount=180)]


@pytest.mark.parametrize('display, fields', [
    ('주문금액 열만', ['주문금액']),
    ('주문금액만', ['주문금액']),
    ('수량과 주문금액 열만', ['수량', '주문금액']),
])
def test_cancelled_export_keeps_only_the_requested_calculated_columns(table_session, display, fields):
    rows = [dict(수량=2, 단가=5, 숨길열='one'), dict(수량=3, 단가=7, 숨길열='two')]
    identifier, send, save_and_run, states = table_session('입력.csv', list(rows[0]), rows)
    request = '첨부 표의 수량*단가를 계산한 뒤 CSV로 저장해줘.'
    specs, candidate = simple_plan(identifier, request, 'csv')
    compute = dict(kind='compute', formula='수량*단가', output_column='주문금액')
    read, writer = candidate['workflow']['nodes']
    calculation = node('compute', 'calculate_columns', [bind('table', 'read', 'table')],
        table_operation='compute', calculation_formula='수량*단가', output_column='주문금액')
    writer['bindings'] = [bind('table', 'compute', 'table')]
    candidate['tool_ids'].insert(1, 'calculate_columns')
    candidate['workflow']['nodes'] = [read, calculation, writer]
    candidate['workflow']['edges'] = [edge('read', 'compute'), edge('compute', 'out')]
    ready = send(request, [specs[0], compute, specs[-1]], candidate)
    assert ready['status'] == 'ready', ready.get('issues')

    projected = deepcopy(candidate)
    unprojected = deepcopy(candidate)
    unprojected['workflow']['nodes'].pop()
    unprojected['workflow']['edges'].pop()
    unprojected['tool_ids'].remove('write_result')
    projected['tool_ids'] = [*unprojected['tool_ids'], 'json_transform', 'filter_sort']
    projected['workflow']['nodes'][2:] = [
        node('rows', 'json_transform', [bind('value', 'compute', 'table')], operation='lookup',
             arguments_json=json.dumps({'path': ['rows']}), output_port='records'),
        node('columns', 'filter_sort', [bind('rows', 'rows', 'records')], operation='select_columns',
             arguments_json=json.dumps({'columns': fields}, ensure_ascii=False)),
    ]
    projected['workflow']['edges'] = [edge('read', 'compute'), edge('compute', 'rows'), edge('rows', 'columns')]
    ready = send(f'CSV 저장은 취소해줘. 결과는 {display} 보여줘.',
                 [specs[0], compute], projected, reuse=True)
    assert ready['status'] == 'ready', ready.get('issues')
    current = states[-1]
    assert current['requirement_ledger']['tasks'][-1]['spec'] == dict(kind='select_fields', fields=fields)
    assert any('출력 열' in issue for issue in validate_node({**current, 'draft': unprojected})['issues'])
    run = save_and_run()
    assert run['artifact'] is None
    expected = [dict(수량=2, 주문금액=10), dict(수량=3, 주문금액=21)]
    assert run['rows'] == [{key: row[key] for key in fields} for row in expected]


@pytest.mark.parametrize('request_text, output_format', [
    ('첨부 표의 amount와 note 열을 각각 그대로 유지하고 CSV로 저장해줘.', 'csv'),
    ('예를 들어 report.csv로 저장해줘. 실제로는 JSON으로 저장해줘.', 'json'),
    ('첨부 표는 CSV와 JSON 중 하나야. 이 표를 그대로 CSV로 저장해줘.', 'csv'),
])
def test_one_file_is_saved_even_when_other_clauses_mention_scope_or_format(table_session, request_text, output_format):
    identifier, send, save_and_run, _ = table_session('input.csv', ['amount', 'note'], SIMPLE_ROWS)
    specs, candidate = simple_plan(identifier, request_text, output_format)
    ready = send(request_text, specs, candidate)
    assert ready['status'] == 'ready', ready.get('issues')
    run = save_and_run()
    assert run['artifact']['name'].endswith('.' + output_format)
    text = artifact_text(run['artifact'])
    if output_format == 'json':
        assert json.loads(text) == SIMPLE_ROWS
    else:
        assert list(csv.reader(io.StringIO(text, newline=''))) == [
            ['amount', 'note'], ['75', 'below'], ['100', 'boundary'], ['180', 'above'],
        ]


@pytest.mark.parametrize('reply, final_format', [
    ('CSV 저장은 취소하고 싶지 않아.', 'csv'),
    ('이제 CSV로 저장하고 싶지 않아.', None),
])
def test_negated_save_and_negated_cancellation_are_different(table_session, reply, final_format):
    identifier, send, save_and_run, _ = table_session('input.csv', ['amount', 'note'], SIMPLE_ROWS)
    request = '첨부 표를 CSV로 저장해줘.'
    specs, candidate = simple_plan(identifier, request, 'csv')
    ready = send(request, specs, candidate)
    assert ready['status'] == 'ready', ready.get('issues')
    specs, candidate = simple_plan(identifier, request, final_format)
    ready = send(reply, specs, candidate)
    assert ready['status'] == 'ready', ready.get('issues')
    run = save_and_run()
    assert run['rows'] == SIMPLE_ROWS
    if final_format:
        assert run['artifact']['name'].endswith('.csv')
    else:
        assert run['artifact'] is None


def test_new_grouped_task_keeps_its_own_original_evidence_through_execution(tmp_path):
    from tests.design.test_grouped_totals_request import controlled_draft, state as grouped_state
    from tests.design.test_review151_followup_contract import assert_csv_matches_kit

    current = {**initial(), **grouped_state()}
    original = current['messages'][0]['content']
    current['messages'][:0] = [dict(role='user', content='앞선 업무 ' * 400),
                               dict(role='user', content='새로운 작업이야.')]

    class UnexpectedExtraction:
        def invoke(self, messages):
            pytest.fail('이전 작업의 길이가 새 작업의 확정된 원문 추출을 막았습니다.')

    extraction = make_requirement_check('fixture', UnexpectedExtraction(), 'enforce')(current)
    assert extraction['requirement_route'] == 'plan', extraction.get('requirement_failure')
    current.update(extraction)
    tasks = current['requirement_ledger']['tasks']
    assert len(tasks) == 10
    assert {(task['source_message'], task['quote']) for task in tasks} == {(2, original)}
    current['draft'] = controlled_draft(current)
    validated = validate_node(current)
    assert validated['status'] == 'ready', validated['issues']
    current.update(validated, confirmed=True)

    fixtures = Path(__file__).parents[1] / 'fixtures/manual_kit'
    with (fixtures / 'orders.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    storage = Storage(str(tmp_path))
    try:
        storage.add_asset(current['resources'][0], rows)
        run = run_workflow(storage, storage.save_agent(current), '', '')
    finally:
        storage.close()
    assert run['status'] == 'succeeded', run.get('error')
    assert run['artifact']['name'] == '지역별매출.csv'
    assert_csv_matches_kit(base64.b64decode(run['artifact']['content']), (fixtures / 't02_expected.csv').read_bytes())
