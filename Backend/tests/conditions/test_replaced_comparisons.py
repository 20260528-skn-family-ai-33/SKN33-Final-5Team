"""Issues #44/#45: later user corrections must survive audit, save and execution.

These are deterministic backend regressions, not measurements of LLM generation.
"""
import json
from copy import deepcopy

import pytest

from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.conditions.condition_readiness import canonical_written_operators, written_comparison_issues
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import state, workflow_draft
from workflow_engine.runner import run_workflow


def talk(*turns):
    return {'messages': [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': content}
                         for i, content in enumerate(turns)]}


def flt(operator, value, field='amount'):
    return {'field': field, 'operator': operator, 'value': value, 'compare_field': None, 'value_type': 'number'}


def drafted(*filters):
    return {'execution_plan': {'filters': list(filters)}}


@pytest.mark.parametrize('replacement', [
    '기존 조건 대신 amount가 30 미만인 행만 남겨줘.',
    '이전 조건 대신 amount가 30 미만인 행만 남겨줘.',
])
def test_issue44_whole_replacement_corrects_old_lower_bound(replacement):
    s = talk('amount가 30 이상인 행만 남겨줘.', '저장할까요?', replacement)
    bad = drafted(flt('gte', 30))
    assert written_comparison_issues(s, bad)
    fixed, changes = canonical_written_operators(s, bad)
    assert fixed['execution_plan']['filters'] == [flt('lt', 30)]
    assert changes and not written_comparison_issues(s, fixed)
    assert bad['execution_plan']['filters'] == [flt('gte', 30)]


@pytest.mark.parametrize('operator', ['gt', 'gte', 'lt', 'lte'])
def test_issue45_retired_threshold_is_blocked_even_with_another_operator(operator):
    s = talk('amount가 30 이상인 행만 남겨줘.', '저장할까요?', 'amount가 40 이상인 행만 남겨줘.')
    bad = drafted(flt(operator, 30))
    assert written_comparison_issues(s, bad)
    # Do not invent a numeric-value repair. The caller must rebuild the draft.
    assert canonical_written_operators(s, bad) == (None, [])
    assert not written_comparison_issues(s, drafted(flt('gte', 40)))


def test_whole_replacement_retires_both_old_bounds():
    s = talk('amount가 30 이상이고 amount가 100 이하인 행만.', '저장할까요?',
             '기존 조건 대신 amount가 50 미만인 행만 남겨줘.')
    assert not written_comparison_issues(s, drafted(flt('lt', 50)))
    for old in [flt('gte', 30), flt('gt', 30), flt('lte', 100), flt('lt', 100)]:
        assert written_comparison_issues(s, drafted(flt('lt', 50), old))


@pytest.mark.parametrize('replacement', [
    'amount가 200 이하인 행도 조건에 추가해줘.',
    '기존 상한 조건 대신 amount가 200 이하인 행까지 남겨줘.',
    '하한은 그대로 두고 amount가 200 이하인 행까지 남겨줘.',
])
def test_adding_or_updating_upper_bound_preserves_lower_bound(replacement):
    s = talk('amount가 30 이상인 행만 남겨줘.', '저장할까요?', replacement)
    good = drafted(flt('gte', 30), flt('lte', 200))
    assert not written_comparison_issues(s, good)
    assert canonical_written_operators(s, good) == (None, [])
    assert written_comparison_issues(s, drafted(flt('lt', 30), flt('lte', 200)))


def test_replacement_marker_for_another_column_does_not_clear_this_columns_lower_bound():
    s = talk('amount가 30 이상이고 price가 10 이상인 행만.', '저장할까요?',
             '기존 조건 대신 price가 5 미만인 행만 남기고 amount가 100 이하인 조건도 추가해줘.')
    good = drafted(flt('gte', 30), flt('lte', 100), flt('lt', 5, field='price'))
    assert not written_comparison_issues(s, good)
    assert canonical_written_operators(s, good) == (None, [])
    assert written_comparison_issues(s, drafted(flt('gte', 30), flt('lte', 100), flt('gt', 10, field='price')))


def test_field_name_with_punctuation_uses_the_same_subject_binding():
    s = talk('item.amount가 30 이상인 행만.', '저장할까요?',
             '기존 조건 대신 item.amount가 30 미만인 행만 남겨줘.')
    bad = drafted(flt('gte', 30, field='item.amount'))
    assert written_comparison_issues(s, bad)
    fixed, _ = canonical_written_operators(s, bad)
    assert fixed['execution_plan']['filters'] == [flt('lt', 30, field='item.amount')]


def test_reintroduced_threshold_becomes_valid_again():
    s = talk('amount가 30 이상인 행만.', '저장할까요?', 'amount가 40 이상인 행만.',
             '저장할까요?', 'amount가 30 이상인 행만 남겨줘.')
    assert not written_comparison_issues(s, drafted(flt('gte', 30)))
    assert written_comparison_issues(s, drafted(flt('gt', 40)))


@pytest.mark.parametrize('revision', [
    '예시 문장은 "기존 조건 대신 amount가 30 미만인 행만 남겨줘."야.',
    '기존 조건 대신 amount가 30 미만인 행을 남기고 싶지 않아.',
])
def test_quoted_or_negated_replacement_cannot_reverse_the_draft(revision):
    s = talk('amount가 30 이상인 행만 남겨줘.', '저장할까요?', revision)
    current = drafted(flt('gte', 30))
    assert canonical_written_operators(s, current) == (None, [])
    assert not written_comparison_issues(s, current)


def test_explicit_exclusion_still_allows_its_complement():
    s = talk('amount가 30 이상인 행만 남겨줘.', '저장할까요?',
             '기존 조건 대신 amount가 30 이상인 행은 제외해줘.')
    complement = drafted(flt('lt', 30))
    assert canonical_written_operators(s, complement) == (None, [])
    assert not written_comparison_issues(s, complement)
    assert written_comparison_issues(s, drafted(flt('gte', 30)))


def full_state(latest, operator, value, initial='amount가 30 이상인 행만 남겨 JSON으로 저장해줘.'):
    s = state(initial)
    s['messages'] += [{'role': 'assistant', 'content': '저장할까요?'}, {'role': 'user', 'content': latest}]
    d = workflow_draft(s, threshold=value, operator=operator)
    for plan in [d['execution_plan'], d['workflow']['nodes'][1]['settings']['execution_plan']]:
        plan['filters'][0]['field'] = 'amount'
    d['name'] = '금액 조건 선별'
    d['instructions'] = latest
    d['requirements'] = [{'description': '수정된 금액 조건', 'source_id': 'user', 'quote': latest}]
    s.update(draft=d, confirmed=True)
    return s


@pytest.mark.parametrize('mode', ['off', 'shadow', 'enforce'])
@pytest.mark.parametrize('latest,operator,value', [
    ('기존 조건 대신 amount가 30 미만인 행만 남겨 JSON으로 저장해줘.', 'gte', 30),
    ('amount가 40 이상인 행만 남겨 JSON으로 저장해줘.', 'gt', 30),
])
def test_stale_draft_cannot_be_saved_directly(tmp_path, monkeypatch, mode, latest, operator, value):
    monkeypatch.setenv('A4B_CONDITION_MODE', mode)
    s = full_state(latest, operator, value)
    checked = validate_node(s)
    assert checked['status'] != 'ready' and checked['issues']
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='확인·검증'):
            make_save_node(store)(s)
        assert not store.agents()
    finally:
        store.close()


@pytest.mark.parametrize('initial,latest,bad_operator,value,good_operator,good_value,expected', [
    ('amount가 30 이상인 행', '기존 조건 대신 amount가 30 미만인 행', 'gte', 30, 'lt', 30, [20]),
    ('amount 30 이상', 'amount 40 이상', 'gt', 30, 'gte', 40, [40, 45]),
])
def test_audit_validation_save_and_real_table_execution(tmp_path, initial, latest, bad_operator, value,
                                                      good_operator, good_value, expected):
    s = full_state(latest, bad_operator, value, initial)
    before = deepcopy(s['draft'])
    s.update(audit_conditions(s))
    checked = validate_node(s)
    if good_value == value:
        # #44 has an unambiguous operator correction at the same value.
        assert checked['status'] == 'ready', checked['issues']
        assert s['draft']['execution_plan']['filters'] == [flt(good_operator, good_value)]
        assert before['execution_plan']['filters'] == [flt(bad_operator, value)]
    else:
        # #45 must reject the stale value; accept a subsequently corrected draft.
        assert checked['status'] != 'ready' and checked['issues']
        s = full_state(latest, good_operator, good_value, initial)
        assert validate_node(s)['status'] == 'ready'
    store = Storage(tmp_path)
    try:
        asset = {'id': 'table', 'name': 'amount.csv', 'kind': 'table', 'columns': ['amount'], 'profile': {}}
        store.add_asset(asset, [{'amount': n} for n in [20, 30, 35, 40, 45]])
        result = make_save_node(store)(s)
        run = run_workflow(store, result['saved_agent_id'], '실행', '', file_ids=['table'])
        assert run['status'] == 'succeeded'
        rows = [{'amount': n} for n in expected]
        assert run['rows'] == rows
        assert json.loads((tmp_path / run['report_path']).read_text(encoding='utf-8-sig')) == rows
    finally:
        store.close()
