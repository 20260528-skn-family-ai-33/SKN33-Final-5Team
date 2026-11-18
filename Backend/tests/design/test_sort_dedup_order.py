import csv
import io

import pytest

from agent_builder.conditions.condition_readiness import sort_dedup_order
from tests.design.test_dedup_sort_save_loop import (
    CURRENT, LATEST, SORT_FIRST_REQUEST, SORT_FIRST_ROWS, TABLE, conversation, make_planner, press_save, service,
)


SORT_FIRST_SENTENCES = (
    '먼저 주문일 내림차순으로 정렬해줘. 그다음 주문번호가 같은 행은 첫 행만 남기고 '
    'CSV로 저장해줘. 빈 값은 그대로 유지해.'
)
SORT_FIRST_AFTER_VERB = (
    '주문일 내림차순 정렬을 먼저 하고, 주문번호가 같은 행은 첫 행만 남기고 '
    'CSV로 저장해줘. 빈 값은 그대로 유지해.'
)
DEDUP_FIRST_REVISION = (
    '순서를 바꿔줘. 이번에는 주문번호가 같은 행은 첫 행만 남긴 뒤 주문일 내림차순으로 정렬하고 CSV로 저장해줘.'
)


@pytest.mark.parametrize(('request_text', 'expected'), [
    ('주문번호 중복을 제거하기 전에 주문일 내림차순으로 정렬해줘.', 'sort'),
    ('주문일 내림차순으로 정렬하기 전에 주문번호 중복을 제거해줘.', 'deduplicate'),
])
def test_before_phrasing_reverses_the_order_of_mentioned_actions(request_text, expected):
    assert sort_dedup_order(conversation(request_text)) == expected


def test_latest_explicit_step_order_replaces_previous_order():
    assert sort_dedup_order(conversation(SORT_FIRST_REQUEST, '첫 행만 남김')) == 'sort'
    assert sort_dedup_order(conversation(SORT_FIRST_REQUEST, DEDUP_FIRST_REVISION)) == 'deduplicate'
    assert sort_dedup_order(conversation(SORT_FIRST_REQUEST, DEDUP_FIRST_REVISION, SORT_FIRST_SENTENCES)) == 'sort'


@pytest.mark.parametrize(('request_text', 'prior_sort_first', 'expected_sort_first'), [
    (SORT_FIRST_SENTENCES, False, True),
    (SORT_FIRST_AFTER_VERB, False, True),
    (DEDUP_FIRST_REVISION, True, False),
])
@pytest.mark.parametrize('correct', [True, False])
def test_service_saves_only_currently_requested_row_step_order(
        service, request_text, prior_sort_first, expected_sort_first, correct):
    act, saved, fakes = service
    CURRENT['request'] = SORT_FIRST_REQUEST if prior_sort_first else request_text
    original = make_planner([], sort=LATEST, sort_first=True)
    act('upload', original, asset=dict(TABLE), rows=SORT_FIRST_ROWS)
    if prior_sort_first:
        assert act('message', original, content=SORT_FIRST_REQUEST)['status'] == 'ready'

    CURRENT['request'] = request_text
    plan = make_planner([], sort=LATEST, sort_first=expected_sort_first == correct)
    first = act('message', plan, content=request_text)
    done = press_save(act, plan)
    if correct:
        assert first['status'] == 'ready' and done['status'] == 'saved'
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded'
        rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
        order = [(row['주문번호'], row['주문일']) for row in rows]
        assert order == ([('A1', '2026-11-07'), ('B2', '2026-11-04')] if expected_sort_first
                         else [('B2', '2026-11-04'), ('A1', '2026-11-01')])
    else:
        assert first['status'] == 'validation_failed' and saved() == 0
