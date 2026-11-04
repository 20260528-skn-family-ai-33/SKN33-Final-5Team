"""Identifiers the model made up are not unsupported requests and never make a turn READY.

Live 2026-11-03: sessions ended with "지원 범위: delete_duplicates_by_multiple_fields" and
"지원 범위: create_data / inference_generation" although the user asked for neither.
"""
import pytest
from tests.conditions.test_condition_readiness import state, proposal, checked

TEXT = '비용이 100만원 초과인 행만 남겨줘. 빈 비용은 제외해. 중복은 유지해. JSON으로 저장해.'
FULL = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
        'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'json'}


def with_actions(s, actions, values=FULL):
    raw = proposal(s, values)
    raw['unsupported_actions'] = actions
    return checked(s, raw)


@pytest.mark.parametrize('action', ['delete_duplicates_by_multiple_fields', 'create_data', 'inference_generation', 'table.compare_rows'])
def test_invented_identifier_is_not_reported_as_unsupported(action):
    s = state(TEXT)
    ledger = with_actions(s, [action])
    assert ledger['decision'] != 'unsupported' and ledger['unsupported_actions'] == []
    assert any(action in note for note in ledger['invalid_values'])


def test_an_invented_identifier_never_lets_the_turn_be_ready():
    s = state(TEXT)
    assert with_actions(s, [])['decision'] == 'ready'
    assert with_actions(s, ['create_data'])['decision'] == 'manual_review'


def test_missing_conditions_are_still_asked():
    s = state('비용이 큰 행만 남겨줘. 빈 비용은 제외해. 중복은 유지해. JSON으로 저장해.')
    ledger = with_actions(s, ['inference_generation'], {**FULL, 'filter.operator': None, 'filter.value': None})
    assert ledger['decision'] == 'clarify'
    assert {'filter.operator', 'filter.value'} <= {q['key'] for q in ledger['questions']}


@pytest.mark.parametrize('action', ['외부 Slack 전송', '외부 이메일 발송', '월간 예약'])
def test_written_unsupported_actions_are_kept(action):
    s = state(TEXT)
    ledger = with_actions(s, [action])
    assert ledger['decision'] == 'unsupported' and ledger['unsupported_actions'] == [action]
