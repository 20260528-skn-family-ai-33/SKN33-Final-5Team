"""Cancelling the fixed flow's field question ends it in code, before the capability model.

Live 2026-11-03 (RunPod, B-8): "그만할래" after "어떤 항목을 한 줄씩 뽑을까요?" went to the
capability model and the planner; one run built a 15-node extraction draft (99 s), another
ended as "missing capability". The graph runs attachment_gate -> capability_check -> ... ->
conversation, so the cancel has to be handled at the gate, not inside document_rows_reply.
"""
import pytest
from langgraph.types import Command
from agent_builder.design.document_rows_template import document_rows_cancel_reply, ASK_EXPLANATION, QUESTION, CANCELLED
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial
from tests.design.test_document_rows_template import REQUEST, DOC, no_planner
from tests.conditions.test_condition_readiness import Fake
from tests.capabilities.test_capability_assessment import packet


def asked(answer, resources=(DOC,)):
    return {**initial(), 'resources': list(resources), 'messages': [
        {'role': 'user', 'content': REQUEST}, {'role': 'assistant', 'content': ASK_EXPLANATION + '\n• ' + QUESTION},
        {'role': 'user', 'content': answer}]}


@pytest.mark.parametrize('answer', ['취소', '취소해줘', '그만할래', '그만', '됐어요', '필요 없어', '안 할래요', '아니요, 그만할게.'])
def test_bare_cancel_is_answered_in_code(answer):
    reply = document_rows_cancel_reply(asked(answer))
    assert reply['messages'][-1]['content'] == CANCELLED
    assert reply['draft']['questions'] == [] and reply['trace'][-1]['model_calls'] == 0


@pytest.mark.parametrize('answer', ['다른 걸로 바꿀래', '아니, 요약으로 바꿔줘', '처음부터 다시 할게요', '대신 금액만 뽑아줘',
                                    '상품명, 금액', '취소 여부, 금액', '아니요', '전체 다'])
def test_changes_and_field_answers_keep_the_existing_path(answer):
    assert document_rows_cancel_reply(asked(answer)) is None


def test_cancel_needs_the_fixed_flow_question_and_a_document():
    assert document_rows_cancel_reply(asked('취소', resources=())) is None
    s = asked('취소'); s['messages'][1]['content'] = '어느 열을 기준으로 선별할까요?'
    assert document_rows_cancel_reply(s) is None


def test_cancel_reaches_no_model_with_enforced_capability_check(tmp_path):
    store = Storage(tmp_path)
    s = {**initial(), 'messages': [{'role': 'user', 'content': REQUEST}], 'resources': [DOC]}
    capability = Fake([packet(s, 'existing_tools', tools=['read_document', 'extract_information', 'export_result'])])
    cfg = {'configurable': {'thread_id': s['session_id']}}
    graph, connection = build_graph(store, conversation=no_planner, capability_transport=capability, capability_mode='enforce')
    try:
        first = graph.invoke(s, cfg)
        assert first['draft']['questions'] == [QUESTION] and capability.calls == 1
        done = graph.invoke(Command(resume={'type': 'message', 'content': '그만할래'}), cfg)
        assert capability.calls == 1, '취소 답에 지원 판단 모델을 호출함'
        assert done['messages'][-1]['content'] == CANCELLED and done['draft']['questions'] == []
        assert not store.agents()
    finally:
        connection.close(); store.close()
