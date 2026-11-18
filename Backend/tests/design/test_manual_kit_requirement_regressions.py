"""Service failures: a partial ledger and repeated clarification answers."""
from copy import deepcopy
import base64
import csv
import io
import json
from pathlib import Path

import pytest

from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check
from agent_builder.design.output_selection_request import output_selection, literal_numeric_filters
from tests.design.test_feedback_requirement_extraction import state, packet


REGIONAL_REQUEST = ('주문내역.csv에서 완료 주문만 대상으로 수량 곱하기 단가를 주문금액으로 계산해줘. '
                    '지역별 주문금액 합계와 주문 행 개수를 구해서 열 이름을 지역, 총매출, 주문건수로 해줘. '
                    '지역 오름차순으로 정렬한 뒤 지역별매출.csv로 저장해줘. '
                    '수량이 0인 완료 주문도 주문건수에는 포함해줘.')


def repeated_answer_state():
    current = state()
    answer = current['messages'][-1]['content']
    current['messages'][-1]['content'] = (
        f'기준 값을 포함할까요? → {answer}\n선별할 기준 값은 얼마인가요? → {answer}')
    return current


def test_batch_answer_retains_projection_and_numeric_condition():
    current = repeated_answer_state()
    assert output_selection(current) == ['주문번호', '주문금액']
    assert literal_numeric_filters(current) == literal_numeric_filters(state())
    assert [t['spec'] for t in extract_plan(packet(), current)['tasks']] == [
        t['spec'] for t in extract_plan(packet(), state())['tasks']]


def test_batch_answer_cannot_hide_missing_threshold():
    incomplete = packet()
    incomplete['tasks'][2]['spec']['filters'].pop()
    with pytest.raises(ValueError, match='숫자 선별 조건'):
        extract_plan(incomplete, repeated_answer_state())


def test_named_output_materializes_omitted_export_but_rejects_conflicting_format():
    from agent_builder.design.requirement_ledger import validate_plan
    missing = packet()
    missing['tasks'] = missing['tasks'][:-1]
    projected = extract_plan(missing, state())
    assert projected['tasks'][-1]['spec'] == dict(kind='export', format='csv')
    assert projected['tasks'][-1]['source_message'] == 1
    wrong = packet()
    wrong['tasks'][-1]['spec']['format'] = 'json'
    with pytest.raises(ValueError, match='결과 파일 저장'):
        extract_plan(wrong, state())
    projected['tasks'] = projected['tasks'][:-1]
    with pytest.raises(ValueError, match='결과 파일 저장'):
        validate_plan(projected, state())


def test_format_only_revision_does_not_restore_old_csv_format():
    from agent_builder.design.output_selection_request import literal_output_format
    current = state()
    current['messages'].append(dict(role='user', content='저장 형식은 JSON으로 바꿔줘.'))
    assert output_selection(current) == ['주문번호', '주문금액']
    assert literal_output_format(current) is None


def test_decoder_cannot_end_after_read_when_compute_and_threshold_are_proven():
    from jsonschema import Draft202012Validator
    from agent_builder.design.requirement_ledger import extraction_schema
    from agent_builder.design.output_selection_request import constrain_literal_selection
    base = extraction_schema(2, ['table-source'])
    schema = constrain_literal_selection(base, state())
    assert schema['properties']['tasks']['minItems'] == 3
    assert Draft202012Validator(schema).is_valid(packet())
    incomplete = dict(tasks=packet()['tasks'][:1], unresolved=[])
    assert not Draft202012Validator(schema).is_valid(incomplete)
    assert base['properties']['tasks'].get('minItems', 0) < 3
    for current in ({**state(), 'base_agent_id':'saved'},
                    {**state(), 'messages':[dict(role='user', content='표를 CSV로 저장해줘.')]}):
        assert constrain_literal_selection(base, current)['properties']['tasks'].get('minItems', 0) < 3
    owned = extraction_schema(2, ['table-source'], owned_kinds={'filter'})
    assert constrain_literal_selection(owned, state())['properties']['tasks'].get('minItems', 0) < 3


@pytest.mark.parametrize('threshold,format_', [(0, 'csv'), (75, 'json')])
def test_bound_is_derived_from_other_columns_and_thresholds(threshold, format_):
    from agent_builder.design.requirement_ledger import extraction_schema
    from agent_builder.design.output_selection_request import constrain_literal_selection, literal_output_format
    current = dict(messages=[dict(role='user', content=(
        f'청구액은 개수×판매가, 청구액 {threshold} 이상, 전표·청구액을 청구.{format_}로 저장해줘.'))],
        resources=[dict(id='invoices', name='청구원장.csv', kind='table', columns=['전표','개수','판매가'])])
    schema = constrain_literal_selection(extraction_schema(1, ['invoices']), current)
    assert schema['properties']['tasks']['minItems'] == 3
    assert literal_output_format(current) == format_
    incomplete = dict(tasks=[dict(source_message=0, description='원장 읽기',
        spec=dict(kind='read', file_ids=['invoices']))], unresolved=[])
    with pytest.raises(ValueError):
        extract_plan(incomplete, current)


@pytest.mark.parametrize('second', ['C·D를 second.csv로 저장해줘.', 'A·B를 second.csv로 저장해줘.'])
def test_distinct_output_instructions_remain_ambiguous(second):
    current = state()
    current['messages'][-1]['content'] = 'A·B를 first.csv로 저장해줘.\n' + second
    assert output_selection(current) is None


def test_regional_request_cannot_accept_the_captured_read_only_model_response():
    current = state()
    current['messages'] = [dict(role='user', content=REGIONAL_REQUEST)]
    response = dict(tasks=[dict(source_message=0, description='read_table 업무',
                               spec=dict(kind='read', file_ids=['table-source']))], unresolved=[])
    class Transport:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return {'content': json.dumps(response)}
    transport = Transport()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 2
    assert result['requirement_route'] == 'stop'
    assert result['requirement_ledger'] is None


def test_plain_file_export_and_cancelled_processing_are_not_rejected():
    from agent_builder.design.processing_guard import validate_processing_presence
    current = state()
    plan = dict(tasks=[dict(spec=dict(kind='read', file_ids=['table-source'])),
                       dict(spec=dict(kind='export', format='csv'))], unresolved=[])
    for text in ['주문내역.csv를 CSV로 저장해줘.', '계산하지 말고 CSV로 저장해줘.',
                 '정렬하지 말고 원본을 저장해줘.']:
        current['messages'] = [dict(role='user', content=text)]
        validate_processing_presence(deepcopy(plan), current)


@pytest.mark.parametrize('batch_reply,captured', [(False, False), (True, False), (False, True)])
def test_manual_kit_t14_save_execute_and_csv(batch_reply, captured, tmp_path, monkeypatch):
    """Real kit rows and real backend; model responses fixed, not a live-model claim."""
    import service.handlers.design as worker
    from service.worker import handle
    from tests.design.test_feedback_service_scenarios import (
        ORDER_REQUEST, ORDER_ANSWER, order_draft, order_specs, UnexpectedConditionCall)

    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    captured_response = json.loads(source.with_name('t14_model_response.json').read_text(encoding='utf-8')) if captured else None
    with source.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames
        rows = list(reader)
    build = worker.build_graph
    identifier = None
    class Capabilities:
        def invoke(self, messages):
            return {'content': json.dumps(dict(decision='existing_tools', reason='표 계산과 선별',
                tool_ids=['read_table','calculate_columns','filter_sort','json_transform','export_result'],
                missing_conditions=[], missing_actions=[], user_quotes=[ORDER_REQUEST]))}
    class Requirements:
        def invoke(self, messages):
            payload = json.loads(messages[1]['content'])
            if len(payload['user_messages']) == 1:
                response = dict(tasks=[dict(source_message=0, description='결과 저장',
                    spec=dict(kind='export', format='csv'))], unresolved=[
                    dict(key='filter.value', text='매출 계산식과 높은 주문의 기준을 알려주세요.')])
            else:
                response = (json.loads(json.dumps(captured_response['extraction']).replace('table-source', identifier))
                            if captured else dict(tasks=[dict(source_message=1, description=s['kind'], spec=s)
                                                        for s in order_specs(identifier)], unresolved=[]))
            return {'content': json.dumps(response)}
    monkeypatch.setattr(worker, 'build_graph', lambda *a, **kw: build(*a, **{
        **kw, 'capability_mode':'enforce', 'capability_transport':Capabilities(),
        'requirement_mode':'enforce', 'requirement_transport':Requirements(),
        'condition_mode':'enforce', 'condition_transport':UnexpectedConditionCall(), 'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    result = handle({**base, 'action':'new_session', 'model':'fixture', 'credential_id':'fixture'})
    session_id = result['id']
    result = handle({**base, 'action':'upload', 'id':session_id, 'revision':0,
                     'asset':dict(name='주문내역.csv', kind='table', format='csv', columns=columns), 'rows':rows})
    identifier = result['resources'][0]['id']
    def action(kind, **extra):
        candidate = (json.loads(json.dumps(captured_response['draft']).replace('table-source', identifier))
                     if captured else order_draft(identifier))
        if batch_reply:
            candidate['requirements'][-1]['quote'] = repeated_answer_state()['messages'][-1]['content']
        return handle({**base, 'action':kind, 'id':session_id, 'revision':result['revision'], **extra},
                      conversation=lambda _: dict(draft=candidate))
    result = action('message', content=ORDER_REQUEST)
    assert result['status'] == 'needs_clarification'
    answer = repeated_answer_state()['messages'][-1]['content'] if batch_reply else ORDER_ANSWER
    result = action('message', content=answer, question_id=result['pending_questions'][0]['id'])
    assert result['status'] == 'ready', result.get('issues')
    result = action('confirm')
    assert result['status'] == 'saved', result.get('issues')
    run = handle({**base, 'action':'test', 'id':result['saved_agent_id'], 'content':''})
    assert run['status'] == 'succeeded', run.get('error')
    reader = csv.DictReader(io.StringIO(base64.b64decode(run['artifact']['content']).decode('utf-8-sig')))
    assert reader.fieldnames == ['주문번호', '주문금액']
    assert list(reader) == [{'주문번호':'O006', '주문금액':'300000'},
                            {'주문번호':'O009', '주문금액':'100000'},
                            {'주문번호':'O010', '주문금액':'150000'}]
