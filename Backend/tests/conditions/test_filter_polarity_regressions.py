"""A range mentioned in a prohibition or an example must not reverse executed rows."""
import pytest

from agent_builder.conditions.condition_readiness import _grounded_enum
from tests.conditions.test_condition_readiness import checked, proposal
from tests.conditions.test_filter_revision_regressions import audited, conversation, result_rows


ROWS = [{'amount': amount} for amount in (20, 30, 40)]


@pytest.mark.parametrize('subject', ['', 'amount가 '])
@pytest.mark.parametrize(('word', 'operator', 'expected'), [
    ('이상', 'lt', ROWS[:1]),
    ('초과', 'lte', ROWS[:2]),
    ('이하', 'gt', ROWS[-1:]),
    ('미만', 'gte', ROWS[1:]),
])
def test_negated_comparison_preserves_draft_and_executed_rows(subject, word, operator, expected):
    # Issue #42: audit must not turn a correct < 30 draft into >= 30.
    session = conversation(f'{subject}30 {word}이 아닌 행을 남겨줘.')
    assert result_rows(session, ROWS, [('amount', operator, 30)]) == expected


@pytest.mark.parametrize(('word', 'operator'), [
    ('이상', 'gte'), ('초과', 'gt'), ('이하', 'lte'), ('미만', 'lt'),
])
@pytest.mark.parametrize('negation', ['이 아닌', ' 아닌', '이지 않은'])
def test_negated_comparison_cannot_confirm_the_named_operator(word, operator, negation):
    session = conversation(f'amount가 30 {word}{negation} 행을 남겨줘.')
    ledger = checked(session, proposal(session, {
        'filter.field': 'amount', 'filter.operator': operator, 'filter.value': '30',
    }))
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['filter.operator']['value'] is None
    assert any(q['key'] == 'filter.operator' for q in ledger['questions'])


@pytest.mark.parametrize('quote', [
    'amount가 30 이상이 아닌 행을 남겨줘.',
    'amount가 30 이상은 아니야.',
    'amount가 30 이상이지 않은 행을 남겨줘.',
])
def test_negated_quote_does_not_ground_either_comparison(quote):
    # Ask for the intended comparison rather than inferring a complement.
    assert not _grounded_enum('filter.operator', 'gte', quote)
    assert not _grounded_enum('filter.operator', 'lt', quote)


def test_negated_comparison_can_be_confirmed_and_executed():
    session = conversation('amount가 30 이상이 아닌 행을 남겨줘.')
    first = checked(session, proposal(session, {
        'filter.field': 'amount', 'filter.operator': 'lt', 'filter.value': '30',
    }))
    assert first['decision'] == 'clarify'
    question = next(q for q in first['questions'] if q['key'] == 'filter.operator')
    session.update(condition_ledger=first, condition_enforced=True, pending_questions=first['questions'],
                   condition_answers=[{'turn': 2, 'question_ids': [question['id']]}])
    session['messages'].extend([{'role': 'assistant', 'content': question['text']},
                                {'role': 'user', 'content': '미만'}])
    session['condition_ledger'] = checked(session, {'operations': [], 'conditions': [], 'unsupported_actions': []})
    assert session['condition_ledger']['decision'] == 'ready'
    assert result_rows(session, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]
    session['pending_questions'] = []
    session['messages'].extend([{'role': 'assistant', 'content': '30 미만인 행만 남기겠습니다.'},
                                {'role': 'user', 'content': 'JSON으로 저장해줘.'}])
    session['condition_ledger'] = checked(session, {'operations': [], 'conditions': [], 'unsupported_actions': []})
    assert session['condition_ledger']['decision'] == 'ready'
    assert result_rows(session, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]


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
