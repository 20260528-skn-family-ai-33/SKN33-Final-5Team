"""Exercise extraction, constrained generation, decoding, coverage, save and real rows."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from tests.conditions.test_clarification_choices import compound_state, confirmed_single, judge, PARTIAL, BASE
from tests.design.test_condition_requirement_ownership import packet
from tests.design.test_beta_feedback import native_schema
from agent_builder.conditions.condition_readiness import compound_filter_state
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_generation import constrain_generation, constrain_ordered_steps
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.operation_steps import schema_for, compile_workflow
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from workflow_engine.runner import run_workflow


def seal(s, values=None):
    for asset in s['resources']:
        asset.update(format='csv', profile={field: {'types': ['number']} for field in asset['columns'] if field != '상태'})
    l = judge(s, values or {**PARTIAL, 'output.format': ('json', 'JSON'),
                           'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')})
    assert l['decision'] == 'ready', l['questions']
    s.update(condition_ledger=l, condition_enforced=True, condition_required=True, pending_questions=[])
    # The extractor omits every predicate; production ownership must restore them.
    ledger = extract_plan(packet(dict(kind='export', format='json')), s)
    s.update(requirement_required=True, requirement_ledger=ledger)
    s['requirement_fingerprint'] = ledger_fingerprint(s)
    s['requirement_digest'] = plan_digest(ledger)
    return s


def generate(s):
    bounded = constrain_ordered_steps(schema_for(constrain_generation(native_schema(), s)), s)
    # A deterministic producer stands in for the model; every byte still passes the
    # actual ordered generation schema and decoder, rather than bypassing them.
    proposed = compile_conditions(s)['draft']
    nodes = proposed['workflow']['nodes']
    from agent_builder.design.output_selection_request import output_selection
    selected = output_selection(s)
    if selected:
        from contracts.models import AgentDraft
        from agent_builder.design.planner_constraints import native_decoder_schema
        schema = native_decoder_schema(AgentDraft.model_json_schema(), s['resources'])
        schema['$defs']['WorkflowNode']['anyOf'] = [deepcopy(schema['$defs'][variant['$ref'].split('/')[-1]]) if '$ref' in variant else variant
                                                   for variant in schema['$defs']['WorkflowNode']['anyOf']]
        bounded = constrain_ordered_steps(schema_for(constrain_generation(schema, s)), s)
        output = nodes.pop()
        parent = nodes[-1]['id']
        nodes.extend([
            dict(id='records', tool='json_transform', label='행 읽기',
                 settings=dict(operation='lookup', arguments_json=dict(path=['rows']), output_port='records'),
                 bindings=[dict(input='value', node_id=parent, output='table')]),
            dict(id='columns', tool='filter_sort', label='출력 열 선택',
                 settings=dict(operation='select_columns', arguments_json=dict(columns=selected), output_port=None),
                 bindings=[dict(input='rows', node_id='records', output='records')]),
            dict(id='output', tool='export_result', label='결과 저장', settings=output['settings'],
                 bindings=[dict(input='records', node_id='columns', output='records')]),
        ])
        proposed['tool_ids'] = list(dict.fromkeys(node['tool'] for node in nodes))
    indices = {node['id']: index for index, node in enumerate(nodes, 1)}
    steps = [dict(tool=node['tool'], label=node['label'], settings=deepcopy(node['settings']),
                  bindings=[dict(input=b['input'], step=indices[b['node_id']], output=b['output'])
                            for b in node['bindings']]) for node in nodes]
    if selected:
        from agent_builder.design.native_settings import NATIVE_SETTINGS
        for step in steps:
            if step['settings'].get('operation'):
                import json
                step['settings']['arguments_json'] = json.dumps(step['settings']['arguments_json'], ensure_ascii=False)
            else:
                step['settings'] = {key: step['settings'].get(key) for key in (*NATIVE_SETTINGS[step['tool']], 'node_ref')}
    first = steps[0]; first.pop('bindings')
    next_steps = steps[1:] if bounded['$defs']['WorkflowDefinition']['properties']['next_steps'].get('type') == 'array' else {'step_' + str(index): step for index, step in enumerate(steps[1:], 2)}
    wire = dict(first_step=first, next_steps=next_steps)
    validator = Draft202012Validator({'$ref': '#/$defs/WorkflowDefinition', '$defs': bounded['$defs']})
    validator.validate(wire)
    if len(nodes) == 3:
        for mutation in ('drop', 'relation'):
            bad = deepcopy(wire)
            plan = bad['next_steps']['step_2']['settings']['execution_plan']
            if mutation == 'drop':
                plan['filters'] = plan['filters'][:-1]
            else:
                plan['match'] = 'any' if plan['match'] == 'all' else 'all'
            assert not validator.is_valid(bad)
    proposed['workflow'] = compile_workflow(wire)
    if selected:
        from agent_builder.design.native_settings import expand_node_settings
        proposed = expand_node_settings(proposed)
    s.update(draft=proposed, confirmed=True)
    result = validate_node(s)
    assert result['status'] == 'ready', result['issues']
    s['draft'] = result['draft']
    return s


@pytest.mark.parametrize(('request_text', 'expected'), [
    ('비용이 10원 이상이고 수량이 3개 이상인 행', [(15, 4)]),
    ('비용이 10원 이상이면서 수량이 3개 이상인 행', [(15, 4)]),
    ('비용이 10원보다 적은 행 또는 수량이 3개보다 적은 행', [(15, 1), (5, 4)]),
    ('비용이 10원 이상이고 수량이 3개 미만인 행', [(15, 1)]),
])
def test_compound_extraction_generation_and_execution(tmp_path, request_text, expected):
    s = compound_state(request_text + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s = generate(seal(s))
    rows = [{'비용': a, '수량': b} for a, b in [(15, 1), (15, 4), (5, 4)]]
    with_store(s, rows, expected, tmp_path)


def with_store(s, rows, expected, path):
    store = Storage(path)
    try:
        store.add_asset(s['resources'][0], rows)
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        assert [(row['비용'], row['수량']) for row in result['rows']] == expected
    finally:
        store.close()


@pytest.mark.parametrize('numeric', [
    '비용이 10원 이상이고 수량이 3개 이상인 행',
    '비용이 10원 미만이거나 수량이 3개 미만인 행',
])
def test_explicit_text_condition_is_generated_and_enforced(tmp_path, numeric):
    s = compound_state(numeric + '. 그리고 상태가 완료인 행만 남겨줘. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('상태')
    s = generate(seal(s))
    rows = [dict(비용=a, 수량=b, 상태=status) for a, b, status in [
        (15, 4, '완료'), (15, 1, '완료'), (5, 4, '완료'), (5, 4, '대기'), (15, 4, '대기')]]
    expected = [(15, 4)] if '이상' in numeric else [(15, 1), (5, 4)]
    with_store(s, rows, expected, tmp_path)


def test_added_group_preserves_previous_conditions_in_generation():
    s = compound_state(BASE + '. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('적립금')
    seal(s)
    s['messages'].append(dict(role='user', content='할인이 5원 이상이고 적립금이 100원 이상인 조건도 추가해줘'))
    found = compound_filter_state(s)
    assert {r['field'] for r in found['rules']} == {'비용', '수량', '할인', '적립금'}
    s = generate(seal(s))
    filters = next(t['spec']['filters'] for t in s['requirement_ledger']['tasks'] if t['spec']['kind'] == 'filter')
    assert len(filters) == 4


def test_schedule_condition_does_not_reopen_numeric_filter():
    s = confirmed_single('예약 조건은 매일 오전 9시로 설정해줘')
    assert compound_filter_state(s)['pending'] is None
    l = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                   'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert 'filter.match' not in l['unresolved']


@pytest.mark.parametrize(('criteria', 'relation_reply', 'expected'), [
    ('비용이 낮고 수량이 10개보다 많은 행', None, [(3, 11)]),
    ('수량이 10개보다 크거나 같은 값이고 비용이 낮은 행', None, [(3, 11)]),
    ('수량이 10개보다 크거나 같은 값이거나 비용이 낮은 행', None, [(3, 1), (3, 11), (7, 11)]),
    ('수량이 10개 이상, 비용이 낮은 행', '모두 만족', [(3, 11)]),
])
def test_partial_conditions_across_turns_are_preserved_through_generation(tmp_path, criteria, relation_reply, expected):
    s = compound_state(criteria + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    assert compound_filter_state(s)['pending'] == 'complete'
    s['messages'].append(dict(role='user', content='비용은 5원 미만'))
    if relation_reply:
        assert compound_filter_state(s)['pending'] == 'match'
        s['messages'].append(dict(role='user', content=relation_reply))
    s = generate(seal(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('lt', '미만'),
        'filter.value': ('5', '5원'), 'output.format': ('json', 'JSON'),
        'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}))
    with_store(s, [dict(비용=3, 수량=1), dict(비용=3, 수량=11), dict(비용=7, 수량=11)], expected, tmp_path)


def test_amending_one_bound_preserves_other_bounds_and_columns(tmp_path):
    s = compound_state('수량이 3개 이상이고 비용이 10원 이상이며 비용이 20원 이하인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    values = {'filter.field': ('수량', '수량'), 'filter.operator': ('gte', '이상'), 'filter.value': ('3', '3개'),
              'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
    s = generate(seal(s, values))
    s['messages'].append(dict(role='user', content='비용 하한은 15원 이상으로 바꿔줘'))
    s = generate(seal(s, values))
    rows = [dict(비용=cost, 수량=quantity) for cost, quantity in [(12, 3), (15, 3), (20, 3), (21, 3), (16, 2)]]
    with_store(s, rows, [(15, 3), (20, 3)], tmp_path)


@pytest.mark.parametrize('clear', ['모든 숫자 조건은 취소하고', '비용과 수량 조건 취소하고'])
@pytest.mark.parametrize('stale_model_result', [False, True])
def test_numeric_cancel_then_reassert_applies_only_the_new_filter(tmp_path, clear, stale_model_result):
    s = compound_state('수량이 3개 이상이고 비용이 10원 초과이며 비용이 20원 이하인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    initial = {'filter.field': ('수량', '수량'), 'filter.operator': ('gte', '이상'), 'filter.value': ('3', '3개'),
               'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
    s = generate(seal(s, initial))
    s['messages'].append(dict(role='user', content=f'{clear} 비용은 20원보다 큰 행만 남겨줘'))
    amended = {**initial, 'filter.field': ('비용', '비용은 20원'), 'filter.operator': ('gt', '보다 큰'),
               'filter.value': ('20', '20원보다')}
    s = generate(seal(s, initial if stale_model_result else amended))
    rows = [dict(비용=cost, 수량=quantity) for cost, quantity in [(9, 3), (15, 3), (20, 3), (21, 3), (25, 2)]]
    with_store(s, rows, [(21, 3), (25, 2)], tmp_path)


@pytest.mark.parametrize(('join', 'expected'), [('이고', [(15, 2)]), ('이거나', [(15, 4), (15, 2), (25, 2)])])
def test_cancelled_incomplete_operand_leaves_a_single_short_answer_to_apply(tmp_path, join, expected):
    connective = '고' if join == '이고' else '거나'
    s = compound_state(f'할인이 5원 이상{join} 비용이 낮{connective} 수량이 적은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    waiting = compound_filter_state(s)
    assert waiting['pending'] == 'complete' and waiting['missing_fields'] == ['비용', '수량']
    s['messages'].append(dict(role='user', content='비용 조건 취소해줘'))
    waiting = compound_filter_state(s)
    assert waiting['pending'] == 'complete' and waiting['missing_fields'] == ['수량']
    s['messages'].append(dict(role='user', content='3개 미만'))
    s = generate(seal(s, {'filter.field': ('할인', '할인'), 'filter.operator': ('gte', '이상'),
        'filter.value': ('5', '5원'), 'output.format': ('json', 'JSON'),
        'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}))
    rows = [dict(비용=cost, 수량=quantity, 할인=discount) for cost, quantity, discount in
            [(15, 4, 5), (15, 2, 5), (25, 2, 1), (25, 4, 1)]]
    with_store(s, rows, expected, tmp_path)


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('join', ['이고', ','])
def test_short_answer_after_operand_cancellation_resumes_real_graph(tmp_path, structured, join):
    from langgraph.types import Command
    from agent_builder.design.graph import build_graph
    from tests.conditions.test_condition_readiness import Fake, proposal

    s = compound_state(f'할인이 5원 이상{join} 비용이 낮고 수량이 적은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    asset = s['resources'][0]
    asset.update(format='csv', profile={field: {'types': ['number']} for field in asset['columns']})
    raw = proposal(s, {'filter.field': '할인', 'filter.operator': None, 'filter.value': None,
                       'output.format': 'json', 'missing.policy': 'error', 'duplicate.policy': 'keep'})
    rows = [dict(비용=15, 수량=2, 할인=5), dict(비용=15, 수량=4, 할인=5), dict(비용=20, 수량=2, 할인=1)]
    store = Storage(tmp_path)
    store.add_asset(asset, rows)
    graph, conn = build_graph(store, conversation=compile_conditions, condition_transport=Fake([raw, raw, raw]),
                              condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}, 'recursion_limit': 40}
    try:
        assert graph.invoke(s, config)['status'] == 'needs_clarification'
        waiting = graph.invoke(Command(resume=dict(type='message', content='비용 조건 취소해줘')), config)
        assert waiting['status'] == 'needs_clarification'
        question, = waiting['pending_questions']
        assert question['key'] == 'filter.match' and '수량' in question['text'] and '비용' not in question['text']
        answer = '3개 미만'
        reply = dict(type='message', question_id=question['id'], content=answer)
        if structured:
            reply.update(content=f"{question['text']} → {answer}",
                         question_answers=[dict(question_id=question['id'], value=answer)])
        ready = graph.invoke(Command(resume=reply), config)
        if join == ',':
            assert ready['status'] == 'needs_clarification'
            relation, = ready['pending_questions']
            assert relation['options'] == ['모두 만족', '하나라도 만족']
            ready = graph.invoke(Command(resume=dict(type='message', question_id=relation['id'], content='모두 만족')), config)
        assert ready['status'] == 'ready', ready.get('issues') or ready.get('pending_questions')
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        assert saved['status'] == 'saved'
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[:1]
    finally:
        conn.close()
        store.close()


@pytest.mark.parametrize('column', ['파일명길이', '상품설명길이'])
def test_numeric_column_containing_metadata_label_is_still_a_filter(column):
    s = compound_state(f'수량이 3개 이상이고 {column}가 10 이상인 행을 JSON으로 저장해줘.')
    s['resources'][0]['columns'].append(column)
    found = compound_filter_state(s)
    assert found['pending'] is None
    assert {(rule['field'], rule['value']) for rule in found['rules']} == {('수량', 3.0), (column, 10.0)}


@pytest.mark.parametrize(('name', 'proposed'), [
    ('비용이 20원 이상', {}),
    ('"비용이 20원 이상"', {}),
    ('비용이 20원 미만', {}),
    ('수량이 20개 미만', {'filter.field': ('수량', '수량이 20개'),
                       'filter.operator': ('lt', '20개 미만'), 'filter.value': ('20', '20개')}),
])
def test_numeric_comparison_in_output_name_does_not_change_filters(tmp_path, name, proposed):
    s = generate(seal(compound_state(BASE + '. 빈 값은 오류, 중복은 유지해.')))
    s['messages'].append(dict(role='user', content=f'파일 이름을 {name}으로 바꿔줘'))
    s = generate(seal(s, {**PARTIAL, 'output.format': ('json', 'JSON'),
                         'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지'), **proposed}))
    rows = [dict(비용=cost, 수량=quantity) for cost, quantity in [(5, 3), (15, 3), (20, 3), (20, 2)]]
    with_store(s, rows, [(15, 3), (20, 3)], tmp_path)


def test_adding_a_different_boolean_group_requires_grouping_instead_of_dropping_rules():
    s = compound_state(BASE, '할인이 5원 미만이거나 적립금이 100원 미만인 조건도 추가해줘')
    s['resources'][0]['columns'].append('적립금')
    found = compound_filter_state(s)
    assert found['pending'] == 'rewrite'
    assert {rule['field'] for rule in found['rules']} == {'비용', '수량', '할인', '적립금'}
    assert {rule['field'] for rule in found['addition']['rules']} == {'비용', '수량'}


def test_natural_comparators_on_the_same_column_are_not_reduced_to_the_last_one():
    s = compound_state('비용이 10원보다 큰 행 그리고 비용이 100원보다 작은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    l = judge(s, {**PARTIAL, 'output.format': ('json', 'JSON')})
    assert l['decision'] != 'ready'
    assert l['slots']['filter.operator']['status'] == 'conflict'


def test_or_and_text_projection_does_not_discard_an_unowned_model_predicate():
    s = compound_state('비용이 10원 미만이거나 수량이 3개 미만인 행. 그리고 상태가 완료인 행만 남겨줘. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('상태')
    seal(s)
    wrong = dict(field='할인', operator='gt', value=999, compare_field=None, value_type='number')
    with pytest.raises(ValueError, match='추가 선별 조건'):
        extract_plan(packet(dict(kind='filter', filters=[wrong], match='all')), s)


def test_ambiguous_text_amendment_returns_validation_issues_instead_of_raising():
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.conditions.test_clarification_choices import filter_draft
    s = compound_state(BASE + '. 상태가 완료인 행만 남겨줘.', '상태 기준은 나중에 정할게')
    s['resources'][0]['columns'].append('상태')
    rules = compound_filter_state(s)['rules']
    assert numeric_filter_issues(s, filter_draft(s, rules, 'all'))


@pytest.mark.parametrize(('criteria', 'answer', 'structured', 'expected'), [
    ('비용은 10원 미만, 수량은 3개 미만인 행', '하나라도 만족', True, [(15, 1), (5, 4)]),
    ('비용은 10원 미만, 수량은 3개 미만인 행', '하나라도 만족', False, [(15, 1), (5, 4)]),
    ('비용은 10원 이상, 수량은 3개 이상인 행', '모두 만족', True, [(15, 4)]),
    ('비용은 10원 이상, 수량은 3개 이상인 행', '둘 다', False, [(15, 4)]),
    ('비용은 10원 이상, 수량은 3개 이상인 행', '둘 다', True, [(15, 4)]),
    ('비용은 10원 이상, 수량은 3개 미만인 행', '모두 만족', False, [(15, 1)]),
] + [
    ('비용은 10원 미만, 수량은 3개 미만인 행' if token.casefold() == 'or' else
     '비용은 10원 이상, 수량은 3개 이상인 행', token, structured,
     [(15, 1), (5, 4)] if token.casefold() == 'or' else [(15, 4)])
    for token in ('AND', 'and', 'AnD', 'OR', 'or', 'oR') for structured in (False, True)
])
def test_only_missing_relation_is_asked_and_resumed_to_execution(tmp_path, criteria, answer, structured, expected):
    from langgraph.types import Command
    from agent_builder.design.graph import build_graph
    from tests.conditions.test_condition_readiness import Fake, proposal
    s = compound_state(criteria + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    asset = s['resources'][0]
    asset.update(format='csv', profile={key: {'types': ['number']} for key in asset['columns']})
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                       'output.format': 'json', 'missing.policy': 'error', 'duplicate.policy': 'keep'})
    transport = Fake([raw])
    store = Storage(tmp_path)
    rows = [dict(비용=a, 수량=b) for a, b in [(15, 1), (15, 4), (5, 4)]]
    store.add_asset(asset, rows)
    planned = []

    def conversation(current):
        planned.append(current)
        return compile_conditions(current)

    graph, conn = build_graph(store, conversation=conversation, condition_transport=transport, condition_mode='enforce')
    cfg = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, cfg)
        assert first['status'] == 'needs_clarification'
        assert [q['key'] for q in first['pending_questions']] == ['filter.match']
        assert first['condition_ledger']['slots']['filter.value']['value'] == 10
        assert first['condition_ledger']['slots']['filter.operator']['status'] == 'specified'
        assert not planned
        q = first['pending_questions'][0]
        reply = dict(type='message', content=answer, question_id=q['id'])
        if structured:
            reply.update(content=q['text'] + ' → ' + answer,
                         question_answers=[dict(question_id=q['id'], value=answer, custom=answer not in q['options'])])
        ready = graph.invoke(Command(resume=reply), cfg)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions']
        assert transport.calls == 1
        assert len(planned) == 1
        bad = deepcopy(ready['draft'])
        bad['workflow']['nodes'][1]['settings']['execution_plan']['filters'] = bad['workflow']['nodes'][1]['settings']['execution_plan']['filters'][:1]
        with pytest.raises(ValueError, match='확정 업무 조건'):
            store.save_agent({**ready, 'draft': bad, 'confirmed': True})
        assert not store.agents()
        saved = graph.invoke(Command(resume={'type': 'confirm'}), cfg)
        assert saved['status'] == 'saved'
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded'
        assert [(row['비용'], row['수량']) for row in result['rows']] == expected
    finally:
        conn.close()
        store.close()


@pytest.mark.parametrize('answer', ['하나만', '아무거나', '둘 중 하나만', '모르겠어', 'o r', 'a n d', 'AND/OR', 'OR 아님'])
def test_uncertain_or_exclusive_answers_do_not_confirm_a_boolean_relation(answer):
    s = compound_state('비용은 10원 미만, 수량은 3개 미만인 행', answer)
    assert compound_filter_state(s)['pending'] == 'match'


def test_two_both_does_not_mean_all_three_conditions():
    s = compound_state('비용은 10원 미만, 수량은 3개 미만, 할인은 5원 미만인 행', '둘 다')
    assert compound_filter_state(s)['pending'] == 'match'


@pytest.mark.parametrize('answer', ['OR', 'or', 'AND', 'AnD'])
def test_boolean_keyword_does_not_change_an_already_confirmed_relation(answer):
    from tests.conditions.test_clarification_choices import BASE
    assert compound_filter_state(compound_state(BASE, answer))['mode'] == 'all'


def path_state(match='any', *, missing_policy='error'):
    policy_label = {'error': '오류', 'keep': '유지'}[missing_policy]
    selection = '비용이 10원 미만이거나 수량이 3개 미만인 행' if match == 'any' else '비용이 10원 이상이고 수량이 3개 이상인 행'
    s = compound_state(selection + f'을 JSON으로 저장해줘. 빈 값은 {policy_label}, 중복은 유지해.')
    values = {**PARTIAL, 'output.format': ('json', 'JSON'), 'missing.policy': (missing_policy, policy_label),
              'duplicate.policy': ('keep', '유지')}
    return generate(seal(s, values))


def with_filter_chain(s, plans):
    result=deepcopy(s)
    workflow=result['draft']['workflow'];original=workflow['nodes']
    template=next(node for node in original if node['tool']=='filter_sort')
    nodes=[deepcopy(original[0])]
    for index, plan in enumerate(plans):
        node=deepcopy(template);node['id']='select'+str(index)
        node['settings']['execution_plan']={**node['settings']['execution_plan'], **deepcopy(plan)}
        node['bindings']=[dict(input='table',node_id=nodes[-1]['id'],output='table')]
        nodes.append(node)
    output=deepcopy(original[-1]);output['bindings']=[dict(input='table',node_id=nodes[-1]['id'],output='table')]
    nodes.append(output);workflow['nodes']=nodes
    workflow['edges']=[dict(source=a['id'],target=b['id'],branch='next') for a,b in zip(nodes,nodes[1:])]
    return result


@pytest.mark.parametrize('position',['before','after'])
@pytest.mark.parametrize('operand',[0,1])
def test_or_cannot_lose_an_alternative_in_another_filter_stage(tmp_path, position, operand):
    s=path_state();plan=s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    narrower=dict(filters=[plan['filters'][operand]],match='all')
    bad=with_filter_chain(s,[narrower,plan] if position=='before' else [plan,narrower])
    assert validate_node(bad)['status']!='ready'
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError):store.save_agent(bad)
    finally:store.close()


@pytest.mark.parametrize('match',['all','any'])
def test_filter_path_equivalence_matches_exhaustive_boolean_oracle(match):
    from itertools import product
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    s=path_state(match);rules=s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters']
    # All nonempty subsets, both connectives, every chain of 1..3 stages.
    variants=[dict(filters=[rules[i] for i in subset],match=mode) for subset in [(0,),(1,),(0,1)] for mode in ('all','any')]
    def accepts(plan, truth):
        values=[truth[0 if rule==rules[0] else 1] for rule in plan['filters']]
        return all(values) if plan['match']=='all' else any(values)
    count=0
    for length in (1,2,3):
        for chain in product(variants,repeat=length):
            same=all(all(accepts(plan,truth) for plan in chain)==(all(truth) if match=='all' else any(truth))
                     for truth in product((False,True),repeat=2))
            candidate=with_filter_chain(s,chain)
            assert bool(numeric_filter_issues(candidate,candidate['draft'])) != same, (match,chain)
            count+=1
    assert count==258


@pytest.mark.parametrize(('match','shape'),[('all','repeat'),('any','repeat'),('all','redundant-or')])
@pytest.mark.parametrize('missing_policy', ['error', 'keep'])
def test_equivalent_sequential_graphs_store_and_export_exact_rows(tmp_path, match, shape, missing_policy):
    s=path_state(match, missing_policy=missing_policy);plan=s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    if shape=='split':plans=[dict(filters=[r],match='all') for r in plan['filters']]
    elif shape=='repeat':plans=[plan,plan]
    else:plans=[dict(filters=plan['filters'],match='any'),plan]
    equivalent=with_filter_chain(s,plans)
    result=validate_node(equivalent)
    assert result['status']=='ready',result['issues']
    rows=[dict(비용=15,수량=1),dict(비용=15,수량=4),dict(비용=5,수량=4)]
    missing = [dict(비용=None, 수량=1), dict(비용=None, 수량=None)] if missing_policy == 'keep' else []
    store=Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0],rows + missing)
        saved=store.save_agent(equivalent)
        actual=run_workflow(store,saved,'실행','',file_ids=[s['resources'][0]['id']])
        expected=[rows[1], *missing[-1:]] if match=='all' else [rows[0], rows[2], *missing]
        assert actual['status']=='succeeded' and actual['rows']==expected
    finally:store.close()


def test_keep_policy_cannot_discard_all_missing_alternatives_through_an_extra_stage(tmp_path):
    s = path_state(missing_policy='keep')
    plan = s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    added = dict(field='할인', operator='gt', value=5, compare_field=None, value_type='number')
    extra_plan = dict(plan, filters=[*plan['filters'], added])
    matching = dict(비용=None, 수량=None, 할인=0)
    ordinary = dict(비용=5, 수량=4, 할인=0)
    not_matching = dict(비용=15, 수량=4, 할인=0)
    invalid = with_filter_chain(s, [extra_plan, plan])
    assert validate_node(invalid)['status'] != 'ready'
    asset = s['resources'][0]
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [matching, ordinary, not_matching])
        with pytest.raises(ValueError):
            store.save_agent(invalid)
        saved = store.save_agent(s)
        result = run_workflow(store, saved, '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [matching, ordinary]
    finally:
        store.close()


@pytest.mark.parametrize('mutation',['dedup','source','raw-branch','mutate-field','aggregate'])
def test_other_row_loss_and_source_changes_cannot_hide_before_a_complete_filter(tmp_path, mutation):
    s=path_state();nodes=s['draft']['workflow']['nodes'];plan=nodes[1]['settings']['execution_plan']
    bad=with_filter_chain(s,[dict(filters=[],match='all'),plan]);nodes=bad['draft']['workflow']['nodes']
    if mutation=='dedup':nodes[1]['settings']['execution_plan'].update(duplicate_policy='first',duplicate_field='비용')
    elif mutation=='source':nodes[0]['settings']['file_ids']=['different-source']
    elif mutation=='raw-branch':
        raw=deepcopy(nodes[-1]);raw['id']='raw';raw['bindings']=[dict(input='table',node_id=nodes[0]['id'],output='table')]
        nodes.append(raw);bad['draft']['workflow']['edges'].append(dict(source=nodes[0]['id'],target='raw',branch='next'))
    elif mutation=='mutate-field':
        nodes[1]['tool']='calculate_columns';nodes[1]['settings'].update(execution_plan=None,table_operation='compute',output_column='비용',calculation_formula='비용 * 0')
    else:
        nodes[1]['tool']='aggregate_table';nodes[1]['settings'].update(execution_plan=None,fields=['수량'],aggregation='sum',sum_field='비용')
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    assert numeric_filter_issues(bad,bad['draft'])
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError):store.save_agent(bad)
    finally:store.close()


def test_requested_dedup_cannot_select_representatives_before_the_full_or():
    from agent_builder.design.filter_path_proof import filter_path_issues
    s=path_state();plan=s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    # The policy is authorized, but performing it before selection can retain a
    # nonmatching representative and discard the matching row of the same key.
    for key,value in [('duplicate.policy','first'),('duplicate.field','비용')]:
        s['condition_ledger']['slots'][key]=dict(status='specified',value=value)
    plans=[dict(filters=[],match='all',duplicate_policy='first',duplicate_field='비용'),
           dict(plan,duplicate_policy='first',duplicate_field='비용')]
    wrong=with_filter_chain(s,plans)
    assert filter_path_issues(wrong,wrong['draft'],[plan])
    right=with_filter_chain(s,[dict(plan,duplicate_policy='keep',duplicate_field=None),
                              dict(filters=[],match='all',duplicate_policy='first',duplicate_field='비용')])
    assert not filter_path_issues(right,right['draft'],[plan])
