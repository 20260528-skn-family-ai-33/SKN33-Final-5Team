"""Amounts written with Korean numerals ground the model's threshold the same way digits do.
Live (2026-11-02, qwen3.5:4b): "비용이 백만 원 미만" -> model value 1000000 was rejected because no
number was read from the request; "100만원 미만" with the same meaning was accepted."""
import pytest
from agent_builder.conditions.condition_readiness import _amounts, explicit_policies, source_catalog
from tests.conditions.test_condition_readiness import state, proposal, checked


@pytest.mark.parametrize('text,value', [
    ('비용이 백만 원 미만', 1000000), ('오십만원 이상', 500000), ('금액이 오십만 이상', 500000), ('천오백 개 초과', 1500),
    ('이백만 원 이하', 2000000), ('3천만 원 미만', 30000000), ('1억 5천만 원 이상', 150000000), ('십만 원 이상', 100000),
    ('만 원 이하', 10000), ('삼천 원 초과', 3000), ('구십 점 이상', 90), ('백 명 미만', 100), ('2.5만 원 이상', 25000)])
def test_korean_numeral_amounts(text, value):
    assert value in _amounts(text)


@pytest.mark.parametrize('text', ['백업 파일을 만들어줘', '천천히 처리해', '만약 비용이 크면', '비용이 큰 행만', '이상한 값은 빼',
                                  '오류가 있으면 멈춰', '만족도 열', '십분 뒤에', '억지로 하지 마', '비용이 이백', '삼성 주식'])
def test_words_are_not_numbers(text):
    assert _amounts(text) == set()


def test_digit_amounts_are_unchanged():
    assert _amounts('100만원 초과') == _amounts('1,000,000원') == {1000000}
    assert _amounts('5kg 이하') == {5}


def test_korean_amount_grounds_the_threshold_and_operator():
    s = state('비용이 백만 원 미만인 행만 남겨줘. 빈 비용은 제외해. 중복은 유지해. CSV로 저장해.')
    assert explicit_policies(s, source_catalog(s)[0])['filter.operator']['value'] == 'lt'
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'lt', 'filter.value': '1000000',
                                     'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'csv'}))
    assert ledger['slots']['filter.value'] == {**ledger['slots']['filter.value'], 'status': 'specified', 'value': 1000000}
    assert ledger['decision'] == 'ready'


def test_a_different_number_is_still_rejected():
    s = state('비용이 백만 원 미만인 행만 남겨줘. 빈 비용은 제외해. 중복은 유지해. CSV로 저장해.')
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'lt', 'filter.value': '999999',
                                     'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'csv'}))
    assert ledger['decision'] == 'clarify' and ledger['slots']['filter.value']['status'] != 'specified'


@pytest.mark.parametrize('garbage', ['}', '}, ', '{200 만원}', 'kgs', ']}500000'])
def test_text_threshold_not_in_the_request_is_rejected(garbage):
    """Live (2026-11-02): these came back as filter.value and were confirmed without any check."""
    s = state('비용이 50만원 미만인 행만 남겨줘. 빈 비용은 제외해. 중복은 유지해. CSV로 저장해.')
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'lt', 'filter.value': garbage,
                                     'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'csv'}))
    assert ledger['decision'] == 'clarify' and ledger['slots']['filter.value']['status'] != 'specified'


def test_text_threshold_written_in_the_request_is_kept():
    s = state('상태가 완료인 행만 남겨줘. CSV로 저장해.')
    ledger = checked(s, proposal(s, {'filter.field': '상태', 'filter.operator': 'eq', 'filter.value': '완료', 'output.format': 'csv'}))
    assert ledger['slots']['filter.value']['status'] == 'specified' and ledger['slots']['filter.value']['value'] == '완료'
