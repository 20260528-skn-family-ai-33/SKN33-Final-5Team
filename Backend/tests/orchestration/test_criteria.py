"""Orchestration stage 2: code asks for a missing selection criterion even when the model says ready."""
import json

import pytest

from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_clarification_turns import Fake, assessment, document_planner, ORIGINAL

VAGUE = {'selections': [{'request_part': '위험한 조항만', 'target': '위험한 조항', 'criterion': '위험한', 'criterion_kind': 'vague'}]}


def concrete(criterion):
    return {'selections': [{'request_part': '위험한 조항만', 'target': '위험한 조항', 'criterion': criterion, 'criterion_kind': 'concrete'}]}


@pytest.fixture
def service(tmp_path, monkeypatch):
    import service.handlers.design as worker
    original = worker.build_graph
    box = {'cap': None, 'extract': None, 'mode': 'enforce'}
    def wrapped(*args, **kwargs):
        monkeypatch.setenv('A4B_ORCHESTRATION_MODE', box['mode'])
        kwargs.update(capability_mode='enforce', capability_transport=box['cap'], condition_mode='off', verifier_mode='off',
                      orchestration_transport=box['extract'])
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, 'build_graph', wrapped)
    session = handle({'directory': str(tmp_path), 'action': 'new_session', 'model': 'test', 'credential_id': uid()})
    current = {'s': session}
    def act(name, planner=None, **fields):
        result = handle({'directory': str(tmp_path), 'id': current['s']['id'], 'revision': current['s']['revision'],
                         'action': name, **fields}, conversation=planner)
        current['s'] = result
        return result
    def saved():
        store = Storage(tmp_path)
        try: return len(store.agents())
        finally: store.close()
    return box, act, saved


def test_vague_criterion_is_asked_although_the_model_said_ready_and_the_answer_settles_it(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL), assessment(ORIGINAL)])
    box['extract'] = Fake([VAGUE, VAGUE])  # the model misses the answer on the second turn too
    calls = []
    first = act('message', document_planner(calls, ORIGINAL), content=ORIGINAL)
    assert first['status'] == 'needs_clarification' and calls == []
    q = first['pending_questions'][0]
    assert q['source'] == 'orchestration' and '위험한 조항' in q['text']
    with pytest.raises(ValueError):
        act('confirm')
    answered = act('message', document_planner(calls, ORIGINAL), content='위약금이 있는 조항을 위험으로 봐줘', question_id=q['id'])
    assert answered['status'] == 'ready' and calls == ['위약금이 있는 조항을 위험으로 봐줘'] and not answered['pending_questions']
    assert saved() == 0 and act('confirm')['status'] == 'saved'


def test_delegation_is_not_a_criterion_and_the_question_id_is_stable(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL), assessment(ORIGINAL)])
    box['extract'] = Fake([VAGUE, VAGUE])
    first = act('message', document_planner([], ORIGINAL), content=ORIGINAL)
    again = act('message', document_planner([], ORIGINAL), content='그냥 알아서 해줘')
    assert again['status'] == 'needs_clarification'
    assert [q['id'] for q in again['pending_questions']] == [q['id'] for q in first['pending_questions']]


def test_grounded_concrete_criterion_needs_no_question(service):
    box, act, saved = service
    request = '계약서를 검토하고 위약금이 있는 조항만 근거를 붙여 답해줘'
    box['cap'] = Fake([assessment(request)])
    box['extract'] = Fake([{'selections': [{'request_part': '위약금이 있는 조항만', 'target': '조항', 'criterion': '위약금이 있는', 'criterion_kind': 'concrete'}]}])
    calls = []
    result = act('message', document_planner(calls, request), content=request)
    assert result['status'] == 'ready' and len(calls) == 1


def test_invented_criterion_is_not_trusted(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([concrete('손해배상 금액이 1억 이상인')])  # not in the user's words
    result = act('message', document_planner([], ORIGINAL), content=ORIGINAL)
    assert result['status'] == 'needs_clarification' and result['pending_questions'][0]['source'] == 'orchestration'


def test_selection_not_in_the_request_is_dropped(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([{'selections': [{'request_part': '중요한 메일만', 'target': '메일', 'criterion': None, 'criterion_kind': 'none'}]}])
    calls = []
    assert act('message', document_planner(calls, ORIGINAL), content=ORIGINAL)['status'] == 'ready' and len(calls) == 1


def test_side_question_keeps_the_criterion_question(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([VAGUE])
    first = act('message', document_planner([], ORIGINAL), content=ORIGINAL)
    side = act('message', document_planner([], ORIGINAL), content='오늘 날씨 어때?')
    assert [q['id'] for q in side['pending_questions']] == [q['id'] for q in first['pending_questions']]
    assert box['extract'].calls == 1


@pytest.mark.parametrize('mode', ['off', 'shadow'])
def test_off_makes_no_call_and_shadow_records_without_asking(service, mode):
    box, act, saved = service
    box['mode'] = mode
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([VAGUE])
    calls = []
    result = act('message', document_planner(calls, ORIGINAL), content=ORIGINAL)
    assert result['status'] == 'ready' and len(calls) == 1
    assert box['extract'].calls == (0 if mode == 'off' else 1)


def test_extraction_failure_keeps_the_existing_path(service):
    box, act, saved = service
    class Broken:
        schema = None; calls = 0
        def invoke(self, messages):
            Broken.calls += 1
            return {'content': '{"selections": ['}
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Broken()
    calls = []
    result = act('message', document_planner(calls, ORIGINAL), content=ORIGINAL)
    assert result['status'] == 'ready' and len(calls) == 1


def test_single_table_requests_stay_with_the_condition_checker(service):
    box, act, saved = service
    request = '매출 표에서 금액이 큰 행만 남겨서 저장해줘'
    table = dict(decision='existing_tools', tool_ids=['read_table', 'filter_sort', 'write_result'], missing_conditions=[],
                 missing_actions=[], reason='t', user_quotes=[request])
    box['cap'] = Fake([table])
    box['extract'] = Fake([VAGUE])
    act('message', document_planner([], request), content=request)
    assert box['extract'].calls == 0


def test_ungrounded_target_is_not_shown_to_the_user(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([{'selections': [{'request_part': '위험한 조항만', 'target': '자료와 조건을 확인해 주세요',
                                            'criterion': None, 'criterion_kind': 'none'}]}])
    result = act('message', document_planner([], ORIGINAL), content=ORIGINAL)
    text = result['pending_questions'][0]['text']
    assert '자료와 조건' not in text and '위험한 조항만' in text


@pytest.mark.parametrize('request_text,part,target,kind,asks', [
    ('계약서에서 위약금 조항이 있는 부분만 찾아서 원문 근거와 함께 알려줘', '위약금 조항이 있는 부분', '위약금 조항', 'concrete', False),
    ('규정 문서에서 개인정보 관련 조항만 뽑아 표로 정리해줘', '개인정보 관련 조항', '개인정보 관련 조항', 'none', False),
    ('계약서를 검토하고 위험한 조항만 근거를 붙여 답해줘', '위험한 조항만', '위험한 조항', 'concrete', True),
    ('재고 표에서 재고가 부족한 품목만 알려줘', '재고가 부족한 품목', '재고가 부족한 품목', 'none', True),
])
def test_selection_that_names_its_items_concretely_is_not_asked(request_text, part, target, kind, asks):
    # Extractions observed from RunPod qwen3.5:4b (2026-11-11): the criterion field was left empty.
    from agent_builder.orchestration.criteria import decide, Extraction
    s = {'messages': [{'role': 'user', 'content': request_text}]}
    raw = {'selections': [{'request_part': part, 'target': target, 'criterion': None, 'criterion_kind': kind}]}
    assert bool(decide(s, Extraction.model_validate(raw))['questions']) is asks


BOILERPLATE = {'selections': [{'request_part': '자료와 조건을 확인해 주세요', 'target': '자료와 조건을 확인해 주세요',
                               'criterion': None, 'criterion_kind': 'none'}]}


def test_ungrounded_extraction_is_retried_once_with_the_evidence(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([BOILERPLATE, VAGUE])
    result = act('message', document_planner([], ORIGINAL), content=ORIGINAL)
    assert box['extract'].calls == 2 and result['status'] == 'needs_clarification'
    assert '원문에 없어 무효' in box['extract'].seen[1][-1]['content']


def test_repeated_ungrounded_extraction_is_recorded_not_turned_into_a_question(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL)])
    box['extract'] = Fake([BOILERPLATE, BOILERPLATE])
    calls = []
    result = act('message', document_planner(calls, ORIGINAL), content=ORIGINAL)
    assert box['extract'].calls == 2 and not result['pending_questions'] and len(calls) == 1
    assert any(t.get('extraction_failure') == 'ungrounded_extraction' for t in result['trace'])


def test_answer_settles_the_single_pending_criterion_even_if_the_target_is_renamed(service):
    box, act, saved = service
    request = '중요한 메일만 골라서 요약해줘'
    renamed = {'selections': [{'request_part': '중요한 메일만', 'target': '메일', 'criterion': None, 'criterion_kind': 'vague'}]}
    first_turn = {'selections': [{'request_part': '중요한 메일만', 'target': '중요한 메일', 'criterion': '중요한', 'criterion_kind': 'vague'}]}
    box['cap'] = Fake([assessment(request), assessment(request)])
    box['extract'] = Fake([first_turn, renamed])
    first = act('message', document_planner([], request), content=request)
    assert first['status'] == 'needs_clarification'
    answered = act('message', document_planner([], request), content='대표님이 보낸 메일만 중요한 메일로 봐줘')
    assert answered['status'] == 'ready' and not answered['pending_questions']


def test_answer_to_the_capability_question_about_the_same_criterion_is_accepted(service):
    box, act, saved = service
    request = '중요한 메일만 골라서 요약해줘'
    asked = dict(decision='clarify', tool_ids=['read_document', 'search_document', 'compose_answer'],
                 missing_conditions=['중요한 메일을 선별할 기준'], missing_actions=[], reason='t', user_quotes=[request])
    renamed = {'selections': [{'request_part': '중요한 메일만', 'target': '메일', 'criterion': None, 'criterion_kind': 'vague'}]}
    box['cap'] = Fake([asked, assessment(request)])
    box['extract'] = Fake([renamed])
    first = act('message', document_planner([], request), content=request)
    assert first['pending_questions'][0]['source'] == 'capability'
    answered = act('message', document_planner([], request), content='대표님이 보낸 메일만 중요한 메일로 봐줘')
    assert answered['status'] == 'ready' and not answered['pending_questions']


def test_existence_condition_observed_on_runpod_is_not_asked():
    # RunPod final664 extraction for C03 (2026-11-11): the criterion field was empty.
    from agent_builder.orchestration.criteria import decide, Extraction
    text = '계약서에서 해지 조항을 찾고, 위약금이 있으면 금액을 뽑아서 표로 정리한 다음 검토 의견을 작성해줘'
    raw = {'selections': [{'request_part': '계약서에서 해지 조항을 찾고', 'target': '해지 조항', 'criterion': None, 'criterion_kind': 'none'},
                          {'request_part': '위약금이 있으면 금액을 뽑아서', 'target': '위약금', 'criterion': None, 'criterion_kind': 'concrete'}]}
    assert decide({'messages': [{'role': 'user', 'content': text}]}, Extraction.model_validate(raw))['questions'] == []


@pytest.mark.parametrize('reply,settles', [
    ('대표님이 보낸 메일만 중요한 메일로 봐줘', True),
    ('날씨 좀 알려줘', False), ('점심 뭐 먹지', False), ('잘 모르겠네요', False), ('나중에 정할게요', False)])
def test_only_a_reply_about_the_selection_settles_its_criterion(service, reply, settles):
    box, act, saved = service
    request = '중요한 메일만 골라서 요약해줘'
    vague = {'selections': [{'request_part': '중요한 메일만', 'target': '중요한 메일', 'criterion': '중요한', 'criterion_kind': 'vague'}]}
    box['cap'] = Fake([assessment(request)] * 3)
    box['extract'] = Fake([vague] * 3)
    act('message', document_planner([], request), content=request)
    result = act('message', document_planner([], request), content=reply)
    assert (result['status'] == 'ready') is settles and bool(result['pending_questions']) is (not settles)
