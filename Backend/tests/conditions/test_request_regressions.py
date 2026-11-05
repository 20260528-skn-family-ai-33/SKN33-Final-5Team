import json

import pytest

from tests.conditions.test_condition_readiness import state, proposal, checked, workflow_draft


@pytest.mark.parametrize('suffix,expected', [
    ('예약은 하지 마.', 'ready'),
    ('작업 취소해줘.', 'manual_review'),
    ('예약을 취소해줘.', 'manual_review'),
])
def test_no_schedule_does_not_cancel_the_filter(suffix, expected):
    s = state('비용 100만원 초과인 행을 JSON으로 저장해줘. 빈 비용 제외, 중복 유지. ' + suffix)
    result = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
                                    'filter.value': '1000000', 'output.format': 'json'}))
    assert result['decision'] == expected


def test_existing_schedule_still_requires_cancellation_review():
    s = state('비용 100만원 초과인 행을 JSON으로 저장해줘. 예약은 하지 마.')
    s['draft'] = {'schedule': {'frequency': 'daily'}}
    result = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
                                    'filter.value': '1000000', 'output.format': 'json'}))
    assert result['decision'] == 'manual_review'


@pytest.mark.parametrize('word,operator', [('초과', 'gt'), ('이상', 'gte'), ('미만', 'lt'), ('이하', 'lte')])
def test_named_threshold_keeps_comparison_when_value_is_missing(word, operator):
    s = state(f'비용이 기준 금액을 {word}하는 행을 JSON으로 저장해줘. 기준 금액은 물어봐줘.')
    result = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': None,
                                    'filter.value': None, 'output.format': 'json'}))
    assert result['decision'] == 'clarify'
    assert result['slots']['filter.operator']['value'] == operator
    assert [q['key'] for q in result['questions']] == ['filter.value']


def test_negated_named_threshold_is_not_confirmed():
    s = state('비용이 기준 금액 이상이 아닌 행을 JSON으로 저장해줘.')
    result = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gte',
                                    'filter.value': None, 'output.format': 'json'}))
    assert result['slots']['filter.operator']['status'] != 'specified'


def test_unrequested_deduplication_is_rejected_during_draft_audit():
    from agent_builder.conditions.condition_check import audit_conditions

    s = state('CSV에서 비용이 100만원 초과인 행만 JSON으로 저장해줘.')
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
                                    'filter.value': '1000000', 'output.format': 'json'}))
    draft = workflow_draft(s)
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    plan.update(duplicate_policy='first', duplicate_field='비용')
    s.update(condition_ledger=ledger, condition_enforced=True, draft=draft)
    result = audit_conditions(s)
    assert any('요청하지 않은 중복 처리' in issue for issue in result['semantic_issues'])
    plan.update(duplicate_policy='keep', duplicate_field=None, missing_policy='exclude')
    assert not audit_conditions(s).get('semantic_issues')


def test_unspecified_missing_values_are_excluded_for_numeric_filter():
    from agent_builder.conditions.condition_check import audit_conditions
    from agent_builder.design.condition_templates import compile_conditions

    s = state('CSV에서 비용이 100만원 초과인 행만 JSON으로 저장해줘.')
    s['condition_ledger'] = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
        'filter.value': '1000000', 'output.format': 'json'}))
    s['condition_enforced'] = True
    s['draft'] = workflow_draft(s)
    plan = s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    plan.update(missing_policy='keep', duplicate_policy='keep', duplicate_field=None)
    assert any('빈 값을 제외' in issue for issue in audit_conditions(s)['semantic_issues'])
    compiled = compile_conditions(s)['draft']
    assert compiled['execution_plan']['missing_policy'] == 'exclude'


def test_threshold_answer_is_applied_after_clarification():
    s = state('비용이 기준 금액을 초과하는 행만 JSON으로 저장해줘. 기준 금액은 물어봐줘. 빈 비용 제외, 중복 모두 유지.')
    first = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': None,
                                    'filter.value': None, 'output.format': 'json'}))
    assert [q['key'] for q in first['questions']] == ['filter.value']
    s.update(condition_ledger=first, messages=s['messages'] + [
        {'role': 'assistant', 'content': first['questions'][0]['text']},
        {'role': 'user', 'content': '200만원 초과만 남겨줘. 정확히 200만원은 제외.'}])
    result = checked(s, proposal(s, {'filter.operator': 'gt', 'filter.value': '2000000'}))
    assert result['decision'] == 'ready'
    assert {key: result['slots'][key]['value'] for key in
            ('filter.operator', 'filter.value', 'missing.policy', 'duplicate.policy')} == {
                'filter.operator': 'gt', 'filter.value': 2000000,
                'missing.policy': 'exclude', 'duplicate.policy': 'keep'}


def test_named_filter_field_is_preserved_after_capability_question():
    s = state('비용이 기준 금액을 초과하는 행만 JSON으로 저장해줘. 기준 금액은 물어봐줘.')
    s['messages'] += [{'role': 'assistant', 'content': '기준 금액을 알려주세요.'},
                      {'role': 'user', 'content': '200만원 초과만 남겨줘.'}]
    result = checked(s, proposal(s, {'filter.operator': 'gt', 'filter.value': '2000000'}))
    assert result['decision'] == 'ready'
    assert result['slots']['filter.field']['value'] == '비용'


def test_no_schedule_request_can_be_saved_and_run_with_boundary_and_duplicates(tmp_path):
    from agent_builder.design.condition_templates import compile_conditions
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow

    s = state('비용 100만원 초과인 행을 JSON으로 저장해줘. 빈 비용 제외, 중복 유지. 예약은 하지 마.')
    s['resources'] = [{'id': 'data', 'name': '비용.csv', 'kind': 'table',
                       'columns': ['거래', '비용'], 'profile': {}, 'text': ''}]
    s['condition_ledger'] = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
        'filter.value': '1000000', 'output.format': 'json'}))
    assert s['condition_ledger']['decision'] == 'ready'
    s.update(compile_conditions(s), confirmed=True)
    rows = [{'거래': '경계', '비용': 1000000}, {'거래': '초과', '비용': 1000001},
            {'거래': '중복', '비용': 2000000}, {'거래': '중복', '비용': 2000000},
            {'거래': '빈값', '비용': ''}]
    store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], rows)
        identifier = store.save_agent(s)
        run = run_workflow(store, identifier, '실행', '', file_ids=['data'])
        assert run['status'] == 'succeeded'
        assert run['rows'] == rows[1:4]
        assert json.loads((tmp_path / run['report_path']).read_text(encoding='utf-8-sig')) == rows[1:4]
    finally:
        store.close()
