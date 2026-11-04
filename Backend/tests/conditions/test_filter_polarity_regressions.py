"""A range mentioned in a prohibition or an example must not reverse executed rows."""
import pytest

from tests.conditions.test_filter_revision_regressions import audited, conversation, result_rows


ROWS = [{'amount': amount} for amount in (20, 30, 40)]


def test_do_not_keep_a_range_excludes_its_boundary():
    session = conversation('amount가 30 이상인 행을 남기면 안 돼. 나머지 행만 JSON으로 저장해줘.')
    assert result_rows(session, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]
    assert audited(session, ROWS, [('amount', 'gte', 30)])['issues']


@pytest.mark.parametrize('prompt', [
    'amount가 30 이상인 행을 남기는 건 곤란해. 나머지를 저장해줘.',
    'amount가 30 이상인 행을 남겨 두면 안 돼. 나머지를 저장해줘.',
    'amount가 30 이상인 행을 남겨 주면 안 돼. 나머지를 저장해줘.',
    'amount가 30 이상인 행을 남겨 두지 말아줘. 나머지를 저장해줘.',
    'amount가 30 이상인 행을 남기고 싶지 않아. 나머지를 저장해줘.',
    'amount가 30 이상인 행을 남겨서 안 돼. 나머지를 저장해줘.',
    'amount가 30 이상인 행은 포함 여부를 나중에 정할게.',
    '메모의 예시 문장은 \'amount가 30 이상인 행만 남겨줘\'야. 실제로는 amount < 30만 JSON으로 저장해줘.',
])
def test_ambiguous_or_quoted_positive_action_does_not_overwrite_filter(prompt):
    assert result_rows(conversation(prompt), ROWS, [('amount', 'lt', 30)]) == ROWS[:1]


def test_only_unquoted_instruction_can_repair_an_operator():
    session = conversation('예시 문구는 “amount가 30 이상인 행만 남겨줘”야. 실제로는 amount가 30 초과인 행만 남겨줘.')
    assert result_rows(session, ROWS, [('amount', 'gte', 30)]) == ROWS[-1:]
