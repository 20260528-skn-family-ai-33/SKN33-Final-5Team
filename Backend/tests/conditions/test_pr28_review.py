"""PR #28 review findings (P1·P2·P2) and one follow-up gap, with fixed model answers.

Each case passed on main 9cebf9c and failed on 06e1a2d; switching off only the matching new
code path (literal schedule reading, monthly word check, short-reply reuse) restored it.
"""
import pytest
from agent_builder.conditions.condition_readiness import validate_conditions, source_catalog
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.capabilities.capability_gate import input_fingerprint
from tests.conditions.test_condition_readiness import state, Fake

TABLE = {'id': 't', 'kind': 'table', 'columns': ['비용'], 'row_count': 3, 'version': '1', 'profile': {}}
DAILY = {'schedule.frequency': 'daily', 'schedule.minute': '0', 'schedule.timezone': 'Asia/Seoul'}


def ledger(turns, model, operations=('schedule',)):
    s = state(turns[0]); s['resources'] = [TABLE]
    for t in turns[1:]:
        s['messages'] += [{'role': 'assistant', 'content': '확인했습니다.'}, {'role': 'user', 'content': t}]
    catalog, _ = source_catalog(s)
    last = next(e['id'] for e in reversed(catalog) if e['source_id'] == 'user')
    raw = {'operations': list(operations), 'unsupported_actions': [], 'conditions': [
        {'key': k, 'status': 'specified' if v is not None else 'missing', 'value': v, 'evidence_ids': [last] if v is not None else []}
        for k, v in model.items()]}
    r = validate_conditions(raw, s, catalog)
    return r['decision'], {k.split('.')[1]: v['value'] for k, v in r['slots'].items() if k.startswith('schedule')}, r['unsupported_actions']


@pytest.mark.parametrize('reply', ['오후 6시로 바꿔줘', '18시로 해줘'])
def test_p1_a_corrected_time_wins_over_the_first_request(reply):
    decision, slots, _ = ledger(['한국 시간 매일 오전 9시에 JSON으로 저장해줘.', reply], {**DAILY, 'schedule.hour': '18'})
    assert decision == 'ready' and slots['hour'] == 18 and slots['timezone'] == 'Asia/Seoul'


@pytest.mark.parametrize('model_hour', ['18', None])
def test_follow_up_ambiguous_time_is_asked_never_kept(model_hour):
    # "6시로" may be 06:00 or 18:00; the old 9 must not be confirmed silently.
    decision, slots, _ = ledger(['한국 시간 매일 오전 9시에 JSON으로 저장해줘.', '6시로 바꿔줘'], {**DAILY, 'schedule.hour': model_hour})
    assert decision == 'clarify' and slots['hour'] is None


def test_p1_weekday_only_change():
    decision, slots, _ = ledger(['한국 시간 매주 월요일 오전 9시에 JSON으로 저장해줘.', '화요일로 바꿔줘'],
                                {**DAILY, 'schedule.frequency': 'weekly', 'schedule.weekday': '1', 'schedule.hour': '9'})
    assert decision == 'ready' and slots['weekday'] == 1


@pytest.mark.parametrize('text,frequency', [('매월은 필요 없어. 한국 시간 매일 오전 9시에 JSON으로 저장해줘', 'daily'),
                                            ('매달 말고 한국 시간 매주 월요일 오전 9시에 JSON으로 저장해줘', 'weekly')])
def test_p2_negated_monthly_is_not_a_monthly_request(text, frequency):
    model = {**DAILY, 'schedule.frequency': frequency, 'schedule.hour': '9', **({'schedule.weekday': '0'} if frequency == 'weekly' else {})}
    decision, slots, unsupported = ledger([text], model)
    assert decision == 'ready' and slots['frequency'] == frequency and unsupported == []


def test_p2_real_monthly_stays_unsupported():
    decision, _, unsupported = ledger(['한국 시간 매월 1일 오전 9시에 JSON으로 저장해줘'], {**DAILY, 'schedule.frequency': 'weekly', 'schedule.hour': '9'})
    assert decision == 'unsupported' and unsupported == ['월간 예약']


def after_refusal(reply):
    s = state('JSON으로 저장하고 슬랙으로 보내줘'); s['resources'] = [TABLE]; q = s['messages'][0]['content']
    s['capability_assessment'] = {'decision': 'missing_capability', 'tool_ids': ['read_table'], 'missing_conditions': [],
                                  'missing_actions': ['외부 슬랙 전송'], 'reason': 'r', 'user_quotes': [q]}
    s['capability_fingerprint'] = input_fingerprint(s)
    s['messages'] += [{'role': 'assistant', 'content': '슬랙 전송은 지원하지 않습니다.'}, {'role': 'user', 'content': reply}]
    fake = Fake([{'decision': 'existing_tools', 'tool_ids': ['read_table'], 'missing_conditions': [], 'missing_actions': [],
                  'reason': 'r', 'user_quotes': [q]}] * 2)
    return fake, make_capability_check('fixture', fake, mode='enforce')(s)['capability_assessment']


@pytest.mark.parametrize('reply', ['슬랙은 필요 없어', '그럼 슬랙 없이', '전송은 안 해도 돼', '슬랙'])
def test_p2_withdrawing_the_refused_action_is_judged_again(reply):
    fake, result = after_refusal(reply)
    assert fake.calls == 1 and result['decision'] == 'existing_tools'


@pytest.mark.parametrize('reply', ['네', '모두 유지', '그만할래', '알겠어요'])
def test_plain_replies_still_keep_the_refusal_without_a_model_call(reply):
    fake, result = after_refusal(reply)
    assert fake.calls == 0 and result['decision'] == 'missing_capability'
