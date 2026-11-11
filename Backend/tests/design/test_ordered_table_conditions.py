from copy import deepcopy

from agent_builder.conditions.condition_readiness import plan_issues
from agent_builder.design.requirement_ledger import extract_plan
from tests.conditions.test_condition_readiness import workflow_draft
from tests.design.test_condition_requirement_ownership import COMBINED, packet, validated_state


FILTER_FIRST = ('먼저 amount가 15 이상인 행만 골라. 그 결과에서 customer_id가 같은 행은 '
                '첫 행만 남기고 JSON으로 저장해줘. 빈 값은 오류.')


def combined_draft(state):
    draft = workflow_draft(state, threshold=15, operator='gte')
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    plan['filters'][0]['field'] = 'amount'
    plan.update(duplicate_field='customer_id', duplicate_policy='first')
    draft['execution_plan'] = deepcopy(plan)
    return draft


def test_filtered_rows_feed_deduplication_and_then_export():
    state = validated_state(FILTER_FIRST, with_filter=True)
    plan = extract_plan(packet({'kind': 'export', 'format': 'json'}), state)
    tasks = {t['spec']['kind']: t for t in plan['tasks']}
    assert tasks['filter']['id'] in tasks['policy']['depends_on']
    assert tasks['policy']['id'] in tasks['export']['depends_on']
    assert plan_issues(state['condition_ledger'], combined_draft(state), state=state)


def test_single_native_node_can_remove_duplicates_before_filtering():
    state = validated_state(COMBINED, with_filter=True)
    assert not plan_issues(state['condition_ledger'], combined_draft(state), state=state)


def test_plain_confirmation_preserves_order_but_a_changed_request_is_rechecked():
    from agent_builder.design.condition_requirements import ordered_row_steps
    state = validated_state(FILTER_FIRST, with_filter=True)
    state['messages'].append(dict(role='user', content='네.'))
    assert ordered_row_steps(state) == ('filter', 'policy')
    state['messages'][-1]['content'] = '반대로 중복을 먼저 제거해줘.'
    assert ordered_row_steps(state) == ()
