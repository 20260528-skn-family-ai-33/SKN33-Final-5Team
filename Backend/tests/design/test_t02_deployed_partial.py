"""A saved production workflow ran successfully but omitted count and output names."""
import csv
import io
import json
from pathlib import Path

import pytest

from agent_builder.design.requirement_ledger import (
    ledger_fingerprint, make_requirement_check, plan_digest, validate_plan,
)
from agent_builder.design.requirement_coverage import coverage_report
from tests.design.test_grouped_totals_request import state


FIXTURES = Path(__file__).parents[1] / 'fixtures/manual_kit'


def recorded():
    return json.loads((FIXTURES / 't02_deployed_partial.json').read_text(encoding='utf-8'))


def legacy_state():
    capture = recorded()
    current = state()
    current.update(requirement_required=True, requirement_ledger=capture['ledger'], draft=capture['draft'])
    # Even matching hashes cannot make an incomplete legacy ledger authoritative.
    current['requirement_fingerprint'] = ledger_fingerprint(current)
    current['requirement_digest'] = plan_digest(current['requirement_ledger'])
    return current


def test_saved_production_csv_is_not_the_kit_answer():
    capture = recorded()
    reader = csv.DictReader(io.StringIO(capture['actual_csv'].lstrip('\ufeff')))
    rows = list(reader)
    expected = list(csv.DictReader(io.StringIO((FIXTURES / 't02_expected.csv').read_text(encoding='utf-8-sig'))))
    assert reader.fieldnames == ['지역', 'sum']
    assert [(r['지역'], r['sum']) for r in rows] == [(r['지역'], r['총매출']) for r in expected]
    assert rows != expected  # Matching sums alone are not a successful task.


def test_matching_fingerprints_cannot_approve_the_saved_read_only_ledger():
    current = legacy_state()
    with pytest.raises(ValueError):
        validate_plan(current['requirement_ledger'], current)
    report = coverage_report(current, current['draft'])
    assert report['issues']
    assert not report['tasks']


def test_legacy_ledger_is_rebuilt_and_the_saved_six_node_draft_is_rejected():
    current = legacy_state()
    class NoModel:
        def invoke(self, messages):
            pytest.fail('The full explicit request is already grounded in its source.')
    repaired = make_requirement_check('fixture', NoModel(), 'enforce')(current)
    assert repaired['requirement_route'] == 'plan'
    tasks = repaired['requirement_ledger']['tasks']
    assert len(tasks) == 10
    assert {t['spec']['aggregation'] for t in tasks if t['spec']['kind'] == 'aggregate'} == {'sum', 'count'}
    assert coverage_report({**current, **repaired}, current['draft'])['issues']


@pytest.mark.parametrize('context', ['acknowledgement', 'saved_base', 'missing_profile', 'reference_document'])
@pytest.mark.parametrize('missing', ['count', 'total_name', 'count_name', 'columns'])
def test_partial_processing_still_cannot_hide_required_count_or_output_names(context, missing):
    from agent_builder.design.grouped_totals_request import grouped_totals_packet, validate_grouped_totals_intent
    current = state()
    plan = grouped_totals_packet(current)
    if context == 'acknowledgement':
        current['messages'].append(dict(role='user', content='네.'))
    elif context == 'saved_base':
        current['base_agent_id'] = 'deployed-v1'
    elif context == 'missing_profile':
        current['resources'][0].pop('profile')
    else:
        current['resources'].append(dict(id='reference', name='참고.txt', kind='document'))
    assert grouped_totals_packet(current) is None
    validate_grouped_totals_intent(plan, current)
    def omitted(spec):
        return ((missing == 'count' and spec['kind'] == 'aggregate' and spec['aggregation'] == 'count')
                or (missing == 'total_name' and spec['kind'] == 'rename' and spec['source'] == 'sum')
                or (missing == 'count_name' and spec['kind'] == 'rename' and spec['source'] == 'count')
                or (missing == 'columns' and spec['kind'] == 'select_fields'))
    plan['tasks'] = [t for t in plan['tasks'] if not omitted(t['spec'])]
    with pytest.raises(ValueError):
        validate_grouped_totals_intent(plan, current)


@pytest.mark.parametrize('context', ['acknowledgement', 'saved_base', 'missing_profile', 'reference_document'])
def test_general_extractor_rejects_the_same_sum_only_work_as_the_saved_workflow(context):
    from agent_builder.design.grouped_totals_request import grouped_totals_packet
    current = state()
    raw = grouped_totals_packet(current)
    raw['tasks'] = [t for t in raw['tasks'] if t['spec']['kind'] not in {'rename', 'select_fields'}
                    and not (t['spec']['kind'] == 'aggregate' and t['spec']['aggregation'] == 'count')]
    if context == 'acknowledgement':
        current['messages'].append(dict(role='user', content='네.'))
    elif context == 'saved_base':
        current['base_agent_id'] = 'deployed-v1'
    elif context == 'missing_profile':
        current['resources'][0].pop('profile')
    else:
        current['resources'].append(dict(id='reference', name='참고.txt', kind='document'))
    class SumOnlyModel:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return dict(content=json.dumps(raw))
    transport = SumOnlyModel()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert result['requirement_route'] == 'stop'
    assert result['requirement_ledger'] is None
    assert transport.calls == 2


def test_existing_saved_graph_reproduces_the_production_csv(tmp_path):
    """Import an already saved v1 locally; do not bypass any *new* save validation."""
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    capture = recorded()
    with (FIXTURES / 'orders.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns, rows = reader.fieldnames, list(reader)
    storage = Storage(str(tmp_path))
    try:
        storage.add_asset(dict(id='table-source', name='주문내역.csv', kind='table', format='csv',
                               columns=columns, created_at='2026-11-18T05:00:00+00:00'), rows)
        agent = dict(id='deployed-v1', version=1, config=capture['draft'], model='fixture',
                     credential_id='fixture', created_at='2026-11-18T05:06:50+00:00')
        storage.db.execute('INSERT INTO agent_versions(agent_id,version,json) VALUES(?,?,?)',
                           (agent['id'], 1, json.dumps(agent, ensure_ascii=False)))
        storage.db.commit()
    finally:
        storage.close()
    run = handle(dict(directory=str(tmp_path), action='test', id='deployed-v1', content=''))
    assert run['status'] == 'succeeded', run.get('error')
    actual = run['artifact']['content'].lstrip('\ufeff')
    assert actual == capture['actual_csv'].lstrip('\ufeff')
    assert actual != (FIXTURES / 't02_expected.csv').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('suffix', [' 처리 결과도 설명해줘.', '\n처리 결과도 설명해줘.'])
def test_explanation_does_not_allow_read_only_extraction(suffix):
    current = state()
    current['messages'][0]['content'] += suffix
    raw = dict(tasks=[dict(source_message=0, description='read', spec=dict(kind='read', file_ids=['table-source']))], unresolved=[])
    class PartialModel:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return dict(content=json.dumps(raw))
    transport = PartialModel()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 2
    assert result['requirement_route'] == 'stop'
    assert result['requirement_ledger'] is None


@pytest.mark.parametrize('text', [
    '첨부 표를 amount 오름차순으로 정렬해줘. JSON으로 저장해줘. 처리 결과도 설명해줘.',
    '첨부 표를 amount 오름차순으로 정렬하고 결과도 설명해줘.',
])
def test_explanation_does_not_allow_a_legacy_workflow_without_sort(text):
    from agent_builder.capabilities.capability_gate import input_fingerprint, require_save_ready
    from agent_builder.design.nodes.validation import validate_node
    from tests.design.test_task_omission import processing_request
    from tests.workflow_engine.test_workflow import node, bind, edge
    current = processing_request([node('read', 'read_table'),
        node('out', 'write_result', [bind('table', 'read', 'table')], format='json')], [edge('read', 'out')])
    current['messages'] = [dict(role='user', content=text)]
    current['draft'].update(instructions=text, purpose=text,
        requirements=[dict(description='정렬과 출력', source_id='user', quote=text)])
    current['capability_assessment'].update(user_quotes=[text], tool_ids=['read_table', 'filter_sort', 'write_result'])
    current['capability_fingerprint'] = input_fingerprint(current)
    result = validate_node(current)
    assert result['status'] != 'ready'
    with pytest.raises(ValueError):
        require_save_ready({**current, **result, 'confirmed': True})


@pytest.mark.parametrize('text', ['정렬이 뭔가요?', '계산과 정렬의 차이를 설명해줘.',
                                    '"정렬해줘"라는 문구는 무슨 뜻인가요?'])
def test_explanation_alone_does_not_create_processing_requirements(text):
    from agent_builder.design.processing_guard import _legacy_actions
    assert not _legacy_actions(dict(messages=[dict(role='user', content=text)]))


@pytest.mark.parametrize('separator', ['\n', '\r\n'])
@pytest.mark.parametrize('explain', [False, True])
def test_multiline_numeric_requirement_still_rejects_a_missing_filter(separator, explain):
    from agent_builder.design.output_selection_request import literal_numeric_filters
    from agent_builder.design.requirement_ledger import extract_plan
    text = separator.join(['주문금액 100000 이상', '주문번호·주문금액을 고액주문.csv로 저장해줘.'])
    if explain:
        text += separator + '처리 결과도 설명해줘.'
    current = dict(messages=[dict(role='user', content=text)], resources=[
        dict(id='table-source', kind='table', name='표.csv', columns=['주문번호', '주문금액'])])
    rules = literal_numeric_filters(current)
    assert len(rules) == 1 and rules[0]['field'] == '주문금액' and rules[0]['value'] == 100000
    specs = [dict(kind='read', file_ids=['table-source']), dict(kind='select_fields', fields=['주문번호', '주문금액']),
             dict(kind='export', format='csv')]
    packet = dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs], unresolved=[])
    with pytest.raises(ValueError, match='숫자 선별 조건'):
        extract_plan(packet, current)
    packet['tasks'].insert(1, dict(source_message=0, description='금액 선별',
                                 spec=dict(kind='filter', match='all', filters=rules)))
    assert extract_plan(packet, current)['tasks'][1]['spec']['filters'] == rules
    current['messages'].append(dict(role='user', content='주문금액 조건을 취소해줘.' + separator +
        '주문번호·주문금액을 결과.csv로 저장해줘.'))
    assert literal_numeric_filters(current) == []
    packet['tasks'].pop(1)
    assert not any(t['spec']['kind'] == 'filter' for t in extract_plan(packet, current)['tasks'])


@pytest.mark.parametrize('renames', [{'총매출':'sum'}, {'주문건수':'count'}, {'총매출':'sum', '주문건수':'count'}])
def test_existing_aggregate_output_names_do_not_need_noop_renames(renames):
    from agent_builder.design.grouped_totals_request import grouped_totals_packet, validate_grouped_totals_intent
    current = state()
    packet = grouped_totals_packet(current)
    for before, after in renames.items():
        current['messages'][0]['content'] = current['messages'][0]['content'].replace(before, after)
        packet = json.loads(json.dumps(packet, ensure_ascii=False).replace(before, after))
    packet['tasks'] = [t for t in packet['tasks'] if not (t['spec']['kind'] == 'rename'
                      and t['spec']['source'] == t['spec']['target'])]
    assert grouped_totals_packet(current) is None
    validate_grouped_totals_intent(packet, current)
