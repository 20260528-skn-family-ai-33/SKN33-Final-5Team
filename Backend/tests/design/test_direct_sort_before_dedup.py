"""Exercise two-stage sorting through the Direct planner's transport schema."""

import csv
import io

import pytest

from agent_builder.design.native_settings import NATIVE_SETTINGS
from agent_builder.design.nodes.direct_conversation import decoder_schema
from tests.design.test_dedup_sort_save_loop import (
    AMOUNT_ASC, CURRENT, LATEST, PRE_FINAL_REQUEST, PRE_FINAL_ROWS, REQUEST, SORT, SORT_FIRST_REQUEST,
    SORT_FIRST_ROWS, TABLE, make_planner, node, service,
)
from tests.design.test_direct_conversation import Fake, planner


class ValidatingTransport(Fake):
    def invoke(self, messages):
        from jsonschema import Draft202012Validator

        Draft202012Validator(self.schema).validate(self.responses[0])
        return super().invoke(messages)


@pytest.mark.parametrize('version,bypasses_dedup',
                         [(f'v{number}', False) for number in range(1, 9)] + [('v8', True)])
def test_direct_sort_before_dedup_is_generated_saved_and_executed(monkeypatch, service, version, bypasses_dedup):
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)
    monkeypatch.setitem(CURRENT, 'request', SORT_FIRST_REQUEST)
    act, saved, fakes = service
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=SORT_FIRST_ROWS)
    candidate = make_planner([], sort=LATEST, sort_first=True)({
        'messages': [{'role': 'user', 'content': SORT_FIRST_REQUEST}],
        'resources': uploaded['resources'],
    })['draft']
    candidate['purpose'] = '정렬한 뒤 중복 제거'
    if bypasses_dedup:
        candidate['workflow']['nodes'][-1]['bindings'][0]['node_id'] = 'sort'
    if version in ('v6', 'v7', 'v8'):
        for node in candidate['workflow']['nodes']:
            node['settings'] = {key: node['settings'][key] for key in (*NATIVE_SETTINGS[node['tool']], 'node_ref')}

    transport = ValidatingTransport(candidate, candidate)
    transport.schema = decoder_schema(version)
    generate = planner(monkeypatch, transport)
    first = act('message', generate, content=SORT_FIRST_REQUEST)
    if bypasses_dedup:
        assert first['status'] == 'validation_failed'
        assert any('중복 제거 단계의 표' in issue for issue in first['issues'])
        with pytest.raises(ValueError, match='필수 조건과 근거 검증'):
            act('confirm', generate)
        assert saved() == 0
        return
    assert first['status'] == 'ready' and not first['issues']
    assert len(transport.calls) == 1
    done = act('confirm', generate)
    assert done['status'] == 'saved' and saved() == 1

    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == [('A1', '2026-11-07'), ('B2', '2026-11-04')]


@pytest.mark.parametrize('version', [f'v{number}' for number in range(1, 9)])
def test_direct_pre_dedup_sort_with_a_result_sort(monkeypatch, service, version):
    """PR #142 review 7: sort, keep the first row, then order the result by another key."""
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)
    monkeypatch.setitem(CURRENT, 'request', PRE_FINAL_REQUEST)
    act, saved, fakes = service
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=PRE_FINAL_ROWS)
    candidate = make_planner([], sort=LATEST, sort_first=True, final_sort=AMOUNT_ASC)({
        'messages': [{'role': 'user', 'content': PRE_FINAL_REQUEST}],
        'resources': uploaded['resources'],
    })['draft']
    candidate['purpose'] = '정렬한 뒤 중복 제거하고 금액순 정렬'
    if version in ('v6', 'v7', 'v8'):
        for node in candidate['workflow']['nodes']:
            node['settings'] = {key: node['settings'][key] for key in (*NATIVE_SETTINGS[node['tool']], 'node_ref')}

    transport = ValidatingTransport(candidate, candidate)
    transport.schema = decoder_schema(version)
    generate = planner(monkeypatch, transport)
    first = act('message', generate, content=PRE_FINAL_REQUEST)
    assert first['status'] == 'ready' and not first['issues']
    done = act('confirm', generate)
    assert done['status'] == 'saved' and saved() == 1
    result = fakes['run'](done['saved_agent_id'])
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == [('B2', '2026-11-04'), ('A1', '2026-11-07')]


@pytest.mark.parametrize('request_text,sort', [
    (REQUEST, SORT),
    ('중복주문.csv에서 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장해줘. 빈 값은 그대로 유지해.', []),
], ids=['unordered-sort', 'dedup-only'])
@pytest.mark.parametrize('bypasses_dedup', [True, False], ids=['bypass', 'connected'])
def test_direct_v1_output_uses_deduplicated_rows(monkeypatch, service, request_text, sort, bypasses_dedup):
    monkeypatch.setenv('A4B_PLANNER_PROMPT', 'v1')
    monkeypatch.setitem(CURRENT, 'request', request_text)
    act, saved, fakes = service
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=SORT_FIRST_ROWS)
    candidate = make_planner([], sort=sort, sort_first=bool(sort))({
        'messages': [{'role': 'user', 'content': request_text}],
        'resources': uploaded['resources'],
    })['draft']
    if bypasses_dedup:
        candidate['workflow']['nodes'][-1]['bindings'][0]['node_id'] = 'sort' if sort else 'read'

    transport = ValidatingTransport(candidate, candidate)
    transport.schema = decoder_schema('v1')
    generate = planner(monkeypatch, transport)
    first = act('message', generate, content=request_text)
    if bypasses_dedup:
        assert first['status'] == 'validation_failed'
        assert any('중복 제거 단계의 표' in issue for issue in first['issues'])
        with pytest.raises(ValueError, match='필수 조건과 근거 검증'):
            act('confirm', generate)
        assert saved() == 0
        return

    assert first['status'] == 'ready' and not first['issues']
    done = act('confirm', generate)
    assert done['status'] == 'saved' and saved() == 1
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == [('A1', '2026-11-01'), ('B2', '2026-11-04')]


AMOUNT_DESC = [{**AMOUNT_ASC[0], 'direction': 'descending'}]
CHANGE_RESULT = '결과는 금액 내림차순으로 바꿔줘.'
CANCEL_RESULT = '결과 정렬만 빼줘.'
LATEST_ROWS = [('A1', '2026-11-07'), ('B2', '2026-11-04')]


def _planner_with_v8_schema(monkeypatch, draft):
    monkeypatch.setenv('A4B_PLANNER_PROMPT', 'v8')
    for step in draft['workflow']['nodes']:
        step['settings'] = {key: step['settings'][key] for key in (*NATIVE_SETTINGS[step['tool']], 'node_ref')}
    transport = ValidatingTransport(draft, draft)
    transport.schema = decoder_schema('v8')
    return planner(monkeypatch, transport)


@pytest.mark.parametrize('follow_up,pre,final,separate_final,expected', [
    pytest.param(CHANGE_RESULT, LATEST, AMOUNT_DESC, False, LATEST_ROWS[::-1], id='change-result'),
    pytest.param(CHANGE_RESULT, AMOUNT_DESC, [], False, None, id='change-wrong-retained-row'),
    pytest.param(CANCEL_RESULT, LATEST, [], False, LATEST_ROWS, id='cancel-result'),
    pytest.param(CANCEL_RESULT, [], [], False, None, id='cancel-wrong-retained-row'),
    pytest.param(CANCEL_RESULT, LATEST, LATEST, False, None, id='cancel-sort-in-dedup'),
    pytest.param(CANCEL_RESULT, LATEST, [], True, None, id='cancel-sort-after-dedup'),
])
def test_result_sort_followup_preserves_dedup_selection(
    monkeypatch, service, follow_up, pre, final, separate_final, expected,
):
    act, saved, fakes = service
    monkeypatch.setitem(CURRENT, 'request', PRE_FINAL_REQUEST)
    initial = make_planner([], sort=LATEST, sort_first=True, final_sort=AMOUNT_ASC)
    # Amount descending would pick the older A1. The surviving rows' amounts
    # also make amount descending and the inherited date order observably differ.
    records = [{**PRE_FINAL_ROWS[0], '금액': 400}, PRE_FINAL_ROWS[1], {**PRE_FINAL_ROWS[2], '금액': 350}]
    uploaded = act('upload', initial, asset=TABLE, rows=records)
    assert act('message', initial, content=PRE_FINAL_REQUEST)['status'] == 'ready'

    monkeypatch.setitem(CURRENT, 'request', follow_up)
    candidate = make_planner([], sort=pre, sort_first=bool(pre), final_sort=final)

    def generate(state):
        result = candidate(state)
        if separate_final:
            workflow = result['draft']['workflow']
            _, sorting, dedup, writer = workflow['nodes']
            post_sort = node('post_sort', 'filter_sort',
                             [{'input': 'table', 'node_id': dedup['id'], 'output': 'table'}],
                             execution_plan=sorting['settings']['execution_plan'])
            workflow['nodes'].insert(-1, post_sort)
            writer['bindings'][0]['node_id'] = post_sort['id']
            workflow['edges'][-1]['target'] = post_sort['id']
            workflow['edges'].append({'source': post_sort['id'], 'target': writer['id'], 'branch': 'next'})
        return result

    if expected is not None:
        draft = candidate({'messages': [{'role': 'user', 'content': follow_up}], 'resources': uploaded['resources']})['draft']
        generate = _planner_with_v8_schema(monkeypatch, draft)
    changed = act('message', generate, content=follow_up)
    if expected is None:
        assert changed['status'] == 'validation_failed'
        if follow_up == CANCEL_RESULT and (final or separate_final):
            assert any('결과 정렬이 취소' in issue for issue in changed['issues'])
        with pytest.raises(ValueError):
            act('confirm', generate)
        assert saved() == 0
        return
    assert changed['status'] == 'ready' and not changed['issues'], changed['issues']
    done = act('confirm', generate)
    assert done['status'] == 'saved' and saved() == 1
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == expected


@pytest.mark.parametrize('final', [SORT, []], ids=['opposite-direction', 'missing-result-sort'])
def test_direct_result_sort_can_reverse_the_same_presort_column(monkeypatch, service, final):
    request = PRE_FINAL_REQUEST.replace('금액 오름차순', '주문일 오름차순')
    monkeypatch.setitem(CURRENT, 'request', request)
    act, saved, fakes = service
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=PRE_FINAL_ROWS)
    candidate = make_planner([], sort=LATEST, sort_first=True, final_sort=final)({
        'messages': [{'role': 'user', 'content': request}], 'resources': uploaded['resources'],
    })['draft']
    generate = _planner_with_v8_schema(monkeypatch, candidate)

    first = act('message', generate, content=request)
    if not final:
        assert first['status'] == 'validation_failed'
        with pytest.raises(ValueError):
            act('confirm', generate)
        assert saved() == 0
        return
    assert first['status'] == 'ready' and not first['issues'], first['issues']
    done = act('confirm', generate)
    assert done['status'] == 'saved' and saved() == 1
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == LATEST_ROWS[::-1]
