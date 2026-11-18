"""Keeping all input data must not reopen the confirmed output contract."""
import json

import pytest

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.conditions.test_condition_readiness import checked, proposal, state


REQUEST = ('실행할 때 연결하는 CSV에서 비용이 100만원 초과인 행만 JSON으로 저장해줘. '
           '빈 비용은 제외하고 중복 행은 모두 유지해.')


@pytest.mark.parametrize('user_request', [
    REQUEST,
    '중복 행은 모두 유지하고 JSON으로 저장해줘.',
    '모든 열을 모두 보존해서 CSV로 저장해줘.',
    '원본의 모든 행과 열을 모두 그대로 유지해서 JSON으로 저장해줘.',
    '빈 값은 제외하고 중복은 모두 유지해. 결과는 JSON으로 저장해줘.',
    '각 열의 값을 모두 보존한 표를 JSON으로 저장해줘.',
    '레코드들은 빠짐없이 모두 유지하고 JSON으로 저장해줘.',
])
def test_retaining_all_data_has_one_output(user_request):
    assert not multiple_outputs(state(user_request))


@pytest.mark.parametrize('user_request', [
    '결과를 CSV와 JSON 두 파일로 각각 저장해줘.',
    '동일한 결과를 JSON 파일 두 개로 각각 저장해줘.',
    '원본과 선별 결과를 모두 JSON으로 저장해줘.',
    '결과를 각각 CSV 파일로 저장해줘.',
    '중복 행은 모두 유지하고 결과를 CSV와 JSON으로 각각 저장해줘.',
    '열은 모두 보존하고 결과 두 개를 따로 저장해줘.',
    '원본 행은 모두 유지하고 요약 결과는 별도 파일로 저장해줘.',
    '결과를 모두 저장해줘.',
])
def test_separate_outputs_survive_data_retention_clauses(user_request):
    assert multiple_outputs(state(user_request))


def test_confirmed_single_export_stays_owned_after_keep_all_rows_request():
    current = state(REQUEST)
    values = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
              'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'json'}
    ledger = checked(current, proposal(current, values, ['filter']))
    assert ledger['decision'] == 'ready'
    current.update(condition_enforced=True, condition_required=True, condition_ledger=ledger)

    class RemainingWork:
        def invoke(self, messages):
            mapping = self.schema['$defs']['RequiredTask']['properties']['spec']['discriminator']['mapping']
            assert 'export' not in mapping
            return {'content': json.dumps({'tasks': [], 'unresolved': []})}

    result = make_requirement_check('fixture', RemainingWork(), 'enforce')(current)
    assert result['requirement_route'] == 'plan'
    specs = [task['spec'] for task in result['requirement_ledger']['tasks']]
    assert specs.count({'kind': 'export', 'format': 'json'}) == 1
    assert next(spec for spec in specs if spec['kind'] == 'policy')['duplicate_policy'] == 'keep'
    assert next(spec for spec in specs if spec['kind'] == 'filter')['filters'][0]['value'] == 1000000
