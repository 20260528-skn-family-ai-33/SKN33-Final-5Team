"""Unproven descriptions must not contradict a successful typed setting edit."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.node_edit import apply_node_edit, proof_issue
from tests.design.test_node_edit_descriptions import _table


def _submit(old, label):
    draft = _table(old)
    target = draft['workflow']['nodes'][1]
    target['label'] = label
    request = (json.dumps(label, ensure_ascii=False) + ' 작업의 설정을 다음과 같이 바꿔주세요.\n'
               f'- 조건 1 (amount) 비교 기준: 숫자 {json.dumps(old)} → 숫자 1500000\n'
               '다른 작업의 설정은 유지해주세요.')
    edit = dict(request=request, node_id=target['id'], label=label, changes=[
        dict(path='execution_plan.filters[0].value', old_value=old, new_value=1500000),
        dict(path='execution_plan.filters[0].compare_field', old_value=None, new_value=None)])
    current = dict(draft=draft, status='ready', issues=[], resources=[], pending_questions=[],
                   trace=[], messages=[dict(role='user', content=request)], node_edit=edit)
    return current, apply_node_edit(current)


@pytest.mark.parametrize('label', ['금액 100만원 이상', '금액 1,000,000원 이상',
                                  '금액 1000000.0원 이상', '금액 1e6원 이상'])
def test_unproven_old_amount_in_node_label_blocks_a_misleading_success(label):
    current, result = _submit(1000000.0, label)
    before = deepcopy(current['draft'])
    assert result['edit_route'] == 'stop'
    assert '이전 숫자가 남아 있어' in result['issues'][0]
    assert '대화에서' in result['issues'][0]
    assert 'execution_plan' not in result['issues'][0]
    assert current['draft'] == before and 'draft' not in result


def test_large_numeric_value_is_not_silently_rounded_during_an_edit():
    old = 12345678901234567890123456789030
    _, result = _submit(old, f'금액 {old} 이상')
    assert result['edit_route'] == 'stop'
    assert '표시된 이전 값 또는 새 값' in result['issues'][0]


def test_unrelated_large_exponent_in_authored_label_does_not_crash_an_edit():
    current, result = _submit(1000000.0, '자료 구분 1e999999999')
    assert result['edit_route'] == 'validate', result.get('issues')
    assert proof_issue({**current, **result}) is None
