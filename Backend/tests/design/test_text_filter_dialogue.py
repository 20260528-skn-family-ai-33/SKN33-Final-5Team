"""Independent rows and real graph transitions for the shared text-filter contract."""
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path

import pytest
from langgraph.types import Command

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.output_selection_request import output_selection
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.text_filter_contract import text_filter_state
from infrastructure.persistence.storage import Storage
from tests.conditions.test_clarification_choices import compound_state, judge, PARTIAL
from tests.conditions.test_condition_readiness import Fake, proposal
from tests.design.test_compound_filter_pipeline import generate
from tests.design.test_condition_requirement_ownership import packet
from workflow_engine.runner import run_workflow

BASE = '비용이 10원 이상이고 수량이 3개 이상인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
VALUES = {**PARTIAL, 'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
ROWS = [dict(비용=15, 수량=4, 지역='서울', 등급='A', 상태='완료'),
        dict(비용=15, 수량=4, 지역='부산', 등급='A', 상태='완료'),
        dict(비용=15, 수량=4, 지역='서울', 등급='B', 상태='대기'),
        dict(비용=5, 수량=4, 지역='서울', 등급='A', 상태='완료')]


def current(instruction):
    s = compound_state(BASE + ' ' + instruction)
    asset = s['resources'][0]
    asset.update(columns=list(ROWS[0]), format='csv',
                 profile={field: {'types': ['number' if field in ('비용', '수량') else 'string']} for field in ROWS[0]})
    return s


def prepare(s, values=None):
    ledger = judge(s, values or VALUES)
    assert ledger['decision'] == 'ready', ledger['questions']
    s.update(condition_ledger=ledger, condition_enforced=True, condition_required=True, pending_questions=[])
    # Model omits every filter: production ownership must reconstruct the whole instruction.
    requirements = extract_plan(packet(dict(kind='export', format='json')), s)
    s.update(requirement_required=True, requirement_ledger=requirements,
             requirement_fingerprint=ledger_fingerprint(s), requirement_digest=plan_digest(requirements))
    return generate(s)


def execute(s, path):
    store = Storage(path)
    try:
        store.add_asset(s['resources'][0], ROWS)
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        artifact = result['artifact']
        assert artifact['name'].endswith('.json')
        exported = json.loads(artifact['content'].lstrip('\ufeff'))
        assert exported == result['rows']
        return result['rows']
    finally:
        store.close()


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급이 A인 행만 남겨줘',
    '등급이 A이고 지역이 서울인 행만 남겨줘',
    '지역이 서울이며 등급이 A인 행만 남겨줘',
    '지역이 서울이면서 등급이 A인 행만 남겨줘',
    '지역이 서울인 행만, 등급이 A인 행만',
    '지역이 서울인 것만 그리고 등급이 A인 것만 남겨줘',
    '서울 지역만, A 등급만 남겨줘',
])
def test_text_conjunction_keeps_every_operand_through_export(tmp_path, instruction):
    s = prepare(current(instruction))
    assert execute(s, tmp_path) == ROWS[:1]


@pytest.mark.parametrize('order', list(permutations(['지역이 서울', '등급이 A', '상태가 완료'])))
def test_three_text_conditions_are_order_independent(tmp_path, order):
    s = prepare(current('이고 '.join(order) + '인 행만 남겨줘'))
    assert execute(s, tmp_path) == ROWS[:1]


@pytest.mark.parametrize('instruction', ['서울 지역만', '지역이 서울인 것만', '지역은 서울만 남겨줘', '서울 지역인 행만'])
def test_alternate_text_wording_has_the_same_result(tmp_path, instruction):
    assert execute(prepare(current(instruction)), tmp_path) == [ROWS[0], ROWS[2]]


@pytest.mark.parametrize('instruction', ['결과에는 상태만 포함해줘.', '출력에 상태만 포함해줘.'])
def test_output_column_followup_preserves_the_existing_row_filter(tmp_path, instruction):
    s = current('상태가 완료인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content=instruction))
    assert execute(prepare(s), tmp_path) == [{'상태': row['상태']} for row in ROWS[:2]]


def test_exporting_one_column_preserves_its_row_filter():
    s = current('서울 지역만 남겨줘.')
    s['messages'].append(dict(role='user', content='결과에는 지역만 저장해줘.'))
    contract = text_filter_state(s)
    assert not contract['issues']
    assert [(rule['field'], rule['value']) for _, rule in contract['predicates']] == [('지역', '서울')]
    assert output_selection(s) == ['지역']


def test_quoted_output_destination_remains_a_literal_filter_value():
    contract = text_filter_state(current("'결과에는' 상태만 남겨줘."))
    assert not contract['issues']
    assert [(rule['field'], rule['value']) for _, rule in contract['predicates']] == [('상태', '결과에는')]


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급은 나중에 정할게. 행만 남겨줘',
    '지역이 서울인 행만, 등급은 아직 미정인 행만',
    '지역이 서울인 행만 또는 등급이 A인 행만 남겨줘',
    '지역이 서울이고 지역이 부산인 행만 남겨줘',
])
def test_unsettled_operands_never_become_partial_ready_filters(instruction):
    s = current(instruction)
    assert text_filter_state(s)['issues']
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('removed_field', ['지역', '등급'])
def test_missing_text_operand_cannot_be_saved(tmp_path, removed_field):
    s = prepare(current('지역이 서울이고 등급이 A인 행만 남겨줘'))
    bad = deepcopy(s)
    for node in bad['draft']['workflow']['nodes']:
        if node['tool'] == 'filter_sort':
            node['settings']['execution_plan']['filters'] = [r for r in node['settings']['execution_plan']['filters'] if r['field'] != removed_field]
    assert validate_node(bad)['status'] in ('validation_failed', 'needs_clarification')
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.save_agent(bad)
    finally:
        store.close()


@pytest.mark.parametrize('field', ['비용', '지역'])
@pytest.mark.parametrize('structured', [False, True])
def test_keep_answer_finishes_the_question_and_exports_original_conditions(tmp_path, field, structured):
    s = current(f'지역이 서울인 행만 남겨줘. {field} 조건 삭제해도 될까?')
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw])
    store = Storage(tmp_path); store.add_asset(s['resources'][0], ROWS)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        asked = graph.invoke(s, config)
        assert [q['key'] for q in asked['pending_questions']] == ['filter.match']
        question = asked['pending_questions'][0]
        reply = dict(type='message', content='기존 조건 모두 유지', question_id=question['id'])
        if structured:
            reply.update(content=question['text'] + ' → 기존 조건 모두 유지',
                         question_answers=[dict(question_id=question['id'], value='기존 조건 모두 유지')])
        ready = graph.invoke(Command(resume=reply), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions'] and not multiple_outputs(ready)
        assert fake.calls == 1
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [ROWS[0], ROWS[2]]
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('reply', ['기존 조건 모두 유지', '모두 만족', '둘 다', 'AND', 'OR'])
def test_filter_control_replies_do_not_create_extra_artifacts(reply):
    s = current('지역이 서울인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='질문 → ' + reply))
    s['condition_answers'] = [dict(turn=1, candidate_keys=['filter.match'], answer=reply)]
    assert not multiple_outputs(s)
    s['messages'].append(dict(role='user', content='별도 CSV 파일도 만들어줘.'))
    assert multiple_outputs(s)


def test_deployment_gate_runs_the_filter_contract_regressions():
    workflow = Path(__file__).resolve().parents[3] / '.github/workflows/deploy.yml'
    text = workflow.read_text()
    for suite in ['tests/conditions/test_clarification_choices.py', 'tests/design/test_compound_filter_pipeline.py',
                  'tests/design/test_filter_edit_sequences.py', 'tests/design/test_text_filter_dialogue.py']:
        assert suite in text


@pytest.mark.parametrize('instruction', [
    '지역이 서울이고 등급이 A인 행만 JSON으로 저장해줘.',
    '등급이 A이고 지역이 서울인 행만 JSON으로 저장해줘.',
    '서울 지역만, A 등급만 JSON으로 저장해줘.',
])
def test_text_only_request_uses_the_same_contract_in_slots_and_export(tmp_path, instruction):
    s = current('')
    s['messages'][0]['content'] = instruction + ' 빈 값은 오류, 중복은 유지해.'
    # The extractor knows the field but leaves the operator/value empty.
    values = {'filter.field': ('지역', '지역'), 'filter.operator': (None, ''),
              'filter.value': (None, ''), 'output.format': ('json', 'JSON'),
              'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}
    assert execute(prepare(s, values), tmp_path) == [ROWS[0], ROWS[3]]


@pytest.mark.parametrize('instruction', [
    '두 조건 모두 만족하는 행을 JSON으로 저장해줘.',
    '두 조건을 둘 다 만족하는 결과를 JSON으로 저장해줘.',
    '기존 조건 모두 유지. 결과를 JSON으로 저장해줘.',
])
def test_condition_quantifiers_are_not_artifact_counts(instruction):
    assert not multiple_outputs(current(instruction))


def test_cancelling_one_text_atom_preserves_every_other_atom(tmp_path):
    s = current('지역이 서울이고 등급이 A인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='지역 조건 취소해줘'))
    assert execute(prepare(s), tmp_path) == ROWS[:2]


def test_keep_resolves_multiple_cancellation_questions_without_losing_conditions(tmp_path):
    s = current('지역이 서울이고 등급이 A인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='지역 조건 삭제해도 될까? 등급 조건 취소할지 모르겠어'))
    ledger = judge(s, VALUES)
    assert ledger['decision'] == 'clarify'
    q = next(q for q in ledger['questions'] if q['key'] == 'filter.match')
    s['messages'].append(dict(role='user', content='기존 조건 모두 유지'))
    s['condition_answers'] = [dict(turn=2, question_ids=[q['id']], candidate_keys=['filter.match'], answer='기존 조건 모두 유지')]
    assert execute(prepare(s), tmp_path) == ROWS[:1]


def test_shared_exclusion_does_not_invert_each_operand_independently():
    s = current('지역이 서울이고 등급이 A인 행만 제외해줘')
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('instruction', [
    '중복 학생ID는 마지막 행만 남겨', 'amount가 큰 행만 JSON으로 저장해줘',
    'customer_id 가 같은 행 중 첫 번째만 유지한 뒤 JSON 로 저장한다.',
    '비용이 비어 있지 않은 행만 남겨줘',
])
def test_unparsed_selection_does_not_take_over_other_condition_owners(instruction):
    s = current('')
    s['messages'][0]['content'] = instruction
    s['resources'][0]['columns'] += ['학생ID', 'amount', 'customer_id']
    assert not text_filter_state(s)['issues']


@pytest.mark.parametrize('instruction', [
    "'서울' 지역만, 'A' 등급만 남겨줘", "지역이 '서울 또는 부산'인 행만 남겨줘",
])
def test_quoted_literals_do_not_become_connectives(instruction):
    contract = text_filter_state(current(instruction))
    assert not contract['issues']
    if '또는' in instruction:
        assert contract['predicates'][0][1]['value'] == '서울 또는 부산'
    else:
        assert {r['field'] for _, r in contract['predicates']} == {'지역', '등급'}


@pytest.mark.parametrize('followup', [
    '지역이 부산이고 지역이 대구인 행만 남겨줘',
    '비용 조건 삭제해도 될까?',
    '지역 조건 삭제해도 될까?',
])
@pytest.mark.parametrize('structured', [False, True])
def test_followup_proposals_do_not_replace_confirmed_values_until_resolved(tmp_path, followup, structured):
    s = current('지역이 서울인 행만 남겨줘.')
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw, raw]); store = Storage(tmp_path)
    store.add_asset(s['resources'][0], ROWS)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, config)
        assert first['status'] == 'ready'
        original = text_filter_state(first)['predicates']
        asked = graph.invoke(Command(resume=dict(type='message', content=followup)), config)
        assert asked['status'] == 'needs_clarification'
        assert text_filter_state(asked)['predicates'] == original
        if '대구' in followup:
            assert text_filter_state(asked)['pending_decisions']['지역'].proposed == ('부산', '대구')
        q = next(q for q in asked['pending_questions'] if q['key'] == 'filter.match')
        before = fake.calls
        answer = dict(type='message', content='기존 조건 모두 유지', question_id=q['id'])
        if structured:
            answer.update(content=q['text'] + ' → 기존 조건 모두 유지',
                          question_answers=[dict(question_id=q['id'], value='기존 조건 모두 유지')])
        ready = graph.invoke(Command(resume=answer), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions'] and fake.calls == before
        assert text_filter_state(ready)['predicates'] == original
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [ROWS[0], ROWS[2]]
    finally:
        connection.close(); store.close()


def test_unconfirmed_conflicting_proposal_is_not_a_keepable_original():
    s = current('지역이 부산이고 지역이 대구인 행만 남겨줘')
    contract = text_filter_state(s)
    assert contract['issues'] and not contract['predicates'] and not contract['can_keep']


@pytest.mark.parametrize('structured_threshold', [False, True])
@pytest.mark.parametrize('structured_keep', [False, True])
def test_numeric_condition_set_by_a_short_answer_survives_later_keep(tmp_path, structured_threshold, structured_keep):
    s = current('')
    s['messages'][0]['content'] = '비용이 낮고 수량이 10개보다 많은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw])
    rows = [dict(비용=4, 수량=11), dict(비용=6, 수량=11), dict(비용=4, 수량=9)]
    store = Storage(tmp_path); store.add_asset(s['resources'][0], rows)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, config)
        assert first['status'] == 'needs_clarification'
        q = next(q for q in first['pending_questions'] if q['key'] == 'filter.match')
        answered = deepcopy(first); answered['messages'].append(dict(role='user', content='5원 미만'))
        update = proposal(answered, {'filter.operator': 'lt', 'filter.value': '5'})
        fake.packets = iter([update, dict(operations=['filter'], conditions=[], unsupported_actions=[])])
        answer = dict(type='message', content='5원 미만', question_id=q['id'])
        if structured_threshold:
            answer.update(content=q['text'] + ' → 5원 미만',
                          question_answers=[dict(question_id=q['id'], value='5원 미만')])
        ready = graph.invoke(Command(resume=answer), config)
        assert ready['status'] == 'ready', ready.get('issues')
        original = deepcopy(ready['draft']['execution_plan']['filters'])
        asked = graph.invoke(Command(resume=dict(type='message', content='비용 조건 삭제해도 될까?')), config)
        assert asked['status'] == 'needs_clarification'
        q = next(q for q in asked['pending_questions'] if q['key'] == 'filter.match')
        answer = dict(type='message', content='기존 조건 모두 유지', question_id=q['id'])
        if structured_keep:
            answer.update(content=q['text'] + ' → 기존 조건 모두 유지',
                          question_answers=[dict(question_id=q['id'], value='기존 조건 모두 유지')])
        calls = fake.calls
        kept = graph.invoke(Command(resume=answer), config)
        assert kept['status'] == 'ready', kept.get('pending_questions')
        assert kept['condition_answers'][-1]['filter_context']['field'] == '비용'
        assert kept['condition_answers'][-1]['filter_context']['value'] == 5
        assert not kept['pending_questions'] and kept['draft']['execution_plan']['filters'] == original
        assert fake.calls == calls
        # Old checkpoints do not yet contain the server-owned context snapshot.
        legacy = deepcopy(kept)
        for event in legacy['condition_answers']:
            event.pop('filter_context', None)
        from agent_builder.conditions.condition_readiness import compound_filter_state
        assert compound_filter_state(legacy)['pending'] is None
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[:1]
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('scope', ['모든 숫자 조건 취소해줘', '전체 숫자 필터 제거해주세요', '기존 숫자 조건은 해제해줘'])
def test_numeric_bulk_cancel_preserves_text_and_actual_rows(tmp_path, scope):
    s = current('상태가 완료인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content=scope))
    contract = text_filter_state(s)
    assert [rule['field'] for _, rule in contract['predicates']] == ['상태']
    assert contract['cleared_fields'] == {'비용', '수량'}
    assert execute(prepare(s, {**VALUES, 'filter.field': ('상태', '상태'),
                               'filter.operator': ('eq', '완료'), 'filter.value': ('완료', '완료')}), tmp_path) == [ROWS[0], ROWS[1], ROWS[3]]


@pytest.mark.parametrize('instruction', ['요약표와 상세표 둘 다 CSV로 만들어줘', '서울 행과 부산 행을 모두 CSV로 저장해줘'])
def test_multiple_output_quantifiers_are_retained(instruction):
    assert multiple_outputs(current(instruction))


def test_uncovered_text_question_accepts_its_short_answer():
    s = current('상태가 완료된 주문만 남겨줘')
    contract = text_filter_state(s)
    assert contract['issues']
    turn = len(s['messages'])
    s['messages'].append(dict(role='user', content='완료'))
    s['condition_answers'] = [dict(turn=turn, candidate_keys=['filter.match'])]
    contract = text_filter_state(s)
    assert not contract['issues']
    assert [(rule['field'], rule['value']) for _, rule in contract['predicates']] == [('상태', '완료')]


@pytest.mark.parametrize(('word', 'operator'), [('크거나 같은', 'gte'), ('작거나 같은', 'lte'), ('크거나 같고', 'gte')])
def test_inclusive_natural_comparisons_do_not_become_exclusive_or_or(word, operator):
    from agent_builder.conditions.condition_readiness import _compound_filter_in_turn, _stated_rule, _COMPARATOR_SPAN
    request = f'비용이 10원보다 {word} 수량이 3개 미만인 행만 남겨줘'
    s = current('')
    assert _stated_rule('비용', next(_COMPARATOR_SPAN.finditer(request)).group())['operator'] == operator
    if word.endswith('같고'):
        mode, rules = _compound_filter_in_turn(s, request)
        assert mode == 'all' and [rule['operator'] for rule in rules] == ['gte', 'lt']


def test_masking_a_column_is_not_an_uncovered_selection_operand():
    from agent_builder.design.text_filter_parser import uncovered_selection_fields
    assert not uncovered_selection_fields('고객명은 마스킹하고 첫 행만 남겨줘', {'고객명'}, [])


@pytest.mark.parametrize('structured', [False, True])
def test_short_text_answer_resolves_real_graph_and_exports(tmp_path, structured):
    s = current('')
    s['messages'] = [dict(role='user', content='JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'),
                     dict(role='user', content='상태가 완료된 주문만 남겨줘')]
    raw = proposal(s, {'filter.field': '상태', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw, raw])
    store = Storage(tmp_path); store.add_asset(s['resources'][0], ROWS)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        asked = graph.invoke(s, config)
        assert any(q['key'] == 'filter.match' for q in asked['pending_questions']), {key: asked.get(key) for key in ('status', 'pending_questions', 'issues', 'last_error')}
        question = next(q for q in asked['pending_questions'] if q['key'] == 'filter.match')
        answer = dict(type='message', content='완료', question_id=question['id'])
        if structured:
            answer.update(content=question['text'] + ' → 완료', question_answers=[dict(question_id=question['id'], value='완료')])
        ready = graph.invoke(Command(resume=answer), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions'] and fake.calls == 2
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [ROWS[0], ROWS[1], ROWS[3]]
    finally:
        connection.close(); store.close()


def test_inclusive_and_pipeline_keeps_boundary_and_excludes_one_sided_matches(tmp_path):
    s = current('')
    s['messages'][0]['content'] = '비용이 15원보다 크거나 같고 수량이 5개 미만인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    assert execute(prepare(s), tmp_path) == ROWS[:3]


@pytest.mark.parametrize('instruction', ['모든 조건 취소해줘', '전체 필터 삭제해줘'])
def test_unscoped_bulk_cancel_still_clears_text_and_numeric(instruction):
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert not contract['predicates'] and contract['cleared_fields'] == set(s['resources'][0]['columns'])


@pytest.mark.parametrize('instruction', ['모든 숫자 조건 취소하지 마', '모든 숫자 조건 취소해도 될까?'])
def test_numeric_bulk_cancel_requires_a_decisive_command(instruction):
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert [rule['field'] for _, rule in contract['predicates']] == ['상태']
    assert not contract['cleared_fields']


@pytest.mark.parametrize('instruction', [
    '모든 숫자 조건은 취소하고 상태 조건은 유지해줘',
    '상태 조건은 유지하고 모든 숫자 조건은 취소해줘',
    '모든 숫자 조건 취소해줘. 상태 조건은 유지해줘.',
    '비용과 수량 조건 취소하고 상태 조건 유지해줘',
    '비용, 수량 조건 취소해줘',
    '비용 조건이랑 수량 조건 취소해줘',
    '비용 및 수량 필터 제거해줘',
    '비용조건과 수량조건 취소해줘',
])
def test_shared_cancel_transaction_preserves_text_and_exports_all_matching_rows(tmp_path, instruction):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert contract['cleared_fields'] == {'비용', '수량'}
    assert not contract['issues'] and not compound_filter_state(s)['rules']
    ready = prepare(s)  # The model still proposes the old numeric field; the contract owns the edit.
    assert execute(ready, tmp_path) == [ROWS[0], ROWS[1], ROWS[3]]
    stale = deepcopy(ready)
    for node in stale['draft']['workflow']['nodes']:
        if node['tool'] == 'filter_sort':
            node['settings']['execution_plan']['filters'].append(dict(field='비용', operator='gte', value=10,
                                                                     compare_field=None, value_type='number'))
    store = Storage(tmp_path / 'stale')
    try:
        with pytest.raises(ValueError):
            store.save_agent(stale)
    finally:
        store.close()


@pytest.mark.parametrize('instruction', [
    '비용, 수량 조건 취소해도 될까? 상태 조건 유지해줘',
    '비용과 수량 조건을 취소할지 모르겠어',
    '그 조건 취소하고 상태 조건 유지해줘',
    '비용 조건 취소하고 비용 조건 유지해줘',
    '없는열 조건 취소하고 상태 조건 유지해줘',
    '없는열, 비용 조건 취소해줘.',
    '비용, 없는열 조건 취소해줘.',
    '없는열과 비용 조건 취소해줘.',
])
def test_unresolved_cancel_transaction_cannot_reuse_old_conditions(instruction):
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert contract['issues'] and not contract['cleared_fields']
    assert judge(s, VALUES)['decision'] == 'clarify'


@pytest.mark.parametrize('instruction', [
    '출력에서 비용과 수량 삭제하고 나머지 열만 내보내줘',
    '결과에서 지역을 삭제해줘',
    '비용과 수량 필터를 삭제하는 건 하지 마.',
    '비용과 수량 조건의 취소는 하지 마.',
    '비용이 10원 이상이고 수량이 3개 이상인 행을 필터링하고 중복은 유지해줘.',
])
def test_non_cancelling_instructions_preserve_the_filters_and_exported_rows(tmp_path, instruction):
    from agent_builder.conditions.condition_readiness import compound_filter_state

    s = current('상태가 완료인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content=instruction))
    contract = text_filter_state(s)
    assert not contract['cleared_fields'] and not contract['issues']
    assert {rule['field'] for rule in compound_filter_state(s)['rules']} == {'비용', '수량'}
    assert execute(prepare(s), tmp_path) == ROWS[:2]


def test_bare_named_keep_resolves_a_pending_cancellation(tmp_path):
    s = current('지역이 서울인 행만 남겨줘.')
    s['messages'].append(dict(role='user', content='지역 조건 삭제해도 될까?'))
    assert judge(s, VALUES)['decision'] == 'clarify'
    s['messages'].append(dict(role='user', content='지역 유지해줘.'))
    assert not text_filter_state(s)['issues']
    assert execute(prepare(s), tmp_path) == [ROWS[0], ROWS[2]]


@pytest.mark.parametrize('instruction', [
    '비용과 수량 조건은 취소하지 마. 상태 조건 유지해줘',
    '모든 숫자 조건은 취소하면 안 돼. 상태 조건 유지해줘',
    "설명에는 '모든 숫자 조건 취소해줘'라고 적어줘",
    '파일 이름은 비용 조건 취소로 설정해줘',
])
def test_denied_and_metadata_cancellations_preserve_confirmed_rows(tmp_path, instruction):
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    assert not text_filter_state(s)['cleared_fields']
    # Metadata and denied edits must never remove a predicate; some requests may need review.
    contract = text_filter_state(s)
    assert [rule['field'] for _, rule in contract['predicates']] == ['상태']


@pytest.mark.parametrize('separator', [', ', '과 ', ' 및 ', ' 조건과 '])
def test_shared_edit_lexer_expands_listed_fields_without_matching_substrings(separator):
    from agent_builder.conditions.filter_edits import filter_edit_events
    events = filter_edit_events('총비용' + separator + '수량 조건 취소해줘', {'비용', '총비용', '수량', '상태'},
                                numeric_fields={'비용', '총비용', '수량'})
    assert {field for event in events if event.action == 'clear' for field in event.fields} == {'총비용', '수량'}


@pytest.mark.parametrize('structured', [False, True])
def test_cancel_after_ready_rebinds_legacy_slots_without_restoring_old_numeric_state(tmp_path, structured):
    s = current('상태가 완료인 행만 남겨줘')
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': None, 'filter.value': None,
                      'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'})
    fake = Fake([raw, raw])
    store = Storage(tmp_path); store.add_asset(s['resources'][0], ROWS)
    graph, connection = build_graph(store, conversation=compile_conditions, condition_transport=fake, condition_mode='enforce')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, config)
        assert first['status'] == 'ready'
        followup = '비용, 수량 조건 취소해도 될까? 상태 조건은 유지해줘' if structured else '모든 숫자 조건은 취소하고 상태 조건은 유지해줘'
        ready = graph.invoke(Command(resume=dict(type='message', content=followup)), config)
        if structured:
            assert ready['status'] == 'needs_clarification'
            question = next(q for q in ready['pending_questions'] if q['key'] == 'filter.match')
            # Resolve the same two-column transaction explicitly, through the real form protocol.
            fake.packets = iter([raw])
            ready = graph.invoke(Command(resume=dict(type='message', question_id=question['id'],
                content=question['text'] + ' → 비용과 수량 조건 취소해줘',
                question_answers=[dict(question_id=question['id'], value='비용과 수량 조건 취소해줘')])), config)
        assert ready['status'] == 'ready', ready.get('issues') or ready.get('pending_questions')
        assert ready['condition_ledger']['slots']['filter.field']['value'] == '상태'
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded' and result['rows'] == [ROWS[0], ROWS[1], ROWS[3]]
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('suffix', ['', '. 상태 조건은 유지해줘'])
def test_cancel_then_reassert_in_same_turn_keeps_the_new_condition(tmp_path, suffix):
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content='비용 조건 취소하고 비용은 20원 이상으로 바꿔줘' + suffix))
    assert execute(prepare(s), tmp_path) == []


def test_cancel_all_numeric_without_any_text_filter_exports_unfiltered_rows(tmp_path):
    s = current('')
    s['messages'].append(dict(role='user', content='모든 숫자 조건 취소해줘'))
    ledger = judge(s, VALUES)
    assert ledger['decision'] == 'ready' and 'filter' not in ledger['operations']
    assert not any(key.startswith('filter.') for key in ledger['slots'])
    from tests.conditions.test_condition_readiness import workflow_draft
    draft = workflow_draft(s)
    draft['execution_plan']['filters'] = []
    draft['workflow']['nodes'][1]['settings']['execution_plan']['filters'] = []
    s.update(condition_ledger=ledger, condition_enforced=True, condition_required=True,
             draft=draft, confirmed=True, status='ready')
    assert execute(s, tmp_path) == ROWS


def test_partially_unresolved_transaction_does_not_apply_a_half_cancel():
    s = current('상태가 완료인 행만 남겨줘')
    s['messages'].append(dict(role='user', content='비용 조건 취소하고 수량 조건은 취소할지 모르겠어'))
    contract = text_filter_state(s)
    assert contract['issues'] and not contract['cleared_fields']
    turn = len(s['messages'])
    s['messages'].append(dict(role='user', content='기존 조건 모두 유지'))
    s['condition_answers'] = [dict(turn=turn, candidate_keys=['filter.match'])]
    assert not text_filter_state(s)['issues'] and not text_filter_state(s)['cleared_fields']
    from agent_builder.conditions.condition_readiness import compound_filter_state
    assert {rule['field'] for rule in compound_filter_state(s)['rules']} == {'비용', '수량'}


def test_same_turn_numeric_scope_uses_explicit_comparisons_without_profile_metadata():
    s = current('상태가 완료인 행만 남겨줘. 모든 숫자 조건 취소해줘')
    s['resources'][0].pop('profile', None)
    contract = text_filter_state(s)
    assert contract['cleared_fields'] == {'비용', '수량'}
    assert [rule['field'] for _, rule in contract['predicates']] == ['상태']


def test_cancelling_a_resolved_operand_does_not_drop_the_other_unresolved_operand():
    from agent_builder.conditions.condition_readiness import compound_filter_state
    s = current('')
    s['messages'][0]['content'] = '비용이 10원 이상이고 수량이 많은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    s['messages'].append(dict(role='user', content='비용 조건 취소해줘'))
    waiting = compound_filter_state(s)
    assert waiting['pending'] == 'complete' and waiting['missing_fields'] == ['수량']
    s['messages'].append(dict(role='user', content='수량은 3개 초과'))
    complete = compound_filter_state(s)
    assert not complete['pending'] and complete['mode'] == 'all'
    assert [(r['field'], r['operator'], r['value']) for r in complete['rules']] == [('수량', 'gt', 3)]


@pytest.mark.parametrize('instruction', ['결과에는 지역과 등급만 포함해줘', '출력에 지역, 등급만 표시해줘', '결과에는 등급 및 지역만 저장해줘'])
def test_projection_list_is_not_a_literal_predicate_and_exports_requested_columns(tmp_path, instruction):
    s = current('지역이 서울인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    assert [(r['field'], r['value']) for _, r in text_filter_state(s)['predicates']] == [('지역', '서울')]
    fields = output_selection(s)
    assert set(fields) == {'지역', '등급'}
    assert execute(prepare(s), tmp_path) == [{key: row[key] for key in fields} for row in [ROWS[0], ROWS[2]]]


@pytest.mark.parametrize('ending', ['남기지 마', '남기고 싶지 않아', '골라서는 안 돼', '선별하지 마'])
def test_denied_numeric_selection_cannot_be_saved_as_positive(ending):
    s = current('')
    s['messages'][0]['content'] = f'비용이 10원 이상이고 수량이 3개 이상인 행만 {ending}. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('answer', ['몰라', '취소', '아니', '모르겠어', '상관없어'])
def test_nonanswer_never_becomes_a_text_value(answer):
    s = current('상태가 완료된 주문만 남겨줘')
    s['messages'].append(dict(role='user', content=answer))
    s['condition_answers'] = [dict(turn=1, candidate_keys=['filter.match'])]
    contract = text_filter_state(s)
    assert contract['issues'] and not contract['predicates']
    assert judge(s, VALUES)['decision'] != 'ready'


@pytest.mark.parametrize('instruction', ['지역 조건은 바꾸지 마', '지역 조건은 변경하지 않아도 돼', '지역 조건 수정은 필요 없어'])
def test_denied_amendment_keeps_the_original_text_filter(tmp_path, instruction):
    s = current('지역이 서울인 행만 남겨줘')
    s['messages'].append(dict(role='user', content=instruction))
    assert not text_filter_state(s)['issues']
    assert execute(prepare(s), tmp_path) == [ROWS[0], ROWS[2]]


def test_adding_a_column_does_not_mean_adding_a_second_region_predicate():
    s = current('지역이 서울인 행만 남겨줘')
    s['messages'].append(dict(role='user', content='합계 열을 추가하고 지역이 부산인 행만 남겨줘'))
    contract = text_filter_state(s)
    assert not contract['issues'] and [(r['field'],r['value']) for _,r in contract['predicates']] == [('지역','부산')]


def test_all_null_values_error_policy_is_not_multiple_artifacts():
    assert not multiple_outputs(current('빈 값은 모두 오류로 처리해'))


def test_field_name_containing_an_edit_verb_cannot_cancel_another_field():
    from agent_builder.conditions.filter_edits import filter_edit_events
    assert {field for event in filter_edit_events('보존지역 조건 취소해줘', {'지역','보존지역'})
            if event.action == 'clear' for field in event.fields} == {'보존지역'}


@pytest.mark.parametrize('instruction', ['비용과 수량 필터 삭제를 하지 마', '첨부 표에서 지역과 관계없이 저장해줘'])
def test_edit_subject_and_negated_action_noun_are_preserved(instruction):
    from agent_builder.conditions.filter_edits import filter_edit_events
    events = filter_edit_events(instruction, {'비용','수량','지역'}, numeric_fields={'비용','수량'})
    expected = {'지역'} if instruction.startswith('첨부') else set()
    assert {field for event in events if event.action=='clear' for field in event.fields} == expected


@pytest.mark.parametrize('answer', ['3개 미만으로 할지는 아직 모르겠어', '3개 미만으로 정하지 마'])
def test_unconfirmed_numeric_reply_does_not_set_a_missing_operand(answer):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    s = current(''); s['messages'][0]['content']='비용이 10원 이상이고 수량이 많은 행만 남겨줘'
    s['messages'].append(dict(role='user',content=answer))
    assert compound_filter_state(s)['pending']


def test_text_only_cancel_preserves_an_unresolved_numeric_subject_without_profile():
    from agent_builder.conditions.condition_readiness import compound_filter_state
    s=current('');s['resources'][0].pop('profile',None)
    s['messages'][0]['content']='비용이 10원 이상이고 수량이 많은 행만 남겨줘'
    s['messages'].append(dict(role='user',content='모든 텍스트 조건 취소해줘'))
    assert '수량' not in text_filter_state(s)['cleared_fields']
    assert compound_filter_state(s)['pending']=='complete'


@pytest.mark.parametrize('reply', ['몰라', '취소', '아니', '모르겠어', '상관없어'])
def test_real_question_nonanswer_stays_unresolved_and_cannot_save(tmp_path, reply):
    s=current('');s['messages'][0]['content']='상태가 완료된 주문만 남겨줘. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    raw=proposal(s,{'filter.field':'상태','filter.operator':None,'filter.value':None,
                    'missing.policy':'error','duplicate.policy':'keep','output.format':'json'})
    store=Storage(tmp_path);store.add_asset(s['resources'][0],ROWS)
    graph,connection=build_graph(store,conversation=compile_conditions,condition_transport=Fake([raw,raw,raw]),condition_mode='enforce')
    config={'configurable':{'thread_id':s['session_id']}}
    try:
        first=graph.invoke(s,config)
        question=next(q for q in first['pending_questions'] if q['key']=='filter.match')
        after=graph.invoke(Command(resume=dict(type='message',content=reply,
                         question_answers=[dict(question_id=question['id'],value=reply)])),config)
        assert after['status']!='ready' and not after.get('saved_agent_id')
        with pytest.raises(ValueError):store.save_agent({**after,'confirmed':True})
    finally:
        connection.close();store.close()


def test_source_qualified_filter_clear_reaches_storage_and_execution(tmp_path):
    s=current('지역이 서울인 행만 남겨줘')
    s['messages'].append(dict(role='user',content='첨부 표에서 지역과 관계없이 저장해줘'))
    assert execute(prepare(s),tmp_path)==ROWS[:3]


def test_column_name_with_keep_verb_cancels_only_that_exact_column(tmp_path):
    s=current('지역이 서울이고 보존지역이 부산인 행만 남겨줘')
    s['resources'][0]['columns'].append('보존지역')
    s['messages'].append(dict(role='user',content='보존지역 조건 취소해줘'))
    contract=text_filter_state(s)
    assert contract['cleared_fields']=={'보존지역'}
    assert [(r['field'],r['value']) for _,r in contract['predicates']]==[('지역','서울')]


def test_separate_output_restriction_does_not_negate_an_affirmed_row_selection():
    from agent_builder.conditions.filter_edits import selection_action_uncertain
    assert not selection_action_uncertain('인 행만 남겨줘. 파일 삭제하지 마')
    assert not selection_action_uncertain('인 행만 남겨줘 그리고 지역 열 삭제하지 마')
    assert selection_action_uncertain('인 행만 남기지 마')


def test_explicit_literal_cancel_status_is_still_a_valid_cell_value():
    s=current('상태가 취소인 행만 남겨줘')
    assert [(r['field'],r['value']) for _,r in text_filter_state(s)['predicates']]==[('상태','취소')]
