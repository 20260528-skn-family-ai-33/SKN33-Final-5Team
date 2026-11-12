"""One-off Gmail summaries from verified slots (team set R01, T01-T07, M11, M12)."""
import json

import pytest

from agent_builder.orchestration.gmail_slots import verify, GmailRequest, assemble, query_for
from infrastructure.persistence.ids import uid
from service.worker import handle
from tests.design.test_clarification_turns import Fake


def slots(read=True, count=(None, None), period=(None, None), zone=(None, None)):
    return {'gmail_read_summary': read, 'count': {'value': count[0], 'quote': count[1]},
            'period': {'value': period[0], 'quote': period[1]}, 'timezone': {'value': zone[0], 'quote': zone[1]}}


def state(*users):
    return {'messages': [{'role': 'user', 'content': u} for u in users]}


@pytest.mark.parametrize('users,raw,expected', [
    (['Gmail에서 최근 5개 메일을 요약 정리해줘.'], slots(count=(5, '최근 5개')), (5, 'recent', None)),
    (['Gmail에서 최신 메일 다섯 통의 내용을 요약해줘.'], slots(count=(5, '다섯 통'), period=('recent', '최신')), (5, 'recent', None)),
    (['Gmail에서 어제 받은 메일을 요약해줘.'], slots(period=('previous_day', '어제')), (100, 'previous_day', None)),
    (['한국 시간 기준으로 Gmail에서 어제 온 메일을 요약해줘.'], slots(period=('previous_day', '어제'), zone=('Asia/Seoul', '한국 시간')),
     (100, 'previous_day', 'Asia/Seoul')),
    (['Gmail에서 최근 5개 메일을 요약해줘.', '아니, 10개로 바꿔줘.'], slots(count=(10, '10개')), (10, 'recent', None)),
    (['Gmail에서 어제 온 메일을 요약해줘.', '최대 5개만 요약해줘.'], slots(count=(5, '최대 5개'), period=('previous_day', '어제')), (5, 'previous_day', None)),
])
def test_verified_slots(users, raw, expected):
    s = state(*users)
    settings = verify(s, GmailRequest.model_validate(raw))
    assert (settings['count'], settings['period'], settings['timezone']) == expected


@pytest.mark.parametrize('users,raw', [
    (['Gmail에서 최근 5개 메일을 요약해줘.'], slots(count=(7, '최근 5개'))),                 # count not written in its quote
    (['Gmail에서 최근 메일을 요약해줘.'], slots(count=(5, '최근 5개'))),                      # quote not in the user's words
    (['Gmail에서 최근 메일을 요약해줘.'], slots(period=('recent', '최근'))),                  # recent needs a count
    (['Gmail에서 최근 5개 메일을 요약해줘.'], slots(count=(5, '5개'), zone=('Mars/Base', '5개'))),  # not a timezone
    (['Gmail에서 최근 5개 메일을 요약해서 보내줘.'], slots(read=False, count=(5, '5개'))),      # not a read-and-summarize request
    (['Gmail에서 최근 600개 메일을 요약해줘.'], slots(count=(600, '600개'))),                 # above the limit
])
def test_unverified_slots_leave_the_request_to_the_planner(users, raw):
    assert verify(state(*users), GmailRequest.model_validate(raw)) is None


def test_assembled_workflow_uses_the_runtime_day_placeholder():
    s = state('한국 시간 기준으로 Gmail에서 어제 온 메일을 요약해줘.')
    settings = verify(s, GmailRequest.model_validate(slots(period=('previous_day', '어제'), zone=('Asia/Seoul', '한국 시간'))))
    draft = assemble(s, settings)
    mail = draft['workflow']['nodes'][0]
    assert mail['tool'] == 'email.list' and json.loads(mail['settings']['arguments_json']) == {
        'max_results': 100, 'query': '{{gmail.previous_day|Asia/Seoul}}'}
    assert query_for({**settings, 'timezone': None}) == '{{gmail.previous_day}}'


@pytest.fixture
def service(tmp_path, monkeypatch):
    import service.handlers.design as worker
    original = worker.build_graph
    box = {}
    def wrapped(*a, **k):
        monkeypatch.setenv('A4B_ORCHESTRATION_MODE', box.get('mode', 'enforce'))
        return original(*a, **{**k, 'capability_mode': 'enforce', 'capability_transport': box['cap'], 'condition_mode': 'off',
                               'verifier_mode': 'off', 'orchestration_transport': box['criteria'], 'gmail_transport': box['gmail']})
    monkeypatch.setattr(worker, 'build_graph', wrapped)
    s = handle({'directory': str(tmp_path), 'action': 'new_session', 'model': 't', 'credential_id': uid()})
    current = {'s': s}
    def act(text, planner):
        current['s'] = handle({'directory': str(tmp_path), 'id': current['s']['id'], 'revision': current['s']['revision'],
                               'action': 'message', 'content': text, 'gmail_connected': True}, conversation=planner)
        return current['s']
    return box, act


def gmail_assessment(text):
    return dict(decision='existing_tools', tool_ids=['email.list', 'compose_answer'], missing_conditions=[], missing_actions=[],
                reason='t', user_quotes=[text])


def test_one_off_previous_day_summary_is_assembled_without_the_planner(service):
    box, act = service
    text = 'Gmail에서 어제 받은 메일을 요약해줘.'
    box.update(cap=Fake([gmail_assessment(text)]), criteria=Fake([{'selections': []}]),
               gmail=Fake([slots(period=('previous_day', '어제'))]))
    calls = []
    result = act(text, lambda s: calls.append(1) or {})
    assert calls == [] and result['status'] == 'ready'
    assert json.loads(result['draft']['workflow']['nodes'][0]['settings']['arguments_json'])['query'] == '{{gmail.previous_day}}'


def test_unverified_extraction_falls_back_to_the_planner(service):
    box, act = service
    text = 'Gmail에서 어제 받은 메일을 요약해줘.'
    box.update(cap=Fake([gmail_assessment(text)]), criteria=Fake([{'selections': []}]),
               gmail=Fake([slots(period=('previous_day', '지난달'))]))
    from tests.design.test_clarification_turns import document_planner
    calls = []
    act(text, document_planner(calls, text))
    assert len(calls) == 1


def test_off_mode_does_not_call_the_slot_extractor(service):
    box, act = service
    text = 'Gmail에서 어제 받은 메일을 요약해줘.'
    box.update(mode='off', cap=Fake([gmail_assessment(text)]), criteria=Fake([]), gmail=Fake([]))
    from tests.design.test_clarification_turns import document_planner
    calls = []
    act(text, document_planner(calls, text))
    assert len(calls) == 1 and box['gmail'].calls == 0


def test_repair_pass_goes_to_the_planner_and_assembly_counts_as_an_attempt(monkeypatch):
    from agent_builder.orchestration.gmail_slots import gmail_slot_reply
    monkeypatch.setenv('A4B_ORCHESTRATION_MODE', 'enforce')
    s = {**state('Gmail에서 어제 받은 메일을 요약해줘.'), 'capability_assessment': gmail_assessment('x'), 'repair_count': 0}
    extractor = Fake([slots(period=('previous_day', '어제'))])
    assert gmail_slot_reply(s, extractor)['repair_count'] == 1
    assert gmail_slot_reply({**s, 'issues': ['요구사항 검증: 건수 불일치']}, Fake([])) is None
