"""Confirmation cannot unseal any original condition or the aggregate row lineage."""
import base64
import csv
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.grouped_totals_contract import grouped_contract, contract_specs
from tests.design.test_grouped_totals_request import state, sealed_state, controlled_draft, upload_profile


def raw_from(plan):
    return dict(tasks=[{k:t[k] for k in ('source_message', 'description', 'spec')}
                       for t in plan['tasks']], unresolved=[])


def assert_csv_matches_kit(actual, expected):
    # Checkout line endings are not CSV data. Preserve field contents (including
    # embedded newlines), header order, row order, duplicates and empty cells.
    def records(content):
        return list(csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True))
    assert records(actual) == records(expected)


def test_csv_oracle_accepts_record_terminators_only():
    assert_csv_matches_kit(b'a,b\r\n1,"x,y"\r\n0,\r\n', b'a,b\n1,"x,y"\n0,\n')


@pytest.mark.parametrize('actual', [
    b'b,a\n1,"x,y"\n0,\n',  # header order
    b'a,b\n0,\n1,"x,y"\n',  # row order
    b'a,b\n2,"x,y"\n0,\n',  # changed value
    b'a,b\n1,"x,y"\n',      # omitted zero/empty row
    b'a,b\n1,"x,y"\n0,\n0,\n',  # duplicate
    b'a,b\n1,x,y\n0,\n',    # wrong quoting adds a column
    b'a,b\n1,"x, y"\n0,\n',  # whitespace within a field
])
def test_csv_oracle_rejects_changed_content(actual):
    with pytest.raises(AssertionError):
        assert_csv_matches_kit(actual, b'a,b\n1,"x,y"\n0,\n')


def test_csv_oracle_does_not_normalize_embedded_newlines():
    with pytest.raises(AssertionError):
        assert_csv_matches_kit(b'a\n"x\r\ny"\n', b'a\n"x\ny"\n')


def contract_packet(current):
    contract = grouped_contract(current)
    return dict(tasks=[dict(source_message=(contract['export_source'] if role.startswith('export_')
        else contract['revisions'].get(role, contract['source'])),
        description=role, spec=spec) for role, spec in contract_specs(contract)], unresolved=[])


def seal(current, packet):
    plan = extract_plan(packet, current)
    return {**current, 'requirement_required':True, 'requirement_ledger':plan,
            'requirement_fingerprint':ledger_fingerprint(current), 'requirement_digest':plan_digest(plan)}


@pytest.mark.parametrize('context', ['ack', 'repeated_ack', 'missing_profile', 'base', 'reference'])
@pytest.mark.parametrize('change', ['status', 'formula', 'sort', 'format', 'source', 'threshold'])
def test_extraction_rejects_changed_values_after_confirmation(context, change):
    current = sealed_state()
    packet = raw_from(current['requirement_ledger'])
    current['messages'].append(dict(role='user', content='네.'))
    if context == 'repeated_ack':
        current['messages'].append(dict(role='user', content='확인했습니다.'))
    elif context == 'missing_profile':
        current['resources'][0].pop('profile')
    elif context == 'base':
        current['base_agent_id'] = 'old-workflow'
    elif context == 'reference':
        current['resources'].append(dict(id='reference', name='참고.txt', kind='document'))
    tasks = packet['tasks']
    if change == 'status': tasks[1]['spec']['filters'][0]['value'] = '취소'
    elif change == 'formula': tasks[2]['spec']['formula'] = '수량 + 단가'
    elif change == 'sort': tasks[7]['spec']['sort'][0]['direction'] = 'descending'
    elif change == 'format': tasks[-1]['spec']['format'] = 'json'
    elif change == 'source': tasks[0]['spec']['file_ids'] = []
    else: tasks[1]['spec']['filters'].append(dict(field='수량', operator='gt', value=0,
                                               compare_field=None, value_type='number'))
    class WrongModel:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return dict(content=json.dumps(packet))
    transport = WrongModel()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert result['requirement_route'] == 'stop'
    assert result['requirement_ledger'] is None
    assert transport.calls == 2


@pytest.mark.parametrize('mutation', ['status', 'formula', 'sort', 'format', 'count_source', 'join_source', 'policy'])
def test_graph_and_save_reject_changes_even_with_a_forged_matching_ledger(mutation, tmp_path):
    from infrastructure.persistence.storage import Storage
    current = sealed_state()
    draft = controlled_draft(current)
    current['messages'].append(dict(role='user', content='네.'))
    current = seal(current, raw_from(current['requirement_ledger']))
    nodes = draft['workflow']['nodes']
    tasks = current['requirement_ledger']['tasks']
    if mutation == 'status':
        nodes[1]['settings']['execution_plan']['filters'][0]['value'] = '취소'
        tasks[1]['spec']['filters'][0]['value'] = '취소'
    elif mutation == 'formula':
        nodes[2]['settings']['calculation_formula'] = '수량 + 단가'
        tasks[2]['spec']['formula'] = '수량 + 단가'
    elif mutation == 'sort':
        nodes[8]['settings']['execution_plan']['sort'][0]['direction'] = 'descending'
        tasks[7]['spec']['sort'][0]['direction'] = 'descending'
    elif mutation == 'format':
        nodes[-1]['settings']['operation'] = 'write_json'
        tasks[-1]['spec']['format'] = 'json'
    elif mutation == 'count_source': nodes[5]['bindings'][0]['node_id'] = nodes[4]['id']
    elif mutation == 'join_source': nodes[7]['bindings'][1]['node_id'] = nodes[4]['id']
    else: nodes[1]['settings']['execution_plan']['missing_policy'] = 'keep'
    current['requirement_digest'] = plan_digest(current['requirement_ledger'])
    current.update(draft=draft, confirmed=True, status='ready')
    assert coverage_report(current, draft)['issues']
    storage = Storage(str(tmp_path))
    try:
        with pytest.raises(ValueError):
            storage.save_agent(current)
        assert storage.agents() == []
    finally:
        storage.close()


@pytest.mark.parametrize('reply, role, expected', [
    ('완료 대신 취소 주문만 대상으로 해줘.', 'filter', '취소'),
    ('계산식은 수량 + 단가로 변경해줘.', 'compute', '수량 + 단가'),
    ('지역 내림차순으로 정렬해줘.', 'sort', 'descending'),
    ('같은 열을 JSON으로 저장해줘.', 'export_json', 'json'),
])
def test_explicit_changes_update_only_the_named_condition(reply, role, expected):
    current = sealed_state()
    original = raw_from(current['requirement_ledger'])
    current['messages'] += [dict(role='user', content=reply), dict(role='user', content='네.')]
    revised = contract_packet(current)
    updated = seal(current, revised)
    specs = dict(zip([r for r, _ in contract_specs(grouped_contract(current))],
                    [t['spec'] for t in updated['requirement_ledger']['tasks']]))
    actual = (specs[role]['filters'][0]['value'] if role == 'filter' else
              specs[role]['formula'] if role == 'compute' else
              specs[role]['sort'][0]['direction'] if role == 'sort' else specs[role]['format'])
    assert actual == expected
    assert coverage_report(updated, controlled_draft(updated))['issues'] == []
    with pytest.raises(ValueError):
        extract_plan(original, current)


def test_unknown_addition_is_not_discarded_or_allowed_to_unseal_old_conditions():
    current = sealed_state()
    old = raw_from(current['requirement_ledger'])
    current['messages'].append(dict(role='user', content='서울만 추가로 남겨줘.'))
    with pytest.raises(ValueError, match='후속 변경'):
        extract_plan(old, current)


@pytest.mark.parametrize('order', [[5, 6, 3, 4], [3, 5, 4, 6]])
def test_independent_aggregate_branch_order_preserves_specs_and_lineage(order):
    current = sealed_state()
    current['messages'].append(dict(role='user', content='네.'))
    raw = contract_packet(current)
    original = deepcopy(raw['tasks'])
    raw['tasks'][3:7] = [original[index] for index in order]
    updated = seal(current, raw)
    tasks = updated['requirement_ledger']['tasks']
    compute = next(t for t in tasks if t['spec']['kind'] == 'compute')
    aggregates = [t for t in tasks if t['spec']['kind'] == 'aggregate']
    assert {t['spec']['aggregation'] for t in aggregates} == {'sum', 'count'}
    assert all(t['depends_on'] == [compute['id']] for t in aggregates)
    assert coverage_report(updated, controlled_draft(updated))['issues'] == []
    raw['tasks'][3]['spec']['sum_field'] = '수량'
    with pytest.raises(ValueError):
        extract_plan(raw, current)


@pytest.mark.parametrize('replies', [[], ['네.'], ['네.', '확인했습니다.'],
    ['같은 열을 JSON으로 저장해줘.', 'CSV로 저장해줘.', '네.'],
    ['완료 대신 취소 주문만 대상으로 해줘.', '네.'],
    ['계산식은 수량 + 단가로 변경해줘.', '네.'],
    ['지역 내림차순으로 정렬해줘.', '네.'],
    ['같은 열을 JSON으로 저장해줘.', '네.'],
    ['CSV 저장은 취소해줘.', '네.', 'CSV로 저장해줘.', '네.']])
def test_t02_followup_design_save_execute_matches_kit_csv(replies, tmp_path, monkeypatch):
    import service.handlers.design as design
    from service.worker import handle
    from tests.design.test_feedback_service_scenarios import UnexpectedConditionCall
    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream); columns, rows = reader.fieldnames, list(reader)
    request_text = state()['messages'][0]['content']
    build = design.build_graph
    class Capability:
        def invoke(self, messages):
            return dict(content=json.dumps(dict(decision='existing_tools', reason='표 집계',
                tool_ids=['read_table','calculate_columns','filter_sort','aggregate_table','join_tables','json_transform','export_result'],
                missing_conditions=[], missing_actions=[], user_quotes=[request_text])))
    class Requirements:
        def invoke(self, messages):
            payload = json.loads(messages[1]['content'])
            packet = contract_packet(dict(messages=payload['conversation'], resources=payload['resources']))
            # Controlled model answer for the kit's textual region column;
            # the prompt deliberately contains no full upload profile.
            for task in packet['tasks']:
                if task['spec']['kind'] == 'sort':
                    task['spec']['sort'][0]['value_type'] = 'text'
            return dict(content=json.dumps(packet))
    monkeypatch.setattr(design, 'build_graph', lambda *a, **kw: build(*a, **{**kw,
        'capability_mode':'enforce','capability_transport':Capability(),'condition_mode':'enforce',
        'condition_transport':UnexpectedConditionCall(),'requirement_mode':'enforce',
        'requirement_transport':Requirements(),'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    result = handle({**base, 'action':'new_session','model':'fixture','credential_id':'fixture'})
    sid = result['id']
    result = handle({**base,'action':'upload','id':sid,'revision':0,
        'asset':dict(name='주문내역.csv',kind='table',format='csv',columns=columns,**upload_profile(columns, rows)), 'rows':rows})
    for text in [request_text, *replies]:
        result = handle({**base,'action':'message','id':sid,'revision':result['revision'],'content':text},
                        conversation=lambda s: dict(draft=controlled_draft(s)))
        assert result['status'] == 'ready', result.get('issues')
    result = handle({**base,'action':'confirm','id':sid,'revision':result['revision']})
    assert result['status'] == 'saved', result.get('issues')
    run = handle({**base,'action':'test','id':result['saved_agent_id'],'content':''})
    assert run['status'] == 'succeeded', run.get('error')
    actual = base64.b64decode(run['artifact']['content'])
    joined = ' '.join(replies)
    fmt = 'json' if replies == ['같은 열을 JSON으로 저장해줘.', '네.'] else 'csv'
    assert run['artifact']['name'] == '지역별매출.' + fmt
    if not any(change in joined for change in ('취소 주문', '계산식', '내림차순')) and fmt == 'csv':
        assert_csv_matches_kit(actual, source.with_name('t02_expected.csv').read_bytes())
    else:
        # Compute a separate oracle from the original kit rows; do not use
        # workflow helpers or the contract being tested to calculate answers.
        grouped = {}
        status = '취소' if '취소 주문' in joined else '완료'
        for row in rows:
            if row['주문상태'] != status:
                continue
            total, count = grouped.get(row['지역'], (0, 0))
            quantity, price = int(row['수량']), int(row['단가'])
            amount = quantity + price if '계산식' in joined else quantity * price
            grouped[row['지역']] = (total + amount, count + 1)
        expected = [dict(지역=region, 총매출=grouped[region][0], 주문건수=grouped[region][1])
                    for region in sorted(grouped, reverse='내림차순' in joined)]
        if fmt == 'json':
            assert json.loads(actual.decode('utf-8-sig')) == expected
        else:
            reader = csv.DictReader(io.StringIO(actual.decode('utf-8-sig')))
            assert reader.fieldnames == ['지역', '총매출', '주문건수']
            assert list(reader) == [{k: str(v) for k, v in row.items()} for row in expected]
