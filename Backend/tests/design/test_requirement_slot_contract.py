"""The requirement extractor proposes residual work and questions with stable keys."""
import pytest

from tests.design.test_condition_requirement_ownership import COMBINED, Extraction, filter_spec, packet, validated_state
from tests.conditions.test_condition_readiness import checked, proposal
from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check


def test_ready_condition_schema_never_asks_model_to_fill_owned_policy_and_export():
    active = Extraction(packet())
    result = make_requirement_check('fixture', active, 'enforce')(validated_state())
    assert result['requirement_route'] == 'plan'
    branches = active.schema['$defs']['RequiredTask']['properties']['spec']['oneOf']
    assert all(branch.get('$ref') not in ('#/$defs/Policy', '#/$defs/Export') for branch in branches)
    assert [t['spec']['kind'] for t in result['requirement_ledger']['tasks']] == ['policy', 'export']


def test_system_failure_after_answer_does_not_resurface_old_question():
    current = validated_state()
    current.update(pending_questions=[dict(id='old', key='duplicate.policy', text='이전 질문')],
                   draft={'questions':['이전 질문']})
    class Offline:
        def invoke(self, messages):
            raise OSError('transport unavailable')
    result = make_requirement_check('fixture', Offline(), 'enforce')(current)
    assert result['requirement_route'] == 'stop'
    assert result['pending_questions'] == [] and not (result.get('draft') or {}).get('questions')
    assert result['trace'][-1]['failure_kind'] == 'system_error'


def test_distinct_filter_candidates_cannot_be_rewritten_as_two_copies_of_confirmed_filter():
    current = validated_state(COMBINED + ' 별도로 다른 기준 결과를 준비해줘.', with_filter=True)
    other = {**filter_spec(), 'filters': [{**filter_spec()['filters'][0], 'value': 37}]}
    with pytest.raises(ValueError):
        extract_plan(packet(filter_spec(), other), current)


def test_confirmed_amount_survives_model_proposal_citing_unrelated_turn():
    current = validated_state(COMBINED, with_filter=True)
    current['messages'].append({'role':'user', 'content':'파일 이름은 99로 해줘.'})
    ledger = checked(current, proposal(current, {'filter.value':'99'}, ['filter']))
    assert ledger['slots']['filter.value']['value'] == 15
