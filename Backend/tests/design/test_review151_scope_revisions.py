"""Review inputs: separate artifacts survive a format reply; cancelled work stays cancelled."""
import json
from copy import deepcopy

import pytest

from agent_builder.design.condition_requirements import multiple_outputs, project_owned
from agent_builder.design.processing_guard import validate_processing_presence
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.design.test_feedback_requirement_extraction import state


@pytest.mark.parametrize('reply', [
    '정렬은 건너뛰고 원본을 CSV로 저장해줘.',
    '정렬은 필요 없어. 원본을 CSV로 저장해줘.',
    '정렬은 빼고 원본을 CSV로 저장해줘.',
    '정렬할 필요가 없어. 원본을 CSV로 저장해줘.',
])
@pytest.mark.parametrize('ack', [False, True])
def test_sort_cancellation_completes_extraction_once(reply, ack):
    current = state()
    current['resources'][0]['columns'] = ['amount']
    current['messages'] = [dict(role='user', content='첨부 표를 amount 오름차순으로 정렬해줘.'),
                           dict(role='user', content=reply)]
    if ack:
        current['messages'].append(dict(role='user', content='네.'))
    raw = dict(tasks=[
        dict(source_message=1, description='원본 읽기', spec=dict(kind='read', file_ids=['table-source'])),
        dict(source_message=1, description='CSV 저장', spec=dict(kind='export', format='csv')),
    ], unresolved=[])

    class Transport:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            return dict(content=json.dumps(raw))

    transport = Transport()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 1
    assert result['requirement_route'] == 'plan', result
    assert [task['spec']['kind'] for task in result['requirement_ledger']['tasks']] == ['read', 'export']


def test_sort_cancellation_keeps_an_independent_computation():
    current = state()
    current['messages'] = [dict(role='user', content='주문금액을 계산하고 정렬해줘.'),
                           dict(role='user', content='정렬은 필요 없어. 원본을 CSV로 저장해줘.')]
    raw = dict(tasks=[dict(spec=dict(kind='read', file_ids=['table-source'])),
                      dict(spec=dict(kind='export', format='csv'))], unresolved=[])
    with pytest.raises(ValueError, match='표 처리'):
        validate_processing_presence(raw, current)


@pytest.mark.parametrize('ack', [False, True])
def test_format_answer_does_not_replace_original_json_with_csv(ack):
    current = state()
    current['messages'] = [dict(role='user', content='원본은 JSON으로 저장해줘. customer_id 중복은 첫 행만 남기고, 그 결과를 별도 파일로 저장해줘. 빈 값은 오류.'),
                           dict(role='assistant', content='중복 제거 결과는 어떤 파일 형식으로 저장할까요?'),
                           dict(role='user', content='CSV로 저장해줘.')]
    if ack:
        current['messages'].append(dict(role='user', content='네.'))
    assert multiple_outputs(current)
    candidates = [dict(source_message=0, description='원본 JSON 저장', spec=dict(kind='export', format='json')),
                  dict(source_message=1, description='중복 제거 결과 CSV 저장', spec=dict(kind='export', format='csv'))]
    expected = dict(export=deepcopy(candidates[-1]))
    actual = project_owned(candidates, expected, current)
    assert [task['spec']['format'] for task in actual] == ['json', 'csv']


@pytest.mark.parametrize('both', [False, True])
def test_confirmed_output_answer_preserves_schema_and_blocks_incomplete_download(both):
    from agent_builder.design.requirement_coverage import coverage_report
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    from tests.conditions.test_condition_readiness import checked, proposal
    from tests.design.test_condition_requirement_ownership import validated_state, packet, Extraction
    from tests.design.test_feedback_service_scenarios import order_draft
    request = ('원본은 JSON으로 저장해줘. customer_id 중복은 첫 행만 남기고, '
               '그 결과를 별도 파일로 저장해줘. 빈 값은 오류.')
    current = validated_state(request)
    current['messages'] += [dict(role='assistant', content='중복 제거 결과는 어떤 파일 형식으로 저장할까요?'),
                            dict(role='user', content='CSV로 저장해줘.')]
    ledger = checked(current, proposal(current, {'output.format':'csv'}, ['deduplicate']))
    assert ledger['decision'] == 'ready', ledger
    current['condition_ledger'] = ledger
    specs = [dict(kind='read', file_ids=['sales']), dict(kind='export', format='csv')]
    if both:
        specs.insert(1, dict(kind='export', format='json'))
    raw = packet(*specs)

    class Model(Extraction):
        def invoke(self, messages):
            mapping = self.schema['$defs']['RequiredTask']['properties']['spec']['discriminator']['mapping']
            assert 'export' in mapping
            return super().invoke(messages)

    result = make_requirement_check('fixture', Model(raw), 'enforce')(current)
    assert result['requirement_route'] == 'plan', result
    plan = result['requirement_ledger']
    assert [t['spec']['format'] for t in plan['tasks'] if t['spec']['kind'] == 'export'] == (
        ['json', 'csv'] if both else ['csv'])
    current.update(requirement_required=True, requirement_ledger=plan,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(plan))
    assert any('복수 결과 파일' in issue for issue in coverage_report(current, order_draft('sales'))['issues'])


SEPARATE = '원본은 JSON으로 저장해줘. customer_id 중복은 첫 행만 남기고, 그 결과를 별도 파일로 저장해줘. 빈 값은 오류.'

@pytest.mark.parametrize('messages, expected', [
    (['표를 JSON으로 저장해줘.', 'CSV로 저장해줘.'], False),
    (['결과를 CSV와 JSON으로 저장해줘.', 'CSV로 저장해줘.'], False),
    ([SEPARATE, 'CSV로 저장해줘.'], True),
    ([SEPARATE, 'CSV로 저장해줘.', '네.'], True),
    ([SEPARATE, '정렬은 취소해줘. CSV로 저장해줘.'], True),
    ([SEPARATE, '중복 제거는 취소해줘. CSV로 저장해줘.'], True),
    ([SEPARATE, '파일 저장은 취소해줘.', 'CSV로 저장해줘.'], False),
    ([SEPARATE, 'CSV 하나만 저장해줘.', 'CSV로 저장해줘.'], False),
    ([SEPARATE, 'JSON 저장은 취소해줘.', 'CSV로 저장해줘.'], False),
    ([SEPARATE, '원본은 취소하고 CSV 하나만 저장해줘.', 'CSV로 저장해줘.'], False),
    ([SEPARATE, 'PDF 저장은 취소해줘.', 'CSV로 저장해줘.'], True),
    (['파일 저장은 취소해줘. 원본은 JSON으로 저장해줘. 결과는 별도 파일로 저장해줘.', 'CSV로 저장해줘.'], True),
    ([SEPARATE, '파일 저장은 취소해줘. CSV로 저장해줘.'], False),
    (['원본은 JSON으로 저장해줘. 결과는 별도 파일로 저장해줘. 파일 저장은 취소해줘. CSV로 저장해줘.'], False),
    (['비용이 10 이상, 수량이 3 이상인 행을 JSON으로 저장해줘.', '모두 만족'], False),
    (['비용이 10 이상, 수량이 3 이상인 행을 JSON으로 저장해줘.', '기존 조건 모두 유지'], False),
])
def test_scope_interactions(messages, expected):
    current = state()
    current['messages'] = [dict(role='user', content=text) for text in messages]
    actual = multiple_outputs(current)
    assert actual is expected, (messages, explicit_export_intent(current), actual)


def test_structured_control_answer_with_assistant_index_is_not_output_scope():
    current = state()
    current['messages'] = [dict(role='user', content='비용과 수량 조건으로 JSON으로 저장해줘.'),
        dict(role='assistant', content='둘 다 만족할까요?'),
        dict(role='user', content='조건 둘 다 / 기존 조건 모두 유지 → 모두 만족')]
    current['condition_answers'] = [dict(turn=2, answers=[dict(key='filter.match', answer='모두 만족')])]
    assert not multiple_outputs(current)


def test_unrelated_processing_cancel_preserves_json_export_projection():
    from agent_builder.design.condition_requirements import project_owned
    current = state()
    current['messages'] = [dict(role='user', content=SEPARATE),
        dict(role='assistant', content='별도 결과는 어떤 형식으로 저장할까요?'),
        dict(role='user', content='정렬은 취소해줘. CSV로 저장해줘.')]
    candidates = [dict(source_message=0, description='원본 JSON', spec=dict(kind='export', format='json')),
                  dict(source_message=1, description='결과 CSV', spec=dict(kind='export', format='csv'))]
    result = project_owned(candidates, dict(export=candidates[-1]), current)
    assert [task['spec']['format'] for task in result] == ['json', 'csv']


def test_cancelled_scope_does_not_block_a_valid_new_single_csv():
    from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
    from agent_builder.design.requirement_coverage import coverage_report
    from tests.design.test_builder import draft
    from tests.workflow_engine.test_workflow import node, bind, edge
    current = state()
    current['messages'] = [dict(role='user', content='원본은 JSON으로 저장해줘. 그 결과를 별도 파일로 저장해줘.'),
        dict(role='user', content='파일 저장은 취소해줘.'),
        dict(role='user', content='CSV로 저장해줘.')]
    raw = dict(tasks=[dict(source_message=2, description='읽기', spec=dict(kind='read', file_ids=['table-source'])),
                      dict(source_message=2, description='저장', spec=dict(kind='export', format='csv'))], unresolved=[])
    plan = extract_plan(raw, current)
    current.update(requirement_required=True, requirement_ledger=plan,
        requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(plan))
    candidate = draft()
    candidate['workflow'] = dict(entry='read', nodes=[node('read', 'read_table', file_ids=['table-source']),
        node('out', 'write_result', [bind('table', 'read', 'table')], format='csv')], edges=[edge('read', 'out')])
    candidate['tool_ids'] = ['read_table', 'write_result']
    issues = coverage_report(current, candidate)['issues']
    assert not issues, issues


@pytest.mark.parametrize('reply', ['정렬할 필요가 없나요?', '정렬은 필요 없어도 되나요?'])
def test_question_about_sort_necessity_does_not_cancel_confirmed_sort(reply):
    import json
    from agent_builder.design.requirement_ledger import make_requirement_check
    current = state()
    current['resources'][0]['columns'] = ['amount']
    current['messages'] = [dict(role='user', content='amount 오름차순으로 정렬하고 CSV로 저장해줘.'),
                           dict(role='user', content=reply)]
    raw = dict(tasks=[dict(source_message=0, description='읽기', spec=dict(kind='read', file_ids=['table-source'])),
                      dict(source_message=0, description='저장', spec=dict(kind='export', format='csv'))], unresolved=[])
    class Transport:
        def invoke(self, messages):
            return dict(content=json.dumps(raw))
    result = make_requirement_check('fixture', Transport(), 'enforce')(current)
    assert result['requirement_route'] != 'plan', result


def test_same_turn_single_file_replaces_the_previous_format():
    from agent_builder.design.export_intent import explicit_export_intent
    from agent_builder.design.requirement_ledger import extract_plan
    current = state()
    current['messages'] = [dict(role='user', content='표를 JSON으로 저장해줘. CSV 하나만 저장해줘.')]
    assert explicit_export_intent(current)['formats'] == {'csv'}
    raw = dict(tasks=[dict(source_message=0, description='읽기', spec=dict(kind='read', file_ids=['table-source'])),
                      dict(source_message=0, description='저장', spec=dict(kind='export', format='csv'))], unresolved=[])
    assert extract_plan(raw, current)['tasks'][-1]['spec']['format'] == 'csv'
