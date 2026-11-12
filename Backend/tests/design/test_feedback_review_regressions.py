"""PR #103 review: do not turn processing instructions into column names."""
import pytest

from agent_builder.design.document_rows_template import document_rows_applies, document_rows_reply
from agent_builder.design.request_guards import unary_filter_issue
from agent_builder.design.graph import build_graph
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import draft
from tests.design.test_beta_feedback import request
from tests.design.test_document_rows_template import DOC, asked_state


@pytest.mark.parametrize('text', [
    '영수증을 표로 정리해줘. 추출 항목: 품명, 실입고, 수량 내림차순 정렬',
    '영수증을 표로 정리해줘. 추출 항목: 품명, 실입고, 수량 오름차순',
    '영수증을 표로 정리해줘. 추출 항목: 품명, 실입고, 수량 10 이상',
])
def test_inline_processing_instructions_reach_planner_not_ready_extraction(tmp_path, text):
    state = request(text, resources=[DOC])
    seen = []
    def planner(current):
        seen.append(current['messages'][0]['content'])
        return {'draft':draft(['정렬·선별에 사용할 수량 열을 추출 항목에 포함할까요?'])}
    store = Storage(tmp_path)
    graph, connection = build_graph(store, conversation=planner, capability_mode='off',
                                    condition_mode='off', requirement_mode='off', verifier_mode='off')
    try:
        result = graph.invoke(state, {'configurable':{'thread_id':state['session_id']}})
        assert seen == [text], 'The fixed recipe silently consumed a processing instruction.'
        assert result['status'] == 'needs_clarification'
        assert not document_rows_applies(state) and document_rows_reply(state) is None
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('field,operator,text,valid', [
    ('비고','is_empty','비고가 빈 값인 행만 남겨줘',True),
    ('주문상태','is_empty','비고가 빈 값인 행만 남겨줘',False),
    ('주문상태','is_empty','주문상태는 유지하고 비고가 빈 값인 행만 남겨줘',False),
    ('주문상태','not_empty','비고가 빈 값인 행을 제외해줘',False),
    ('상태','is_empty','주문상태가 빈 값인 행만 남겨줘',False),
    ('비고','is_empty','주문상태 is_empty',False),
    ('주문상태','is_empty','주문상태 is_empty',True),
    ('주문상태','not_empty','비고는 비어있고 주문상태 값이 있는 행만 남겨줘',True),
])
def test_empty_filter_evidence_belongs_to_the_target_column(field, operator, text, valid):
    rule = dict(field=field,operator=operator,value=None,compare_field=None)
    assert (unary_filter_issue(rule,text) is None) is valid


def test_followup_field_answer_does_not_consume_a_sort_instruction():
    state = asked_state('품명, 실입고, 수량 내림차순 정렬')
    assert not document_rows_applies(state)
    assert document_rows_reply(state) is None


def test_field_addition_does_not_rename_a_sort_instruction_into_a_column():
    state = request('영수증을 표로 정리해줘. 추출 항목: 품명, 실입고',resources=[DOC])
    state.update(document_rows_reply(state))
    state['messages'].append(dict(role='user',content='수량 내림차순 정렬도 추가해줘'))
    assert not document_rows_applies(state)
    assert document_rows_reply(state) is None
