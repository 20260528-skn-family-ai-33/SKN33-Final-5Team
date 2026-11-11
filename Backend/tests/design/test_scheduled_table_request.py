from copy import deepcopy
import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.requirement_ledger import ledger_fingerprint, make_requirement_check, plan_digest
from tests.conditions.test_condition_readiness import Fake, checked, proposal, state


TABLE = {'id': 'evidence-table', 'kind': 'table', 'name': 'report.csv', 'columns': ['units'], 'version': '1'}


def ready_state(initial, answer=None, key=None):
    current = state(initial)
    current.update(resources=[deepcopy(TABLE)], condition_required=True, condition_enforced=True)
    first = checked(current, proposal(current, {}, ['schedule']))
    if answer:
        question = next(q for q in first['questions'] if q['key'] == key)
        current.update(condition_ledger=first, pending_questions=first['questions'])
        current['messages'] += [{'role':'assistant', 'content':question['text']},
                                {'role':'user', 'content':answer}]
        current['condition_answers'] = [{'turn':2,'answer':answer,'candidate_keys':[key],
                                          'question_ids':[question['id']]}]
        first = checked(current, proposal(current, {}, ['schedule']))
    assert first['decision'] == 'ready'
    current.update(condition_ledger=first, pending_questions=[])
    return current


@pytest.mark.parametrize(('prefix','answer','key','slot','value'),[
    ('매일', '매일 오전 9시 정각으로 해줘.', 'schedule.hour', 'hour', 9),
    ('매주 오전 9시에', '매주 월요일로 해줘.', 'schedule.weekday', 'weekday', 0),
    ('매월 오전 9시에', '매월 15일로 해줘.', 'schedule.monthday', 'monthday', 15),
    ('매일 오후 6시에', None, None, 'hour', 18),
])
def test_whole_scheduled_export_is_a_zero_call_grounded_plan(prefix, answer, key, slot, value):
    current = ready_state(f'한국 시간으로 {prefix} 표를 CSV로 저장해줘.', answer, key)
    transport = Fake([])
    update = make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 0 and update['requirement_route'] == 'plan'
    tasks = update['requirement_ledger']['tasks']
    assert [t['spec']['kind'] for t in tasks] == ['read','schedule','export']
    assert tasks[0]['spec']['file_ids'] == [TABLE['id']]
    assert tasks[1]['spec']['schedule'][slot] == value
    assert tasks[1]['quote'] == (answer or current['messages'][0]['content'])
    assert tasks[2]['depends_on'] == [tasks[0]['id'], tasks[1]['id']]
    assert tasks[2]['spec']['format'] == 'csv'
    assert update['trace'][-1]['model_calls'] == 0


def test_checkpoint_refresh_and_save_revalidate_the_actual_resource_and_schedule():
    current = ready_state('한국 시간으로 매일 오전 9시에 표를 CSV로 저장해줘.')
    check = make_requirement_check('fixture', Fake([]), 'enforce')
    current.update(check(current))
    current.update(compile_conditions(current))
    with pytest.raises(ValueError):
        require_save_ready(current)
    current['confirmed'] = True
    require_save_ready(json.loads(json.dumps(current)))
    old = current['requirement_ledger']
    opaque = {**old['tasks'][0], 'id':'r4','spec':{'kind':'other','action':'opaque_candidate'}}
    current['requirement_ledger'] = {**old, 'tasks': [*old['tasks'],opaque]}
    current['requirement_digest'] = plan_digest(current['requirement_ledger'])
    current.update(check(current))
    assert current['requirement_ledger'] == old
    current['resources'][0]['version'] = '2'
    assert ledger_fingerprint(current) != current['requirement_fingerprint']
    assert check(current)['requirement_route'] == 'stop'
    with pytest.raises(ValueError):
        require_save_ready(current)


def test_matching_checked_cache_preserves_the_saved_agent_and_confirmation():
    current = ready_state('한국 시간으로 매일 오전 9시에 표를 CSV로 저장해줘.')
    transport = Fake([])
    check = make_requirement_check('fixture', transport, 'enforce')
    current.update(check(current))
    current.update(confirmed=True, saved_agent_id='already-saved')
    old_trace = deepcopy(current['trace'])
    current.update(check(current))
    assert current['confirmed'] and current['saved_agent_id'] == 'already-saved'
    assert current['trace'] == old_trace and transport.calls == 0


def test_explicit_revision_never_reuses_the_previous_schedule():
    current = ready_state('한국 시간으로 매일 오전 9시에 표를 CSV로 저장해줘.')
    check = make_requirement_check('fixture', Fake([]), 'enforce')
    current.update(check(current))
    fingerprint = current['requirement_fingerprint']
    current['messages'].append({'role':'user','content':'예약 시각을 오후 6시로 바꿔줘.'})
    current['condition_ledger'] = checked(current, proposal(current, {}, ['schedule']))
    current.update(check(current))
    assert current['requirement_fingerprint'] != fingerprint
    assert current['requirement_ledger']['tasks'][1]['spec']['schedule']['hour'] == 18


def test_schedule_question_survives_restart_and_saved_export_runs_on_the_chosen_table(tmp_path):
    from langgraph.types import Command
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow

    current = state('한국 시간으로 매월 오전 9시에 표를 JSON으로 저장해줘.')
    current['resources'] = [deepcopy(TABLE)]
    config = {'configurable': {'thread_id': current['session_id']}}
    no_requirement_model = Fake([])

    def graph(store):
        return build_graph(store, conversation=compile_conditions, condition_mode='enforce',
                           condition_transport=Fake([proposal(current, {}, ['schedule'])]),
                           requirement_mode='enforce', requirement_transport=no_requirement_model)

    store = Storage(tmp_path)
    store.add_asset(TABLE, [{'units': 4}, {'units': 9}])
    app, conn = graph(store)
    try:
        first = app.invoke(current, config)
        assert first['status'] == 'needs_clarification'
        [question] = first['pending_questions']
        assert question['key'] == 'schedule.monthday'
    finally:
        conn.close()
        store.close()

    store = Storage(tmp_path)
    app, conn = graph(store)
    try:
        second = app.invoke(Command(resume={'type':'message', 'question_id':question['id'],
                                            'content':'매월 15일로 해줘.'}), config)
        assert second['status'] == 'ready' and no_requirement_model.calls == 0
        assert not store.agents()
        saved = app.invoke(Command(resume={'type':'confirm'}), config)
        assert saved['status'] == 'saved' and len(store.agents()) == 1
        assert store.agent(saved['saved_agent_id'])['config']['schedule']['monthday'] == 15
        result = run_workflow(store, saved['saved_agent_id'], '실행', '')
        assert result['status'] == 'succeeded' and result['rows'] == [{'units':4}, {'units':9}]
    finally:
        conn.close()
        store.close()


@pytest.mark.parametrize(('initial','answer','change'),[
    ('한국 시간으로 매일 고객별로 집계한 표를 CSV로 저장해줘.', '오전 9시로 해줘.', None),
    ('한국 시간으로 매일 표를 CSV로 저장해줘. 원본도 따로 저장해줘.', '오전 9시로 해줘.', None),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘. 메일로도 보내줘.', None),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘.', 'two_resources'),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘.', 'multi_answer'),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘.', 'no_event'),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘.', 'library'),
    ('한국 시간으로 매일 표를 CSV로 저장해줘.', '오전 9시로 해줘.', 'saved_edit'),
])
def test_out_of_scope_keeps_the_extractor(initial, answer, change):
    current = ready_state(initial, answer, 'schedule.hour')
    if change == 'two_resources':
        current['resources'].append({**TABLE, 'id':'other-table'})
        current['condition_ledger'] = checked(current, proposal(current, {}, ['schedule']))
    if change == 'multi_answer':
        current['condition_answers'][0]['question_ids'].append('another')
        current['condition_answers'][0]['candidate_keys'].append('schedule.weekday')
    if change == 'no_event':
        current['condition_answers'] = []
    if change == 'library':
        current['required_node_refs'] = ['library-node']
    if change == 'saved_edit':
        current['base_agent_id'] = 'old-agent'
    transport = Fake([{'tasks':[], 'unresolved':[]}])
    make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 1
