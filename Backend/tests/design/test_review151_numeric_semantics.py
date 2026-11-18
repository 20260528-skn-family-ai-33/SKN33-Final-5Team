"""PR151 numeric semantics through source validation and persisted execution."""
import base64
import csv
import io
import json
from copy import deepcopy

import pytest

from agent_builder.design.grouped_totals_request import grouped_totals_packet
from agent_builder.design.numeric_filter_proof import numeric_filter_issues
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.design.test_grouped_totals_request import controlled_draft, state
from tests.design.test_manual_kit_requirement_regressions import REGIONAL_REQUEST
from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node
from workflow_engine.runner import run_workflow


@pytest.mark.parametrize('writer', [False, True])
@pytest.mark.parametrize('policy', ['keep', 'exclude'])
def test_numeric_proof_checks_missing_policy_and_actual_final_rows(tmp_path, writer, policy):
    columns = ['주문번호', '주문금액']
    rows = [dict(zip(columns, values)) for values in [('PASS', 150000), ('EMPTY', ''), ('LOW', 50000)]]
    rule = dict(field='주문금액', operator='gte', value=100000, value_type='number', compare_field=None)
    plan = dict(filters=[rule], sort=[], match='all', missing_policy=policy,
                duplicate_policy='keep', duplicate_field=None, output_format='json')
    current = dict(messages=[dict(role='user', content='주문금액 100000 이상, 주문번호·주문금액을 고액주문.csv로 저장해줘.')],
                   resources=[dict(id='data', name='주문.csv', kind='table', columns=columns)])
    nodes = [node('read', 'read_table', file_ids=['data']),
             node('filter', 'filter_sort', [bind('table', 'read', 'table')], execution_plan=plan)]
    if writer:
        nodes.append(node('out', 'write_result', [bind('table', 'filter', 'table')], format='json'))
    else:
        current['messages'].append(dict(role='user', content='CSV 저장은 취소해줘.'))
    workflow = dict(entry='read', nodes=nodes, edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])])
    draft = dict(workflow=workflow, execution_plan=plan)
    assert bool(numeric_filter_issues(current, draft)) == (policy == 'keep')
    store, identifier, _ = saved(tmp_path, workflow, execution_plan=plan)
    try:
        store.add_asset(current['resources'][0], rows)
        result = run_workflow(store, identifier, '실행', '', file_ids=['data'])
        assert result['status'] == 'succeeded', result.get('error')
        expected = rows[:2] if policy == 'keep' else rows[:1]
        assert result['rows'] == expected
        if writer:
            assert json.loads(result['artifact']['content']) == expected
    finally:
        store.close()


@pytest.mark.parametrize('values', [
    ('0x10', '0x2'), ('0b10', '0b1'), ('0o10', '0o2'),
    ('9007199254740993', '9007199254740992'),
    ('9007199254740992.2', '9007199254740992.1'),
])
@pytest.mark.parametrize('descending', [False, True])
def test_upload_numeric_profile_saves_and_runs_exact_group_sort(values, descending, tmp_path, monkeypatch):
    import service.handlers.design as design
    from service.worker import handle
    from tests.design.test_feedback_service_scenarios import UnexpectedConditionCall

    request = REGIONAL_REQUEST.replace('오름차순', '내림차순') if descending else REGIONAL_REQUEST
    columns = ['주문번호', '주문상태', '지역', '수량', '단가']
    rows = [dict(zip(columns, [str(index), '완료', value, '2', '7'])) for index, value in enumerate(values)]
    # Exact upload API profile for these fixtures. In particular Number('0x10') is 16.
    # Do not recompute numericStrings with Python float, which rejects radix strings.
    profile = {column: dict(types=['string'], missing=0,
                            duplicateValues=1 if column in ('주문상태', '수량', '단가') else 0,
                            numericStrings=2 if column in ('주문번호', '지역', '수량', '단가') else 0)
               for column in columns}
    asset = dict(name='주문내역.csv', kind='table', format='csv', columns=columns, profile=profile, row_count=2)
    build = design.build_graph

    class Capabilities:
        def invoke(self, messages):
            return dict(content=json.dumps(dict(decision='existing_tools', reason='집계와 정렬',
                tool_ids=['read_table', 'filter_sort', 'calculate_columns', 'aggregate_table',
                          'join_tables', 'json_transform', 'export_result'],
                missing_conditions=[], missing_actions=[], user_quotes=[request])))

    class NoExtraction:
        def invoke(self, messages):
            pytest.fail('The complete grouped contract should use the deterministic packet')

    monkeypatch.setattr(design, 'build_graph', lambda *args, **kwargs: build(*args, **{**kwargs,
        'capability_mode': 'enforce', 'capability_transport': Capabilities(),
        'requirement_mode': 'enforce', 'requirement_transport': NoExtraction(),
        'condition_mode': 'enforce', 'condition_transport': UnexpectedConditionCall(), 'verifier_mode': 'off'}))
    base = dict(directory=str(tmp_path))
    result = handle({**base, 'action': 'new_session', 'model': 'fixture', 'credential_id': 'fixture'})
    session_id = result['id']
    result = handle({**base, 'action': 'upload', 'id': session_id, 'revision': 0, 'asset': asset, 'rows': rows})
    current = dict(messages=[dict(role='user', content=request)],
                   resources=[{**asset, 'id': result['resources'][0]['id']}])
    current.update(make_requirement_check('fixture', mode='enforce')(current))
    draft = controlled_draft(current)
    draft['instructions'] = request
    draft['requirements'][0]['quote'] = request
    result = handle({**base, 'action': 'message', 'id': session_id, 'revision': result['revision'], 'content': request},
                    conversation=lambda actual: dict(draft=deepcopy(draft)))
    assert result['status'] == 'ready', result.get('issues')
    result = handle({**base, 'action': 'confirm', 'id': session_id, 'revision': result['revision']})
    assert result['status'] == 'saved', result.get('issues')
    run = handle({**base, 'action': 'test', 'id': result['saved_agent_id'], 'content': ''})
    assert run['status'] == 'succeeded', run.get('error')
    reader = csv.DictReader(io.StringIO(base64.b64decode(run['artifact']['content']).decode('utf-8-sig')))
    assert reader.fieldnames == ['지역', '총매출', '주문건수']
    expected_order = values if descending else tuple(reversed(values))
    assert list(reader) == [dict(지역=value, 총매출='14', 주문건수='1') for value in expected_order]


@pytest.mark.parametrize('original, normalized', [('Ａ', 'A'), ('K', 'K')])
@pytest.mark.parametrize('collision', [False, True])
def test_normalized_formula_never_reads_a_different_column(original, normalized, collision):
    from workflow_engine.executors.table.calculate_columns import execute

    current = state()
    current['messages'][0]['content'] = REGIONAL_REQUEST.replace('수량', original)
    columns = current['resources'][0]['columns']
    columns[columns.index('수량')] = original
    if collision:
        columns.append(normalized)
    assert grouped_totals_packet(current) is None
    row = {original: '2', '단가': '7', normalized: '9'} if collision else {original: '2', '단가': '7'}
    table = dict(columns=list(row), rows=[row])
    calculation = node('compute', 'calculate_columns', [bind('table', 'read', 'table')],
                       table_operation='compute', calculation_formula=f'{original} * 단가', output_column='총액')
    with pytest.raises(ValueError, match='정규화'):
        execute(calculation, dict(outputs={'read': {'table': table}}), None, {}, None)
    calculation['settings']['calculation_formula'] = '단가 * 2'
    assert execute(calculation, dict(outputs={'read': {'table': table}}), None, {}, None)['table']['rows'] == [{**row, '총액': 14}]


def test_large_numeric_threshold_and_compare_field_preserve_precision():
    rows = [{'value': '9007199254740992', 'threshold': '9007199254740993'},
            {'value': '9007199254740993', 'threshold': '9007199254740993'}]
    for compare_field in (None, 'threshold'):
        context = dict(columns=['value', 'threshold'], rows=rows, trace=[])
        rule = dict(field='value', operator='gte', value='9007199254740993',
                    value_type='number', compare_field=compare_field)
        filter_sort_node(context, [rule], [], missing_policy='error')
        assert context['rows'] == rows[1:]


def test_numeric_comparisons_keep_rows_and_audit_json_serializable():
    rows = [{'qty': '0x10'}, {'qty': '9007199254740993'}]
    rule = dict(field='qty', operator='gte', value='1,000', value_type='number', compare_field=None)
    sort = [dict(field='qty', direction='ascending', value_type='number')]
    context = dict(columns=['qty'], rows=deepcopy(rows), trace=[])
    result = filter_sort_node(context, [rule], sort, missing_policy='error')
    assert context['rows'] == rows[1:]
    assert context['trace'][0]['parameters']['filters'][0]['value'] == '1,000'
    assert json.loads(json.dumps(context)) == context
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize('formula', ['Ａ * 단가', 'K * 단가'])
def test_grouped_followup_formula_rejects_normalized_column_alias(formula):
    from agent_builder.design.grouped_totals_contract import grouped_contract
    from agent_builder.design.requirement_ledger import extract_plan
    from tests.design.test_review151_followup_contract import raw_from
    from tests.design.test_grouped_totals_request import sealed_state

    current = sealed_state()
    original_packet = raw_from(current['requirement_ledger'])
    current['resources'][0]['columns'] += ['Ａ', 'A', 'K', 'K']
    change = f'계산식은 {formula}로 변경해줘.'
    current['messages'] += [dict(role='user', content=change), dict(role='user', content='네.')]
    contract = grouped_contract(current)
    assert contract['unknown'] == [change]
    assert contract['formula'] == '수량 * 단가'
    with pytest.raises(ValueError):
        extract_plan(original_packet, current)
    changed_packet = deepcopy(original_packet)
    changed_packet['tasks'][2]['spec']['formula'] = formula
    with pytest.raises(ValueError):
        extract_plan(changed_packet, current)


@pytest.mark.parametrize('preview_source', ['read', 'apply'])
def test_strict_numeric_filter_checks_post_export_preview(preview_source):
    from tests.design.test_final_table_criteria import configured
    from tests.design.test_explicit_keep_policy import node as table_node
    from tests.design.test_explicit_output_columns import set_nodes

    rules = [dict(field='qty', operator='gte', value=10, compare_field=None, value_type='number')]
    current = configured(dict(selection=rules, missing_policy='exclude'))
    neutral = current['draft']['execution_plan']
    set_nodes(current, [
        table_node('read', 'read_table', file_ids=['source']),
        table_node('apply', 'filter_sort', 'read', execution_plan={**neutral, 'filters': rules, 'missing_policy': 'exclude'}),
        table_node('output', 'write_result', 'apply', format='json'),
        table_node('preview', 'filter_sort', preview_source, execution_plan=neutral),
    ])
    assert bool(numeric_filter_issues(current, current['draft'])) == (preview_source == 'read')
