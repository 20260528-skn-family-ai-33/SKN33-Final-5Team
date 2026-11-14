"""Main feedback: range edits, cancelled predicates and plural output columns."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.output_selection_request import output_selection, literal_numeric_filters
from agent_builder.design.numeric_filter_proof import numeric_filter_issues
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.nodes.validation import validate_node
from tests.design.test_builder import initial
from tests.design.test_feedback_requirement_extraction import state, packet
from tests.design.test_feedback_service_scenarios import order_draft, COLUMNS, ROWS, RULES
from tests.design.test_feedback_final_review import _run_csv
from tests.workflow_engine.test_workflow import node, bind, edge


UPPER = dict(field='주문금액', operator='lte', value=130000, compare_field=None, value_type='number')


def revised(*replies):
    current = {**initial(), **deepcopy(state())}
    current['messages'].extend(dict(role='user', content=reply) for reply in replies)
    return current


def changed(filters=None, columns=None):
    raw = packet()
    config = deepcopy(order_draft('table-source'))
    if filters is not None:
        raw['tasks'][2]['spec']['filters'] = deepcopy(filters)
        config['execution_plan']['filters'] = deepcopy(filters)
        config['workflow']['nodes'][2]['settings']['execution_plan']['filters'] = deepcopy(filters)
    if columns is not None:
        raw['tasks'][4]['spec']['fields'] = columns
        for n in config['workflow']['nodes'][-2:]:
            args = json.loads(n['settings']['arguments_json'])
            args['columns'] = columns
            n['settings']['arguments_json'] = json.dumps(args)
    return raw, config


@pytest.mark.parametrize('reply', ['상한도 추가해줘, 주문금액 130000 이하', '주문금액 130000 이하'])
def test_upper_bound_keeps_existing_lower_bound(reply):
    current = revised(reply)
    assert literal_numeric_filters(current) == [RULES[1], UPPER]
    raw, _ = changed(filters=[RULES[0], UPPER])
    with pytest.raises(ValueError, match='숫자 선별 조건'):
        extract_plan(raw, current)


def test_changing_one_bound_keeps_the_opposite_bound():
    current = revised('상한도 추가해줘, 주문금액 130000 이하', '주문금액 110000 이상')
    rules = literal_numeric_filters(current)
    assert {r['operator']: r['value'] for r in rules} == {'gte':110000, 'lte':130000}


def test_explicit_full_replacement_discards_old_range():
    current = revised('상한도 추가해줘, 주문금액 130000 이하', '기존 조건 대신 주문금액 200000 이상')
    assert literal_numeric_filters(current) == [{**RULES[1], 'value':200000}]


def test_cancellation_rejects_stale_predicate_in_ledger():
    current = revised('주문금액 조건을 취소해줘')
    with pytest.raises(ValueError, match='취소'):
        extract_plan(packet(), current)


@pytest.mark.parametrize('required', [False, True])
def test_cancellation_rejects_actual_stale_filter_even_with_correct_ledger(required, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    current = revised('주문금액 조건을 취소해줘')
    raw, _ = changed(filters=[RULES[0]])
    ledger = extract_plan(raw, current)
    current.update(requirement_required=required, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    bad = deepcopy(order_draft('table-source'))
    assert numeric_filter_issues(current, bad)
    result = validate_node({**current, 'draft':bad})
    assert result['status'] != 'ready'
    assert any('취소' in issue for issue in result['issues'])


@pytest.mark.parametrize('reply', [
    '주문번호와 주문금액 열만 저장해줘',
    '주문번호, 주문금액 열만 저장해줘',
    '주문번호 및 주문금액 컬럼만 저장해줘',
    '주문번호·주문금액 열만 저장해줘',
    '주문금액과 주문번호 열만 저장해줘',
])
def test_plural_output_preserves_all_columns_in_order(reply):
    expected = ['주문금액','주문번호'] if reply.startswith('주문금액') else ['주문번호','주문금액']
    assert output_selection(revised(reply)) == expected


@pytest.mark.parametrize('reply', ['없는열과 주문금액 열만 저장해줘', '없는열과 주문번호와 주문금액 열만 저장해줘'])
def test_unknown_list_prefix_cannot_be_silently_dropped(reply):
    assert output_selection(revised(reply)) is None


@pytest.mark.parametrize('reply', ['주문번호만 저장해줘', '주문번호 열만 저장해줘'])
def test_single_column_is_still_supported(reply):
    assert output_selection(revised(reply)) == ['주문번호']


@pytest.mark.parametrize('reply', ['주문금액 조건을 취소하지 마', '"주문금액 조건을 취소해줘"라는 예시를 설명해줘'])
def test_negated_or_quoted_cancellation_keeps_filter(reply):
    current = revised(reply)
    assert literal_numeric_filters(current) == [RULES[1]]
    assert extract_plan(packet(), current)


def test_later_explicit_threshold_is_allowed_after_cancellation():
    current = revised('주문금액 조건을 취소해줘', '주문금액 100000 이상')
    assert extract_plan(packet(), current)
    assert not numeric_filter_issues(current, deepcopy(order_draft('table-source')))


@pytest.mark.parametrize('required', [False, True])
def test_upper_only_execution_cannot_pass_a_correct_range_ledger(required, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    current = revised('상한도 추가해줘, 주문금액 130000 이하')
    raw, _ = changed(filters=[*RULES, UPPER])
    ledger = extract_plan(raw, current)
    current.update(requirement_required=required, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    _, bad = changed(filters=[RULES[0], UPPER])
    assert validate_node({**current, 'draft':bad})['status'] != 'ready'


@pytest.mark.parametrize('required', [False, True])
def test_one_column_execution_cannot_pass_a_two_column_request(required, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    current = revised('주문번호와 주문금액 열만 저장해줘')
    raw = packet(); raw['tasks'][4]['source_message'] = 2
    ledger = extract_plan(raw, current)
    current.update(requirement_required=required, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    _, bad = changed(columns=['주문금액'])
    assert validate_node({**current, 'draft':bad})['status'] != 'ready'


@pytest.mark.parametrize('field,blocked', [('주문금액',True), ('수량',False)])
def test_cancelled_predicate_in_canonical_filter(field, blocked):
    current = revised('주문금액 조건을 취소해줘')
    _, config = changed(filters=[RULES[0]])
    nodes = config['workflow']['nodes']
    filtering = node('canonical_filter','filter_sort',[bind('rows','rows','records')],operation='filter',
                     arguments_json=json.dumps({'predicates':[{'column':field,'op':'gte','value':1}]}))
    nodes.insert(4,filtering)
    nodes[5]['bindings']=[bind('rows','canonical_filter','records')]
    config['workflow']['edges']=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]
    assert bool(numeric_filter_issues(current,config)) == blocked


def test_cancelled_filter_on_an_export_branch_is_rejected():
    from tests.design.test_feedback_nov13 import _branched_output
    current = revised('주문금액 조건을 취소해줘')
    config = _branched_output(COLUMNS+['주문금액'])['draft']
    assert numeric_filter_issues(current,config)


def test_legacy_plan_rejects_cancelled_filter():
    _, config = changed()
    config['workflow'] = None
    assert numeric_filter_issues(revised('주문금액 조건을 취소해줘'),config)


def test_cancel_then_restore_in_same_turn_uses_last_instruction():
    current = revised('주문금액 조건을 취소해줘; 주문금액 100000 이상')
    assert extract_plan(packet(),current)
    assert not numeric_filter_issues(current,deepcopy(order_draft('table-source')))


def test_cancellation_does_not_leak_into_new_task():
    current = revised('주문금액 조건을 취소해줘',
                      '새 작업: 주문금액 100000 이상, 주문번호·주문금액을 신규.csv로 저장해줘')
    assert not numeric_filter_issues(current,deepcopy(order_draft('table-source')))


@pytest.mark.parametrize('reply', ['주문번호 열만 저장하지 마',
                                 '"주문번호와 주문금액 열만 저장해줘"라는 예시를 설명해줘'])
def test_negative_or_quoted_output_request_is_not_projected(reply):
    assert output_selection(revised(reply)) != ['주문금액']


def test_real_column_name_ending_in_particle_is_preserved():
    current = revised('성과와 결과 열만 저장해줘')
    current['resources'][0]['columns'] += ['성과','결과']
    assert output_selection(current) == ['성과','결과']


@pytest.mark.parametrize('name', ['연구과제', '실험와인'])
def test_conjunction_inside_real_single_column_name_is_not_a_list(name):
    current = revised(f'{name} 열만 저장해줘')
    current['resources'][0]['columns'].append(name)
    assert output_selection(current) == [name]


def test_ambiguous_single_name_and_list_are_not_guessed():
    current = revised('성과와결과 열만 저장해줘')
    current['resources'][0]['columns'] += ['성과','결과','성과와결과']
    assert output_selection(current) is None


def test_multiple_exports_are_not_collapsed_to_the_plural_one():
    current = revised('주문번호와 주문금액 열만 저장하고 수량만 저장해줘')
    assert output_selection(current) is None


def test_cancellation_is_included_in_model_input():
    from agent_builder.design.requirement_ledger import make_requirement_check
    current = revised('주문금액 조건을 취소해줘')
    raw, _ = changed(filters=[RULES[0]])
    captured = []
    class Transport:
        def invoke(self, messages):
            captured.append(json.loads(messages[1]['content']))
            return {'content':json.dumps(raw,ensure_ascii=False)}
    assert make_requirement_check('fixture',Transport(),'enforce')(current)['requirement_route'] == 'plan'
    assert captured[0]['cancelled_filter_fields'] == ['주문금액']


@pytest.mark.parametrize('required', [False, True])
@pytest.mark.parametrize('kind', ['range', 'cancel', 'columns'])
def test_revised_request_reaches_expected_csv(tmp_path, monkeypatch, required, kind):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    if kind == 'range':
        current = revised('상한도 추가해줘, 주문금액 130000 이하')
        raw, config = changed(filters=[*RULES, UPPER])
        expected = ['ORD-001']
    elif kind == 'cancel':
        current = revised('주문금액 조건을 취소해줘', '주문번호 오름차순으로 정렬해줘')
        raw, config = changed(filters=[RULES[0]])
        expected = ['ORD-001','ORD-003','ORD-004']
    else:
        current = revised('주문번호와 주문금액 열만 저장해줘')
        raw, config = changed()
        raw['tasks'][4]['source_message'] = 2
        expected = ['ORD-001','ORD-003']
    ledger = extract_plan(raw, current)
    current.update(requirement_required=required, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    fields, rows = _run_csv(tmp_path, current, config, COLUMNS, ROWS)
    assert fields == ['주문번호','주문금액']
    assert [r['주문번호'] for r in rows] == expected


@pytest.mark.parametrize('word,operator,expected', [
    ('이하','lte',['B','C']), ('미만','lt',['B']),
])
def test_range_boundaries_in_exported_csv(tmp_path, monkeypatch, word, operator, expected):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE','off')
    current = revised(f'상한도 추가해줘, 주문금액 130000 {word}')
    raw, config = changed(filters=[*RULES,{**UPPER,'operator':operator}])
    ledger = extract_plan(raw,current)
    current.update(requirement_required=True, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(ledger))
    rows = [dict(zip(COLUMNS,[identifier,'완료',1,value]))
            for identifier,value in [('A',99999),('B',100000),('C',130000),('D',130001)]]
    fields, actual = _run_csv(tmp_path,current,config,COLUMNS,rows)
    assert fields == ['주문번호','주문금액']
    assert [r['주문번호'] for r in actual] == expected
