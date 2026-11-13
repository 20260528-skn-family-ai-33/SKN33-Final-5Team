"""PR 103 final review: cancellation, particles and compound row predicates."""
import base64
import csv
import io
import json
from copy import deepcopy

import pytest

from agent_builder.design.output_selection_request import output_selection
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.request_guards import unary_filter_issue
from tests.design.test_builder import initial, draft
from tests.design.test_feedback_requirement_extraction import state, packet
from tests.design.test_feedback_service_scenarios import COLUMNS, ROWS, order_draft
from tests.workflow_engine.test_workflow import node, bind, edge


CANCEL = '주문번호·주문금액을 고액주문.csv로 저장하지 말고 전체 열을 저장해줘.'


def cancelled_state():
    current = {**initial(), **state()}
    content = current['messages'][-1]['content']
    current['messages'][-1]['content'] = content[:content.index('주문번호·')] + CANCEL
    return current


@pytest.mark.parametrize('ending', ['저장하지 말고 전체 열을 저장해줘.', '저장하지 마.', '저장하지 않아도 돼.'])
def test_cancelled_export_is_never_projected_into_extraction(ending):
    current = state()
    current['messages'][-1]['content'] = '주문번호·주문금액을 고액주문.csv로 ' + ending
    raw = packet(); del raw['tasks'][4]
    assert output_selection(current) is None
    # Keep the other source criteria valid for the complete extraction test.
    current['messages'][-1]['content'] = state()['messages'][-1]['content'].split('주문번호·')[0] + current['messages'][-1]['content']
    result = extract_plan(raw, current)
    assert not any(t['spec']['kind'] == 'select_fields' for t in result['tasks'])


def test_full_output_request_rejects_a_model_selected_subset():
    with pytest.raises(ValueError, match='전체 열'):
        extract_plan(packet(), cancelled_state())


@pytest.mark.parametrize('selected,exported,ready', [
    (['주문번호', '주문금액'], ['주문번호', '주문금액'], False),
    (COLUMNS + ['주문금액'], ['주문번호', '주문금액'], False),
    (COLUMNS + ['주문금액'], COLUMNS + ['주문금액'], True),
])
@pytest.mark.parametrize('enforce', [False, True])
def test_all_columns_guard_checks_real_projection_and_export(selected, exported, ready, enforce, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    current = cancelled_state()
    raw = packet(); del raw['tasks'][4]
    ledger = extract_plan(raw, current)
    current.update(requirement_required=enforce, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    config = order_draft('table-source')
    config['instructions'] = current['messages'][-1]['content']
    config['requirements'][-1]['quote'] = current['messages'][-1]['content']
    config['workflow']['nodes'][-2]['settings']['arguments_json'] = json.dumps(dict(columns=selected))
    config['workflow']['nodes'][-1]['settings']['arguments_json'] = json.dumps(dict(columns=exported, path='전체주문.csv'))
    result = validate_node({**current, 'draft':config})
    assert (result['status'] == 'ready') == ready, result['issues']
    if not ready:
        assert any('전체 열' in issue for issue in result['issues'])


@pytest.mark.parametrize('last,columns,expected', [
    ('주문금액만을', COLUMNS, ['주문번호', '주문금액']),
    ('주문금액만을', COLUMNS + ['주문금액만'], ['주문번호', '주문금액만']),
    ('주문금액만을', COLUMNS + ['주문금액', '주문금액만'], None),
    ('주문금액을', COLUMNS, ['주문번호', '주문금액']),
])
def test_only_particle_respects_real_column_names(last, columns, expected):
    current = state(); current['resources'][0]['columns'] = columns
    current['messages'][-1]['content'] = f'주문번호·{last} 고액주문.csv로 저장해줘.'
    assert output_selection(current) == expected


def test_quoted_export_example_does_not_supply_columns():
    current = state()
    current['messages'][-1]['content'] = '메모에 "주문번호·주문금액을 고액주문.csv로 저장해줘"라는 예시를 넣어줘.'
    assert output_selection(current) is None


def test_cancelled_export_can_be_replaced_by_an_affirmative_new_list():
    current = state()
    current['messages'][-1]['content'] = ('주문번호·주문금액을 고액주문.csv로 저장하지 말고 '
                                        '주문번호·주문상태를 상태.csv로 저장해줘.')
    assert output_selection(current) == ['주문번호', '주문상태']


@pytest.mark.parametrize('suffix', ['남기고 주문상태가 취소인 행은 제외해줘',
                                    '남기며 주문상태가 취소인 행은 삭제해줘',
                                    '제외하고 주문상태가 완료인 행만 남겨줘'])
@pytest.mark.parametrize('predicate,empty', [('빈', True), ('비어 있지 않은', False)])
def test_empty_predicate_uses_its_own_action(suffix, predicate, empty):
    text = f'이메일이 {predicate} 행만 {suffix}'
    remove = suffix.startswith('제외')
    expected = 'is_empty' if empty != remove else 'not_empty'
    for operator in ('is_empty', 'not_empty'):
        issue = unary_filter_issue(dict(field='이메일', operator=operator), text)
        assert (issue is None) == (operator == expected)


def _run_csv(tmp_path, current, config, columns, rows):
    from contracts.models import AgentDraft
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    checked = validate_node({**current, 'draft':config})
    assert checked['status'] == 'ready', checked['issues']
    store = Storage(tmp_path)
    try:
        store.add_asset(dict(id='table-source', name='orders.csv', kind='table', format='csv', columns=columns), rows)
        identifier = store.save_agent({**current, 'confirmed':True, 'draft':AgentDraft.model_validate(config).model_dump()})
    finally:
        store.close()
    run = handle(dict(directory=str(tmp_path), action='test', id=identifier, content=''))
    assert run['status'] == 'succeeded', run.get('error')
    artifact = run['artifact']
    content = (base64.b64decode(artifact['content']).decode('utf-8-sig')
               if artifact.get('encoding') == 'base64' else artifact['content'].lstrip('\ufeff'))
    reader = csv.DictReader(io.StringIO(content))
    return reader.fieldnames, list(reader)


@pytest.mark.parametrize('all_columns', [False, True])
def test_particle_and_cancellation_reach_successful_csv(tmp_path, all_columns):
    current = cancelled_state() if all_columns else {**initial(), **state()}
    if not all_columns:
        current['messages'][-1]['content'] = current['messages'][-1]['content'].replace('주문금액을', '주문금액만을')
    selected = COLUMNS + ['주문금액'] if all_columns else ['주문번호', '주문금액']
    raw = packet(); raw['tasks'][4]['spec']['fields'] = selected
    ledger = extract_plan(raw, current)
    current.update(requirement_required=True, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    config = deepcopy(order_draft('table-source'))
    config['instructions'] = current['messages'][-1]['content']
    config['requirements'][-1]['quote'] = current['messages'][-1]['content']
    for n in config['workflow']['nodes'][-2:]:
        arguments = json.loads(n['settings']['arguments_json'])
        arguments['columns'] = selected
        n['settings']['arguments_json'] = json.dumps(arguments)
    fields, rows = _run_csv(tmp_path, current, config, COLUMNS, ROWS)
    assert fields == selected
    assert [r['주문번호'] for r in rows] == ['ORD-001', 'ORD-003']
    assert [r['주문금액'] for r in rows] == ['100000', '150000']


def test_compound_filter_rejects_inversion_and_executes_correct_rows(tmp_path):
    text = '이메일이 빈 행만 남기고 주문상태가 취소인 행은 제외해줘. CSV로 저장해줘.'
    columns = ['id', '이메일', '주문상태']
    current = initial()
    current.update(messages=[dict(role='user', content=text)],
                   resources=[dict(id='table-source', name='orders.csv', kind='table', columns=columns)])
    config = draft()
    config.update(instructions=text, document_ids=['table-source'], requirements=[dict(description='선별', source_id='user', quote=text)])
    plan = config['execution_plan']
    plan.update(missing_policy='keep', filters=[
        dict(field='이메일', operator='is_empty', value=None, compare_field=None, value_type='text'),
        dict(field='주문상태', operator='ne', value='취소', compare_field=None, value_type='text')])
    nodes = [node('read', 'read_table', file_ids=['table-source']),
             node('filter', 'filter_sort', [bind('table', 'read', 'table')], execution_plan=plan),
             node('write', 'write_result', [bind('table', 'filter', 'table')], format='csv')]
    config['workflow'] = dict(entry='read', nodes=nodes, edges=[edge('read', 'filter'), edge('filter', 'write')])
    wrong = deepcopy(config)
    wrong['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['operator'] = 'not_empty'
    assert validate_node({**current, 'draft':wrong})['status'] != 'ready'
    rows = [dict(zip(columns, row)) for row in [('keep', '', '완료'), ('cancel', '', '취소'), ('filled', 'test@example.com', '완료')]]
    fields, actual = _run_csv(tmp_path, current, config, columns, rows)
    assert fields == columns
    assert actual == [rows[0]]
