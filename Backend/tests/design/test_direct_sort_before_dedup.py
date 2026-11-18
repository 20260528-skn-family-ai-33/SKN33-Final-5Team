"""Exercise two-stage sorting through the Direct planner's transport schema."""

import csv
import io

import pytest

from agent_builder.design.native_settings import NATIVE_SETTINGS
from agent_builder.design.nodes.direct_conversation import decoder_schema
from tests.design.test_dedup_sort_save_loop import (
    CURRENT, LATEST, SORT_FIRST_REQUEST, SORT_FIRST_ROWS, TABLE, make_planner, service,
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
    from tests.design.test_dedup_sort_save_loop import AMOUNT_ASC, PRE_FINAL_REQUEST, PRE_FINAL_ROWS
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
