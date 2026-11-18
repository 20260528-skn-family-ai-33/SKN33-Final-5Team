"""Adversarial transitions: preserve meaning, then verify decoded graphs and rows."""
from copy import deepcopy
from itertools import permutations
import pytest
from langgraph.types import Command
from agent_builder.conditions.condition_readiness import compound_filter_state
from agent_builder.design.text_filter_contract import text_filter_state
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.conditions.test_clarification_choices import BASE, compound_state, judge, PARTIAL
from tests.conditions.test_condition_readiness import Fake, proposal
from tests.design.test_compound_filter_pipeline import seal, generate
from workflow_engine.runner import run_workflow

REQUEST = BASE + '. 빈 값은 오류, 중복은 유지해.'
VALUES = {**PARTIAL, 'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
ROWS = [dict(비용=15, 수량=4, 상태='완료'), dict(비용=15, 수량=4, 상태='대기'), dict(비용=5, 수량=4, 상태='완료')]


def current(*turns):
    s = compound_state(REQUEST + (' ' + turns[0] if turns else ''))
    s['resources'][0]['columns'] += ['상태', '적립금']
    for turn in turns[1:]: s['messages'].append(dict(role='user', content=turn))
    return s


def run_rows(s, path):
    s = generate(seal(s)); store = Storage(path)
    try:
        store.add_asset(s['resources'][0], ROWS)
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        return result['rows']
    finally: store.close()


@pytest.mark.parametrize(('turns', 'expected'), [
    (['상태가 완료인 행만 남기지 말고 상태와 관계없이 저장해줘.'], ROWS[:2]),
    (["상태가 '완료'인 행만 남기지 마. 상태와 상관없이 저장해줘."], ROWS[:2]),
    (['상태와 관계없이 저장해줘. 상태가 완료인 행만 남기지 마.'], ROWS[:2]),
    (['상태가 완료인 행만 남겨줘.', '상태가 대기인 행만 남겨줘.'], [ROWS[1]]),
    (['상태가 완료인 행만 남겨줘.', '상태 조건 취소해줘.'], ROWS[:2]),
    (['상태가 완료인 행만 남겨줘.', '상태 조건은 취소하면 안 돼.'], [ROWS[0]]),
    (['상태가 완료인 행만 남겨줘.', '상태 조건 삭제해도 될까?', '상태 조건은 삭제할 필요 없어.'], [ROWS[0]]),
    (['상태가 완료인 행만 남겨줘.', '상태 조건 취소해줘.', '상태가 대기인 행만 남겨줘.'], [ROWS[1]]),
    (['상태 조건 취소해줘. 상태가 완료인 행만 남겨줘.'], [ROWS[0]]),
    (['상태가 완료인 행만 남겨줘. 상태 조건 취소해줘.'], ROWS[:2]),
    (['상태가 완료인 행은 제외해줘.'], [ROWS[1]]),
    (["상태가 '완료'인 행만 남겨줘."], [ROWS[0]]),
    (['상태가 완료인 행만 남길까?', '상태가 대기인 행만 남겨줘.'], [ROWS[1]]),
])
def test_text_transitions_apply_only_the_final_explicit_intent(tmp_path, turns, expected):
    assert run_rows(current(*turns), tmp_path) == expected


@pytest.mark.parametrize(('instruction', 'field', 'operator', 'value', 'cells', 'expected'), [
    ('지역이 서울인 행만 남긴 뒤 JSON으로 저장해줘.', '지역', 'eq', '서울',
     ['서울', '서울특별시', '부산'], ['서울']),
    ('상품명은 사과가 들어 있는 행만 남겨줘. JSON으로 저장해줘.', '상품명', 'contains', '사과',
     ['사과', '청사과', '사과즙', '포도'], ['사과', '청사과', '사과즙']),
    ("상품명에 '사과'가 들어 있는 행만 남긴 뒤 JSON으로 저장해줘.", '상품명', 'contains', '사과',
     ['사과', '청사과', '사과즙', '포도'], ['사과', '청사과', '사과즙']),
])
def test_complete_text_selection_proceeds_without_a_question_and_exports_expected_rows(
        tmp_path, instruction, field, operator, value, cells, expected):
    s = compound_state(instruction + ' 빈 값은 오류, 중복은 유지해.')
    asset = s['resources'][0]
    asset.update(columns=[field], format='csv', profile={field: {'types': ['string']}})
    raw = proposal(s, {'filter.field': field, 'filter.operator': operator, 'filter.value': value,
                       'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    store = Storage(tmp_path)
    store.add_asset(asset, [{field: cell} for cell in cells])
    graph, connection = build_graph(store, conversation=compile_conditions,
                                    condition_transport=Fake([raw]), condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        ready = graph.invoke(s, config)
        assert ready['status'] == 'ready', ready['pending_questions']
        assert not ready['pending_questions']
        assert ready['draft']['execution_plan']['filters'][0]['operator'] == operator
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded'
        assert result['rows'] == [{field: cell} for cell in expected]
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('reply', ['사과', '사과만 남겨줘'])
def test_short_answer_cannot_turn_unconfirmed_containment_into_equality(reply):
    s = current('상품명에 사과가 들어 있는 행만 남길까?')
    s['resources'][0]['columns'].append('상품명')
    assert text_filter_state(s)['issues']

    s['messages'].append(dict(role='user', content=reply))
    s['condition_answers'] = [dict(turn=1, candidate_keys=['filter.match'])]
    unresolved = text_filter_state(s)
    assert unresolved['issues'] and not unresolved['predicates']

    s['messages'].append(dict(role='user', content='상품명에 사과가 들어 있는 행만 남겨줘.'))
    resolved = text_filter_state(s)
    assert not resolved['issues']
    assert [(rule['operator'], rule['value']) for _, rule in resolved['predicates']] == [('contains', '사과')]


@pytest.mark.parametrize('instruction', ['상태가 완료인 행만 남기지 마.', '상태가 완료인 행만 남겨주지 마.', '상태가 완료인 행만 남겨야 할까?', '상태가 완료인 행만 제외하지 마.'])
def test_ambiguous_negation_or_question_cannot_be_saved(tmp_path, instruction):
    s = current(instruction)
    assert text_filter_state(s)['issues'] and not text_filter_state(s)['predicates']
    ledger = judge(s, VALUES)
    assert ledger['decision'] == 'clarify'
    assert any(q['key'] == 'filter.match' and '상태' in q['text'] for q in ledger['questions'])
    good = current(); seal(good)
    stale = {**s, 'condition_ledger': good['condition_ledger'], 'condition_enforced': True, 'draft': compile_conditions(good)['draft'], 'confirmed': True}
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='상태'): store.save_agent(stale)
    finally: store.close()


@pytest.mark.parametrize('follow_up', ['결과에는 상태도 포함해줘', '상태 기준으로 정렬해줘', '출력 파일 이름은 상태 보고서로 바꿔줘', "출력 파일 이름은 '상태 조건 취소'로 해줘", "출력 파일 이름은 상태조건취소로 해줘", "설명에는 '상태가 대기인 행만 남겨줘'라고 적어줘"])
def test_output_mentions_preserve_the_unchanged_text_predicate(tmp_path, follow_up):
    s = current('상태가 완료인 행만 남겨줘.'); seal(s)
    s['messages'].append(dict(role='user', content=follow_up))
    assert not text_filter_state(s)['issues']
    assert text_filter_state(s)['predicates'][0][1]['value'] == '완료'
    assert run_rows(s, tmp_path) == [ROWS[0]]


@pytest.mark.parametrize('answer', ['AND', 'and', '둘 다', '모두 만족'])
@pytest.mark.parametrize('structured', [False, True])
def test_added_group_is_preserved_through_real_graph_and_execution(tmp_path, answer, structured):
    s = current(); asset = s['resources'][0]
    asset['profile'] = {field: {'types': ['number']} for field in asset['columns'] if field != '상태'}
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None, 'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    addition = '할인은 5원 이상, 적립금은 100원 이상인 조건도 추가해줘'
    next_state = deepcopy(s); next_state['messages'].append(dict(role='user', content=addition))
    update = proposal(next_state, {'filter.field': '할인', 'filter.operator': 'gte', 'filter.value': '5', 'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    for c in update['conditions']:
        if c['key'] in ('missing.policy', 'duplicate.policy', 'output.format'): c['evidence_ids'] = raw['conditions'][-1]['evidence_ids']
    fake = Fake([raw, update]); store = Storage(tmp_path)
    rows = [dict(비용=15, 수량=4, 할인=6, 적립금=200), dict(비용=5, 수량=1, 할인=6, 적립금=200), dict(비용=15, 수량=4, 할인=1, 적립금=200)]
    store.add_asset(asset, rows)
    graph, conn = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    cfg = {'configurable': {'thread_id': s['session_id']}}
    try:
        ready = graph.invoke(s, cfg); assert ready['status'] == 'ready'
        asked = graph.invoke(Command(resume=dict(type='message', content=addition)), cfg)
        assert [q['key'] for q in asked['pending_questions']] == ['filter.match']
        found = compound_filter_state(asked)
        assert {r['field'] for r in found['rules']} == {'비용', '수량', '할인', '적립금'} and found['relation_count'] == 2
        q = asked['pending_questions'][0]; assert '기존 조건' in q['text'] and '추가 조건' in q['text']
        reply = dict(type='message', content=answer, question_id=q['id'])
        if structured: reply.update(content=q['text'] + ' → ' + answer, question_answers=[dict(question_id=q['id'], value=answer, custom=answer not in q['options'])])
        resolved = graph.invoke(Command(resume=reply), cfg)
        assert resolved['status'] == 'ready', resolved.get('issues')
        assert not resolved['pending_questions'] and fake.calls == 2
        saved = graph.invoke(Command(resume=dict(type='confirm')), cfg)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[:1]
    finally: conn.close(); store.close()


@pytest.mark.parametrize('answer', ['OR', '하나라도 만족'])
def test_mixed_group_relation_preserves_every_predicate_and_blocks(answer):
    s = current(); s['messages'] += [dict(role='user', content='할인은 5원 이상, 적립금은 100원 이상인 조건도 추가해줘'), dict(role='user', content=answer)]
    found = compound_filter_state(s)
    assert found['pending'] == 'rewrite' and {r['field'] for r in found['rules']} == {'비용', '수량', '할인', '적립금'}
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('follow_up', ['할인이 5원 이상이고 적립금이 100원 이상인 조건은 추가하지 마', '할인이 5원 이상이고 적립금이 100원 이상인 조건도 추가할지 모르겠어'])
def test_denied_or_uncertain_addition_is_not_applied(follow_up):
    s = current(); before = compound_filter_state(s)
    s['messages'].append(dict(role='user', content=follow_up)); found = compound_filter_state(s)
    assert found['rules'] == before['rules']
    if '모르겠어' in follow_up: assert found['pending']


@pytest.mark.parametrize('reply', ['할인 조건 취소해줘', '적립금 조건 취소해줘', '기존 조건 모두 유지'])
def test_cancel_pending_addition_does_not_remove_base(reply):
    s = current(); s['messages'] += [dict(role='user', content='할인은 5원 이상, 적립금은 100원 이상인 조건도 추가해줘'), dict(role='user', content=reply)]
    found = compound_filter_state(s)
    assert {r['field'] for r in found['rules']} >= {'비용', '수량'} and not found['pending']


def test_keep_reply_cannot_bypass_an_unknown_original_relation():
    s = compound_state('비용은 10원 미만, 수량은 3개 미만인 행', '기존 조건 모두 유지')
    assert compound_filter_state(s)['pending'] == 'match'


@pytest.mark.parametrize('order', list(permutations(['출력 파일 이름만 바꿔줘', '결과에는 상태도 포함해줘', '예약 조건은 매일 오전 9시로 설정해줘'])))
def test_unrelated_edits_commute_without_changing_filter_contract(order):
    s = current('상태가 완료인 행만 남겨줘.')
    numeric, textual = compound_filter_state(s)['rules'], text_filter_state(s)['predicates']
    for instruction in order:
        s['messages'].append(dict(role='user', content=instruction))
        assert compound_filter_state(s)['rules'] == numeric and not compound_filter_state(s)['pending']
        assert text_filter_state(s)['predicates'] == textual and not text_filter_state(s)['issues']


@pytest.mark.parametrize('base', ['비용이 10원 미만이거나 수량이 3개 미만인 행을 JSON으로 저장해줘.', '비용은 10원 미만, 수량은 3개 미만인 행을 JSON으로 저장해줘.'])
@pytest.mark.parametrize('addition', ['할인이 5원 미만이거나 적립금이 100원 미만인 조건도 추가해줘', '할인은 5원 미만, 적립금은 100원 미만인 조건도 추가해줘'])
@pytest.mark.parametrize('reply', ['AND', 'OR'])
def test_or_additions_cannot_silently_change_the_outer_connective(base, addition, reply):
    s = current()
    s['messages'] = [dict(role='user', content=base), dict(role='user', content=addition), dict(role='user', content=reply)]
    found = compound_filter_state(s)
    assert found['pending'] and {rule['field'] for rule in found['rules']} == {'비용', '수량', '할인', '적립금'}


@pytest.mark.parametrize('extra', ['할인이 5원 이상인 조건도 추가해줘', '할인이 5원 이상인 행만 남겨줘'])
def test_single_condition_added_to_or_group_cannot_be_flattened(extra):
    s = current()
    s['messages'] = [dict(role='user', content='비용이 10원 미만이거나 수량이 3개 미만인 행'), dict(role='user', content=extra), dict(role='user', content='나머지 조건 유지')]
    assert compound_filter_state(s)['pending'] == 'rewrite'


@pytest.mark.parametrize(('turns', 'predicate'), [
    (['상태 기준은 나중에 정할게. 상태가 완료인 행만 남겨줘.'], '완료'),
    (['상태가 완료인 행만 남겨줘. 상태 기준은 나중에 정할게.'], None),
])
def test_same_message_order_applies_to_uncertain_edits_too(turns, predicate):
    contract = text_filter_state(current(*turns))
    if predicate is None:
        assert contract['issues']
    else:
        assert not contract['issues'] and contract['predicates'][0][1]['value'] == predicate


def test_cancelled_field_guard_covers_native_and_registered_operation_settings():
    import json
    from agent_builder.design.text_filter_contract import text_filter_issues
    from tests.workflow_engine.test_workflow import node
    from workflow_engine.executors.canonical_operation import validate_settings
    s = current('상태가 완료인 행만 남기지 말고 상태와 관계없이 저장해줘.')
    operation = node('bad', 'filter_sort', operation='filter',
                     arguments_json=json.dumps(dict(rows=[dict(상태='완료')], predicates=[dict(column='상태', op='eq', value='완료')], combine='and')))
    validate_settings(operation)
    assert text_filter_issues(s, dict(workflow=dict(nodes=[operation])))


@pytest.mark.parametrize('instruction', ['상태가 완료인 행만 남겨줘.', '상태가 완료인 행은 제외해줘.', '상태가 완료인 행만 남기지 말고 상태와 관계없이 저장해줘.'])
def test_boundary_grid_results_match_an_independent_predicate(tmp_path, instruction):
    from itertools import product
    rows = [dict(비용=cost, 수량=quantity, 상태=status) for cost, quantity, status in product([5, 10, 15], [1, 3, 4], ['완료', '대기'])]
    if '관계없이' in instruction: test = lambda row: True
    elif '제외' in instruction: test = lambda row: row['상태'] != '완료'
    else: test = lambda row: row['상태'] == '완료'
    expected = [row for row in rows if row['비용'] >= 10 and row['수량'] >= 3 and test(row)]
    s = generate(seal(current(instruction))); store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], rows)
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == expected
    finally: store.close()


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('reply', ['대기만 남겨줘', '조건 취소'])
@pytest.mark.parametrize('instruction', ['상태가 완료인 행만 남기지마.',
                                         '상태 조건 취소할지 모르겠어', '상태 조건 삭제해도 될까?'])
def test_text_answer_uses_its_question_context_without_executing_echoed_labels(tmp_path, structured, reply, instruction):
    s = current('상태가 완료인 행만 남겨줘. ' + instruction); asset = s['resources'][0]
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None, 'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw, raw]); store = Storage(tmp_path); store.add_asset(asset, ROWS)
    graph, conn = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    cfg = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, cfg); assert [q['key'] for q in first['pending_questions']] == ['filter.match']
        q = first['pending_questions'][0]; event = dict(type='message', content=reply, question_id=q['id'])
        if structured: event.update(content=q['text'] + ' → ' + reply, question_answers=[dict(question_id=q['id'], value=reply)])
        ready = graph.invoke(Command(resume=event), cfg)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions']
        contract = text_filter_state(ready)
        if '대기' in reply: assert contract['predicates'][0][1]['value'] == '대기'
        else: assert not contract['predicates']
        saved = graph.invoke(Command(resume=dict(type='confirm')), cfg)
        rows = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']])['rows']
        assert rows == ([ROWS[1]] if '대기' in reply else ROWS[:2])
    finally: conn.close(); store.close()


@pytest.mark.parametrize('value', ['승인 대기', 'v1.0', '모르겠어'])
def test_quoted_text_values_are_data_not_commands(tmp_path, value):
    rows = [dict(비용=15, 수량=4, 상태=value), dict(비용=15, 수량=4, 상태='다른값')]
    s = generate(seal(current(f"상태가 '{value}'인 행만 남겨줘."))); store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], rows)
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[:1]
    finally: store.close()


def test_deferred_text_condition_is_not_reissued_as_the_same_question():
    s = current('상태가 완료인 행만 남기지마.'); ledger = judge(s, VALUES)
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'])
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    s['messages'].append(dict(role='user', content='나중에 결정할게'))
    s['condition_answers'] = [dict(turn=len(s['messages'])-1, question_ids=[question['id']], candidate_keys=['filter.match'], answer='나중에 결정할게')]
    from agent_builder.conditions.condition_readiness import judge_conditions
    result = judge_conditions(s, Fake([]))['ledger']
    assert result['decision'] == 'deferred'
    assert '보류' in next(q for q in result['questions'] if q['key'] == 'filter.match')['text']


@pytest.mark.parametrize(('instruction', 'expected'), [
    ('비용이 10원 이상이고 수량이 3개 이상인 행. 비용 조건 취소해줘.', ['수량']),
    ('비용 조건 취소해줘. 비용이 10원 이상이고 수량이 3개 이상인 행.', ['비용', '수량']),
])
def test_numeric_cancellation_in_the_same_message_obeys_order(instruction, expected):
    s = current(); s['messages'] = [dict(role='user', content=instruction)]
    assert [r['field'] for r in compound_filter_state(s)['rules']] == expected


def test_equality_replaces_the_old_comparison_on_the_same_numeric_column(tmp_path):
    s = current(); seal(s)
    s['messages'].append(dict(role='user', content="비용이 '1'인 행만 남겨줘."))
    rows = [dict(비용=1, 수량=4), dict(비용=15, 수량=4), dict(비용=1, 수량=1)]
    s = generate(seal(s)); store = Storage(tmp_path)
    try:
        plan = s['draft']['execution_plan']
        assert any(r['field']=='비용' and r['operator']=='eq' and r['value_type']=='number' for r in plan['filters'])
        store.add_asset(s['resources'][0], rows)
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status']=='succeeded' and result['rows']==rows[:1]
    finally: store.close()


def test_adding_instead_of_replacing_a_comparison_on_the_same_column_requires_clarification():
    s = current(); seal(s)
    s['messages'].append(dict(role='user', content="비용이 '1'인 행만 남기는 조건도 추가해줘."))
    assert text_filter_state(s)['issues']
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('instruction', [
    '상태 조건 취소할지 모르겠어', '상태 조건 삭제해도 될까?',
    '상태 조건 제거할까요?', '상태 조건 해제 여부는 미정이야',
])
def test_uncertain_cancellation_preserves_the_predicate_and_blocks_save(tmp_path, instruction):
    s = current('상태가 완료인 행만 남겨줘.'); seal(s)
    original = text_filter_state(s)['predicates']
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert contract['predicates'] == original
    assert contract['issues'] and '상태' not in contract['cleared_fields']
    assert judge(s, VALUES)['decision'] != 'ready'
    store = Storage(tmp_path)
    try:
        s.update(confirmed=True)
        with pytest.raises(ValueError):
            store.save_agent(s)
    finally:
        store.close()


@pytest.mark.parametrize('reply', ['상태 조건 취소해줘', '기존 조건 모두 유지'])
def test_uncertain_cancellation_can_be_resolved_without_repeating(reply):
    s = current('상태가 완료인 행만 남겨줘.', '상태 조건 삭제해도 될까?')
    ledger = judge(s, VALUES)
    question = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'])
    s['messages'].append(dict(role='user', content=reply))
    s['condition_answers'] = [dict(turn=len(s['messages'])-1, question_ids=[question['id']],
                                 candidate_keys=['filter.match'], answer=reply)]
    contract = text_filter_state(s)
    assert not contract['issues']
    assert bool(contract['predicates']) == (reply == '기존 조건 모두 유지')
    assert judge(s, VALUES)['decision'] == 'ready'
