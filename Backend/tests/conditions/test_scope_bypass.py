"""Comparison/summary words alone must not switch off the grounded single-table checker.

Live 2026-11-03 (qwen3.5:4b, B-6b x01~x10): single-table filters that merely mentioned 반품·환불·
정정·비교·요약 skipped this checker; the full planner failed all 9 and confirmed 3 with wrong
missing/duplicate policies (local) or failed validation (RunPod).
"""
import pytest
from agent_builder.conditions.condition_check import make_condition_check
from tests.conditions.test_condition_readiness import state

TAIL = ' 빈 비용은 제외해. 중복 거래ID는 마지막 행만 남겨. 정렬은 하지 마. JSON으로 저장해.'


class Recorder:
    def __init__(self): self.calls = 0
    def invoke(self, *a):
        self.calls += 1
        raise RuntimeError('recorded')  # the checker turns this into system_error, never a bypass


def run(text, assessment=None):
    s = state(text)
    if assessment: s['capability_assessment'] = assessment
    recorder = Recorder()
    return make_condition_check(transport=recorder, mode='enforce')(s), recorder


@pytest.mark.parametrize('text', [
    '반품 건 정리를 위해 비용이 100만원 초과인 행만 남겨줘.' + TAIL,
    '환불 처리용으로 비용이 50만원 이상인 행만 남겨줘.' + TAIL,
    '정정 내역 확인용으로 비용이 200만원 미만인 행만 남겨줘.' + TAIL,
    '지난달과 비교할 자료라서 비용이 100만원 이하인 행만 남겨줘.' + TAIL,
    '반품 건 중에서 비용이 큰 행만 남겨줘.' + TAIL,
    '환불 비용이 대략 100만원 넘는 행만 남겨줘.' + TAIL,
    '반품 확인용으로 비용 100만원 미만인 행은 빼고 나머지만 남겨줘.' + TAIL,
    '비교용으로 쓸 거니까 비용이 30만원 미만인 행만 남겨줘.' + TAIL,
    '반품 상태인 행은 빼고 비용이 100만원 초과인 행만 CSV로 저장해줘.',
    '환불 금액이 5만원 이상인 행만 남겨줘.',
    '정정된 거래는 제외하고 50만원 초과만 남겨.',
    '금액을 비교해서 100만원 초과만 골라줘.',
])
def test_single_table_selection_with_scope_words_stays_grounded(text):
    result, recorder = run(text)
    assert recorder.calls == 1, '조건 확인 모델을 거치지 않고 우회됨'
    assert result['condition_ledger'] is not None


@pytest.mark.parametrize('text', [
    '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해줘. 반품을 뺀 매출로 보고 정정 내역도 반영해줘. '
    '지점별 증감과 매장·온라인 중 어디서 변화가 컸는지 정리해서 회의용 표와 짧은 요약으로 만들어줘. '
    '매출이 줄어든 이유는 자료에 없는 걸 지어내지 말아줘.',
    '이번 달과 지난달의 실적 차이를 비교해줘',
    '환불을 반영한 매출 요약을 만들어줘',
    '매장별 증가율을 계산해줘',
])
def test_comparison_and_summary_work_is_still_delegated(text):
    result, recorder = run(text)
    assert recorder.calls == 0
    assert result['condition_route'] == 'conversation' and result['condition_ledger'] is None
    assert 'outside_scope' in result['trace'][-1]['detail']


def test_extended_tool_selection_still_delegates_a_request_with_a_threshold():
    text = '지점별 매출 합계를 구하고 합계가 100만원 이상인 지점만 비교해줘.'
    assessment = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'aggregate_table', 'filter_sort', 'write_result']}
    result, recorder = run(text, assessment)
    assert recorder.calls == 0 and result['condition_ledger'] is None
