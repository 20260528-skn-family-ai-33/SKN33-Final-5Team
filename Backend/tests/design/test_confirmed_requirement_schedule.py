"""Public-9B M05 candidate and explicit user edits at the extraction boundary."""
from copy import deepcopy

from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint
from tests.conditions.test_condition_readiness import checked, proposal, state
from tests.design.test_condition_requirement_ownership import TABLE, packet


def schedule_state():
    current = state('한국 시간으로 매주 오전 9시 정각에 표를 JSON으로 저장해줘.')
    current['resources'] = [deepcopy(TABLE)]
    initial = checked(current, proposal(current, {}, ['schedule']))
    assert initial['decision'] == 'clarify'
    current.update(condition_ledger=initial, pending_questions=initial['questions'])
    current['messages'] += [dict(role='assistant', content=initial['questions'][0]['text']),
                            dict(role='user', content='월요일로 해줘.')]
    accepted = checked(current, proposal(current, {}, ['schedule']))
    assert accepted['decision'] == 'ready' and accepted['slots']['schedule.weekday']['value'] == 0
    current.update(condition_enforced=True, condition_required=True, condition_ledger=accepted,
                   pending_questions=[])
    return current


def test_m05_tuesday_proposal_uses_confirmed_monday_then_explicit_edit_only():
    current = schedule_state()
    previous = ledger_fingerprint(current)
    schedule = dict(frequency='weekly', timezone='Asia/Seoul', hour=9, minute=0,
                    weekday=1, monthday=None, month_end=False)
    raw = packet(dict(kind='schedule', schedule=schedule), dict(kind='export', format='json'))
    first = extract_plan(raw, current)
    assert first['tasks'][0]['spec']['schedule']['weekday'] == 0
    current['messages'].append(dict(role='user', content='예약 요일을 금요일로 바꿔줘.'))
    current['condition_ledger'] = checked(current, proposal(current, {}, ['schedule']))
    assert previous != ledger_fingerprint(current)
    edited = extract_plan(raw, current)
    assert edited['tasks'][0]['spec']['schedule'] == {**schedule, 'weekday': 4}
    assert edited['tasks'][-1]['spec'] == {'kind': 'export', 'format': 'json'}


def test_weekday_in_output_filename_does_not_change_the_confirmed_schedule():
    current = schedule_state()
    current['messages'].append(dict(role='user', content='출력 파일 이름을 화요일 보고서로 해줘.'))
    current['condition_ledger'] = checked(current, proposal(current, {}, ['schedule']))
    assert current['condition_ledger']['slots']['schedule.weekday']['value'] == 0
