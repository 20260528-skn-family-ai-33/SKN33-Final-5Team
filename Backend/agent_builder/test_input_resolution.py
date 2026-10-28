"""Behavior tests with controlled extraction; these are not model accuracy scores."""
from copy import deepcopy
import json

import pytest
from langgraph.types import Command

from agent_builder.condition_readiness import judge_conditions, source_catalog, validate_conditions
from agent_builder.graph import build_graph
from agent_builder.input_requirements import design_requirements, execution_requirements, preflight_execution
from agent_builder.input_resolution import answer_kind
from agent_builder.storage import Storage
from agent_builder.test_condition_readiness import state, proposal, checked, Fake, workflow_draft
from agent_builder.test_workflow import node, bind, edge
from agent_builder.worker import handle
from agent_builder.condition_readiness import ConditionContractError
from agent_builder.nodes.condition_check import make_condition_check


def waiting():
    s = state('비용이 큰 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s['condition_ledger'] = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': None}))
    s['pending_questions'] = s['condition_ledger']['questions']
    return s


def respond(s, text, question_id=None):
    s = deepcopy(s)
    pending = s['pending_questions']
    s['messages'] += [{'role': 'assistant', 'content': pending[0]['text']}, {'role': 'user', 'content': text}]
    s.setdefault('condition_answers', []).append({'turn': len(s['messages']) - 1, 'answer': text,
        'question_ids': [q['id'] for q in pending if not question_id or q['id'] == question_id],
        'candidate_keys': [q['key'] for q in pending if not question_id or q['id'] == question_id]})
    return s


@pytest.mark.parametrize('text', ['모르겠어', '모르겠어요.', ' 잘 모르겠어요! ', '몰라요', '미정',
                                  '아직 결정 못 했어요', '아직 결정 못 했어', '결정하지 못했어요', '아직 정하지 못했어요'])
def test_unknown_skips_model_and_never_becomes_value(text):
    s = respond(waiting(), text)
    fake = Fake([])
    result = judge_conditions(s, fake)
    ledger = result['ledger']
    assert result['model_calls'] == fake.calls == 0
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['filter.value']['value'] is None
    assert ledger['slots']['missing.policy']['value'] == 'error'
    assert ledger['slots']['duplicate.policy']['value'] == 'keep'
    assert ledger['questions'][0]['options'] == ['자료 연결 후 기준 입력', '기준을 직접 입력', '나중에 결정']


def test_even_injected_specified_unknown_or_invented_value_is_blocked():
    s = respond(waiting(), '모르겠어')
    for value in ['모르겠어', '999999']:
        ledger = checked(s, proposal(s, {'filter.value': value}))
        assert ledger['decision'] == 'clarify'
        assert ledger['slots']['filter.value']['value'] is None


@pytest.mark.parametrize('text', ['"모르겠어"인 행을 골라줘', '기준은 모르겠지만 100만원 초과로 해줘', '모르겠어 → 상태값', '주소는 서울',
                                  '아직 결정 못 했어요라는 문구가 있는 행', '아직 결정 못 했어요. 우선 43100원 초과로 해줘'])
def test_content_with_unknown_word_is_not_a_standalone_nonanswer(text):
    assert answer_kind(text) == 'answer'


def test_literal_unknown_text_filter_remains_valid():
    s = state('상태가 "모르겠어"인 행만 골라줘.')
    ledger = checked(s, proposal(s, {'filter.field': '상태', 'filter.operator': 'eq', 'filter.value': '모르겠어'}))
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.value']['value'] == '모르겠어'


@pytest.mark.parametrize('reply', ['모르겠어', '나중에 결정'])
def test_second_unknown_or_explicit_defer_preserves_pending_state(reply):
    s = respond(waiting(), '모르겠어')
    s['condition_ledger'] = judge_conditions(s, Fake([]))['ledger']
    s['pending_questions'] = s['condition_ledger']['questions']
    s = respond(s, reply)
    ledger = judge_conditions(s, Fake([]))['ledger']
    assert ledger['decision'] == 'deferred'
    assert ledger['unresolved'] == ['filter.value']
    assert '보류' in ledger['questions'][0]['text']
    assert not ledger['unsupported_actions']


def test_target_question_does_not_clear_other_confirmed_or_missing_slots():
    s = state('비용이 큰 행만 골라줘.')
    s['condition_ledger'] = checked(s, proposal(s, {'filter.field': '비용'}))
    s['pending_questions'] = s['condition_ledger']['questions']
    operator = next(q for q in s['pending_questions'] if q['key'] == 'filter.operator')
    s = respond(s, '모르겠어', operator['id'])
    ledger = judge_conditions(s, Fake([]))['ledger']
    assert ledger['resolution']['keys'] == ['filter.operator']
    assert ledger['slots']['filter.field']['value'] == '비용'
    assert set(ledger['unresolved']) == {'filter.operator', 'filter.value'}


def test_fault_takes_priority_over_unknown_and_skips_questions():
    s = respond(waiting(), '모르겠어')
    s['source_health'] = {'db_status': 'timeout'}
    result = judge_conditions(s, Fake([]))
    assert result['ledger']['decision'] == 'system_error'
    assert result['ledger']['questions'] == []


def test_checkpoint_defer_restart_answer_confirmation_and_real_file(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_CONDITION_PLAN_MODE', 'template')
    s = waiting()
    first_raw = proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': None})
    s.pop('condition_ledger'); s.pop('pending_questions')
    store = Storage(tmp_path)
    cfg = {'configurable': {'thread_id': s['session_id']}}
    fake = Fake([first_raw])
    graph, conn = build_graph(store, conversation=lambda _: {}, condition_transport=fake, condition_mode='enforce')
    current = graph.invoke(s, cfg)
    for answer in ['모르겠어', '자료 연결 후 기준 입력', '나중에 결정']:
        current = graph.invoke(Command(resume={'type': 'message', 'content': answer,
                              'question_id': current['pending_questions'][0]['id']}), cfg)
        assert current['status'] == 'needs_clarification'
        assert current['__interrupt__']
        assert not store.agents()
    assert fake.calls == 1
    assert current['condition_ledger']['decision'] == 'deferred'
    conn.close(); store.close()
    # New process connection; answers and pending conditions survive a restart.
    store = Storage(tmp_path)
    later = {**current, 'messages': current['messages'] + [{'role': 'user', 'content': '100만원 초과로 해줘.'}]}
    graph, conn = build_graph(store, conversation=lambda _: {}, condition_transport=Fake([proposal(later, {'filter.value': '1000000'})]), condition_mode='enforce')
    current = graph.invoke(Command(resume={'type': 'message', 'content': '100만원 초과로 해줘.'}), cfg)
    assert current['status'] == 'ready'
    assert current['condition_ledger']['slots']['missing.policy']['value'] == 'error'
    assert not store.agents()
    saved = graph.invoke(Command(resume={'type': 'confirm'}), cfg)
    agent_id = saved['saved_agent_id']
    assert saved['status'] == 'saved'
    conn.close(); store.close()
    with pytest.raises(ValueError, match='실행 전 자료 확인'):
        handle({'directory': str(tmp_path), 'action': 'test', 'id': agent_id, 'content': '실행'})
    store = Storage(tmp_path)
    assert store.tests(agent_id) == []
    store.add_asset({'id': 'table', 'name': '비용.csv', 'kind': 'table', 'columns': ['비용']},
                    [{'비용': 1000000}, {'비용': 1500000}])
    store.close()
    run = handle({'directory': str(tmp_path), 'action': 'test', 'id': agent_id, 'content': '실행', 'file_ids': ['table']})
    assert run['status'] == 'succeeded'
    expected = [{'비용': 1500000}]
    assert run['rows'] == expected
    assert json.loads((tmp_path / run['report_path']).read_text()) == expected


def test_execution_file_absence_does_not_block_filter_design():
    s = state(); s['condition_ledger'] = checked(s, proposal(s))
    req = design_requirements(s)[0]
    assert s['condition_ledger']['decision'] == 'ready'
    assert req['timing'] == 'execution' and not req['ready'] and req['fields'] == ['비용']


def test_schedule_file_is_needed_during_design():
    s = state('한국 시간 매일 8시 0분에 실행해줘.')
    raw = proposal(s, {'schedule.frequency': 'daily', 'schedule.hour': '8', 'schedule.minute': '0', 'schedule.timezone': 'Asia/Seoul'}, ['schedule'])
    s['condition_ledger'] = checked(s, raw)
    assert s['condition_ledger']['decision'] == 'clarify'
    assert s['condition_ledger']['questions'][0]['key'] == 'input.table'
    assert design_requirements(s)[0]['timing'] == 'design'
    s['resources'] = [{'id': 'table', 'kind': 'table', 'columns': ['비용']}]
    assert checked(s, raw)['decision'] == 'ready'


@pytest.mark.parametrize('kind', ['missing', 'wrong_columns', 'partial', 'multiple'])
def test_preflight_rejects_missing_wrong_and_partial_files(tmp_path, kind):
    store = Storage(tmp_path)
    config = workflow_draft(state())
    files = []
    if kind != 'missing':
        asset = {'id': 'table', 'kind': 'table', 'name': 'data.csv', 'columns': ['다른열'] if kind == 'wrong_columns' else ['비용']}
        if kind == 'partial': asset['parser_status'] = 'partial'
        store.add_asset(asset, [])
        files = ['table']
        if kind == 'multiple':
            store.add_asset({**asset, 'id': 'table2'}, [])
            files.append('table2')
    try:
        with pytest.raises(ValueError, match='실행 전 자료 확인'): preflight_execution(config, store, files)
    finally: store.close()


def test_preflight_matches_runtime_override_and_checks_comparison_column(tmp_path):
    store = Storage(tmp_path)
    config = workflow_draft(state())
    config['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['compare_field'] = '예산'
    for id, columns in [('old', ['비용']), ('new', ['비용', '예산'])]:
        store.add_asset({'id': id, 'kind': 'table', 'name': id, 'columns': columns}, [])
    config['workflow']['nodes'][0]['settings']['file_ids'] = ['old']
    req = preflight_execution(config, store, ['new'])[0]
    assert req['file_ids'] == ['new'] and req['fields'] == ['비용', '예산']
    store.close()


def test_conditional_branch_file_is_deferred_to_runtime(tmp_path):
    store = Storage(tmp_path)
    config = workflow_draft(state())
    config['workflow'] = {'entry': 'check', 'nodes': [node('check', 'condition'), node('read', 'read_table')],
                          'edges': [edge('check', 'read', 'yes')]}
    req = preflight_execution(config, store)
    assert not req[0]['required_before_start'] and not req[0]['ready']
    store.close()


def test_bound_child_table_does_not_require_a_second_file(tmp_path):
    store = Storage(tmp_path)
    config = workflow_draft(state())
    child = node('child', 'saved_workflow', [bind('table', 'read', 'table')])
    config['workflow'] = {'entry': 'read', 'nodes': [config['workflow']['nodes'][0], child], 'edges': [edge('read', 'child')]}
    store.add_asset({'id': 'table', 'kind': 'table', 'name': 'data.csv', 'columns': ['비용']}, [])
    req = execution_requirements(config, store, ['table'], {'child': {'config': workflow_draft(state())}})
    assert [r['node_id'] for r in req] == ['read']
    store.close()


def test_review_change_rechecks_conditions_before_new_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_CONDITION_PLAN_MODE', 'template')
    s = state()
    after = {**s, 'messages': s['messages'] + [{'role': 'user', 'content': '기준은 200만원 초과로 바꿔줘.'}]}
    fake = Fake([proposal(s), proposal(after, {'filter.value': '2000000'})])
    store = Storage(tmp_path)
    graph, conn = build_graph(store, conversation=lambda _: {}, condition_transport=fake, condition_mode='enforce')
    cfg = {'configurable': {'thread_id': s['session_id']}}
    try:
        first = graph.invoke(s, cfg)
        assert first['status'] == 'ready' and not store.agents()
        changed = graph.invoke(Command(resume={'type': 'message', 'content': '기준은 200만원 초과로 바꿔줘.'}), cfg)
        assert fake.calls == 2
        assert changed['condition_ledger']['slots']['filter.value']['value'] == 2000000
        assert changed['draft']['execution_plan']['filters'][0]['value'] == 2000000
        assert changed['condition_ledger']['slots']['missing.policy']['value'] == 'error'
        assert not changed['confirmed'] and not store.agents()
        saved = graph.invoke(Command(resume={'type': 'confirm'}), cfg)
        assert saved['status'] == 'saved'
        assert store.agent(saved['saved_agent_id'])['config']['execution_plan']['filters'][0]['value'] == 2000000
    finally:
        conn.close(); store.close()


def invalid_update():
    return {'operation': 'filter', 'conditions': [{'key': 'filter.value', 'status': 'specified', 'value': None, 'evidence_ids': []}], 'unsupported_actions': []}


def test_invalid_condition_contract_gets_one_specific_repair_with_original_grounding():
    s = state(); s['condition_ledger'] = checked(s, proposal(s))
    s['messages'].append({'role': 'user', 'content': '기준은 200만원 초과로 바꿔줘.'})
    fake = Fake([invalid_update(), proposal(s, {'filter.value': '2000000'})])
    result = judge_conditions(s, fake, max_contract_repairs=1)
    assert fake.calls == result['model_calls'] == 2
    assert result['ledger']['decision'] == 'ready'
    assert result['ledger']['slots']['filter.value']['value'] == 2000000
    assert result['ledger']['slots']['missing.policy']['value'] == 'error'
    assert result['attempts'][0]['contract_error']
    assert '규격 오류만 수정' in result['messages'][-1]['content']
    assert '2000000' not in result['messages'][-1]['content']


def test_exhausted_contract_repair_preserves_accepted_conditions_and_blocks_save():
    s = state(); s['condition_ledger'] = checked(s, proposal(s))
    s['messages'].append({'role': 'user', 'content': '기준은 200만원 초과로 바꿔줘.'})
    fake = Fake([invalid_update(), invalid_update()])
    update = make_condition_check(transport=fake, mode='enforce')(s)
    assert fake.calls == 2
    assert update['condition_ledger']['decision'] == 'system_error'
    assert update['condition_ledger']['slots']['filter.value']['value'] == 1000000
    assert update['condition_ledger']['validated_user_turn'] == 0
    assert update['status'] == 'validation_failed' and not update['confirmed']
    assert update['condition_route'] == 'stop'


def test_failed_update_can_use_its_original_user_evidence_after_generic_retry():
    s = state(); s['condition_ledger'] = checked(s, proposal(s))
    s['messages'] += [{'role': 'user', 'content': '기준은 200만원 초과로 바꿔줘.'},
                      {'role': 'assistant', 'content': '모델 응답 오류로 보류했습니다.'},
                      {'role': 'user', 'content': '다시 시도해줘.'}]
    raw = proposal(s, {'filter.value': '2000000'})
    raw['conditions'][0]['evidence_ids'] = ['user:1:0']
    result = checked(s, raw)
    assert result['decision'] == 'ready'
    assert result['slots']['filter.value']['value'] == 2000000
    assert result['validated_user_turn'] == 3


def test_condition_contract_repair_is_bounded_and_success_needs_no_extra_call():
    s = state(); fake = Fake([invalid_update(), invalid_update()])
    with pytest.raises(ConditionContractError) as error:
        judge_conditions(s, fake, max_contract_repairs=1)
    assert fake.calls == error.value.model_calls == 2
    assert judge_conditions(s, Fake([proposal(s)]), max_contract_repairs=1)['model_calls'] == 1
    with pytest.raises(ValueError, match='0~1'):
        judge_conditions(s, Fake([]), max_contract_repairs=2)
