"""Check stage ordering against the actual CSV and screen result."""

import csv
import json

import pytest

from agent_builder.design.native_settings import NATIVE_SETTINGS
from agent_builder.design.nodes.direct_conversation import decoder_schema
from contracts.models import AgentDraft
from tests.design.test_dedup_sort_save_loop import (
    AMOUNT_ASC, CURRENT, LATEST, PRE_FINAL_REQUEST, PRE_FINAL_ROWS, REQUEST,
    SORT_FIRST_REQUEST, SORT_FIRST_ROWS, TABLE, make_planner, node, service,
)
from tests.design.test_direct_conversation import planner as direct_planner
from tests.design.test_direct_sort_before_dedup import ValidatingTransport
from workflow_engine.lifecycle.run_lifecycle import RunLifecycle


def _direct_draft(monkeypatch, candidate, version, *, linear=False):
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)
    if linear:
        steps = [{'tool': node['tool'], 'label': node['label'],
                  'settings': {**node['settings'], 'execution_plan': None}} for node in candidate['workflow']['nodes']]
        candidate['workflow'] = {'steps': steps}
    elif version in ('v6', 'v7', 'v8'):
        for step in candidate['workflow']['nodes']:
            step['settings'] = {key: step['settings'][key] for key in (*NATIVE_SETTINGS[step['tool']], 'node_ref')}
    transport = ValidatingTransport(candidate, candidate)
    transport.schema = decoder_schema(version)
    return direct_planner(monkeypatch, transport), transport


def _published_csv(monkeypatch):
    published = []
    publish = RunLifecycle.publish

    def capture(self, run_id, token, target, content):
        publish(self, run_id, token, target, content)
        if target.suffix == '.csv':
            published.append(target)

    monkeypatch.setattr(RunLifecycle, 'publish', capture)
    return published


@pytest.mark.parametrize('order', ['source', 'before', 'unspecified'])
@pytest.mark.parametrize('planned_first', ['sort', 'deduplicate'])
def test_confirmed_order_controls_whether_wrong_rows_can_be_written(monkeypatch, service, order, planned_first):
    act, saved, fakes = service
    request = {
        'source': REQUEST.replace('오름차순', '내림차순'),
        'before': SORT_FIRST_REQUEST,
        'unspecified': ('중복주문.csv의 주문번호 중복 처리 정책은 첫 행만 남기기. '
                        '주문일 내림차순으로 정렬해줘. CSV로 저장해줘. 빈 값은 그대로 유지해.'),
    }[order]
    monkeypatch.setitem(CURRENT, 'request', request)
    generate = make_planner([], sort=LATEST, sort_first=planned_first == 'sort')
    reports = _published_csv(monkeypatch)

    act('upload', generate, asset=TABLE, rows=SORT_FIRST_ROWS)
    first = act('message', generate, content=request)
    if (order == 'before') != (planned_first == 'sort'):
        assert first['status'] == 'validation_failed', first['issues']
        with pytest.raises(ValueError):
            act('confirm', generate)
        assert saved() == 0 and reports == []
        return

    assert first['status'] == 'ready', first['issues']
    done = act('confirm', generate)
    run = fakes['run'](done['saved_agent_id'])
    assert run['status'] == 'succeeded' and saved() == 1 and len(reports) == 1
    with reports[0].open(encoding='utf-8-sig', newline='') as stream:
        rows = [(row['주문번호'], row['주문일']) for row in csv.DictReader(stream)]
    assert rows == ([('A1', '2026-11-07'), ('B2', '2026-11-04')] if order == 'before'
                    else [('B2', '2026-11-04'), ('A1', '2026-11-01')])


@pytest.mark.parametrize('version', ['v1', 'v8'])
def test_screen_only_deduplicated_rows_can_end_in_finish(monkeypatch, service, version):
    act, saved, fakes = service
    request = '중복주문.csv에서 주문번호가 같은 행은 첫 행만 남겨 보여줘. 빈 값은 그대로 유지해.'
    monkeypatch.setitem(CURRENT, 'request', request)
    original_conditions = fakes['cond'].make
    original_capability = fakes['cap'].make

    def conditions(messages):
        proposal = original_conditions(messages)
        proposal['conditions'] = [item for item in proposal['conditions'] if item['key'] != 'output.format']
        return proposal

    monkeypatch.setattr(fakes['cond'], 'make', conditions)
    monkeypatch.setattr(fakes['cap'], 'make', lambda messages: {
        **original_capability(messages), 'tool_ids': ['read_table', 'filter_sort', 'finish'],
    })
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=SORT_FIRST_ROWS)
    candidate = make_planner([], sort=[])({'resources': uploaded['resources'], 'messages': [
        {'role': 'user', 'content': request},
    ]})['draft']
    candidate['tool_ids'] = ['read_table', 'filter_sort', 'finish']
    candidate['workflow']['nodes'][-1] = node('out', 'finish', instructions='중복 제거를 완료했습니다.')
    candidate = AgentDraft.model_validate(candidate).model_dump()
    generate, _ = _direct_draft(monkeypatch, candidate, version, linear=version == 'v8')
    first = act('message', generate, content=request)
    assert first['status'] == 'ready', first['issues']
    done = act('confirm', generate)
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded' and saved() == 1
    assert result['artifact'] is None
    assert [(row['주문번호'], row['주문일']) for row in result['rows']] == [
        ('A1', '2026-11-01'), ('B2', '2026-11-04'),
    ]


@pytest.mark.parametrize('version, example', [
    ('v6', 'written'), ('v7', 'written'), ('v8', 'written'),
    ('v6', 'partial'), ('v7', 'partial'), ('v8', 'partial'),
    ('v8', 'presort'), ('v8', 'partial-presort'),
])
def test_direct_generation_matches_the_order_contract(monkeypatch, service, version, example):
    act, saved, fakes = service
    before_dedup = example in ('presort', 'partial-presort')
    separate_result = example == 'partial-presort'
    request = {
        'written': REQUEST.replace('오름차순', '내림차순'),
        'partial': REQUEST.replace('주문일 오름차순으로 정렬해서', '최신순으로 정렬해서'),
        'presort': SORT_FIRST_REQUEST,
        'partial-presort': PRE_FINAL_REQUEST.replace('주문일 내림차순으로', '최신순으로'),
    }[example]
    monkeypatch.setitem(CURRENT, 'request', request)
    uploaded = act('upload', lambda state: {}, asset=TABLE, rows=PRE_FINAL_ROWS if separate_result else SORT_FIRST_ROWS)
    candidate = make_planner([], sort=LATEST, sort_first=before_dedup, final_sort=AMOUNT_ASC if separate_result else None)({
        'resources': uploaded['resources'], 'messages': [{'role': 'user', 'content': request}],
    })['draft']
    candidate['tool_ids'].append('finish')
    candidate['workflow']['nodes'].append(node('finish', 'finish', [
        {'input': 'artifact', 'node_id': 'out', 'output': 'artifact'},
    ]))
    candidate['workflow']['edges'].append({'source': 'out', 'target': 'finish', 'branch': 'next'})
    candidate = AgentDraft.model_validate(candidate).model_dump()
    generate, transport = _direct_draft(monkeypatch, candidate, version, linear=version == 'v8' and not before_dedup)
    reports = _published_csv(monkeypatch)
    first = act('message', generate, content=request)
    assert first['status'] == 'ready', first['issues']
    contract = json.loads(transport.calls[0][1]['content'].split('\n', 1)[1])
    sort = contract['requested_sort']
    if separate_result:
        assert sort['sorts'][0]['field'] == '금액'
        direction = sort['pre_sort_hint']['direction']
    else:
        direction = sort.get('direction') if example == 'partial' else sort['sorts'][0]['direction']
    assert direction == 'descending'
    done = act('confirm', generate)
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded' and saved() == 1 and len(reports) == 1
    with reports[0].open(encoding='utf-8-sig', newline='') as stream:
        rows = [(row['주문번호'], row['주문일']) for row in csv.DictReader(stream)]
    expected = {
        'written': [('B2', '2026-11-04'), ('A1', '2026-11-01')],
        'partial': [('B2', '2026-11-04'), ('A1', '2026-11-01')],
        'presort': [('A1', '2026-11-07'), ('B2', '2026-11-04')],
        'partial-presort': [('B2', '2026-11-04'), ('A1', '2026-11-07')],
    }
    assert rows == expected[example]
