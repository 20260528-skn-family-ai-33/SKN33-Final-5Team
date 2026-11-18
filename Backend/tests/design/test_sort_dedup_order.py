import csv
import io

import pytest

from agent_builder.conditions.condition_readiness import sort_dedup_order, sort_request
from tests.design.test_dedup_sort_save_loop import (
    AMOUNT_ASC, CURRENT, LATEST, PRE_FINAL_REQUEST, PRE_FINAL_ROWS, REQUEST, SORT_FIRST_REQUEST, SORT_FIRST_ROWS,
    SORT, TABLE, conversation, make_planner, press_save, service,
)


SORT_FIRST_SENTENCES = (
    '먼저 주문일 내림차순으로 정렬해줘. 그다음 주문번호가 같은 행은 첫 행만 남기고 '
    'CSV로 저장해줘. 빈 값은 그대로 유지해.'
)
SORT_FIRST_AFTER_VERB = (
    '주문일 내림차순 정렬을 먼저 하고, 주문번호가 같은 행은 첫 행만 남기고 '
    'CSV로 저장해줘. 빈 값은 그대로 유지해.'
)
SORT_FIRST_FROM = (
    '주문일 내림차순으로 정렬부터 하고, 주문번호가 같은 행은 첫 행만 남기고 '
    'CSV로 저장해줘. 빈 값은 그대로 유지해.'
)
DEDUP_FIRST_REVISION = (
    '순서를 바꿔줘. 이번에는 주문번호가 같은 행은 첫 행만 남긴 뒤 주문일 내림차순으로 정렬하고 CSV로 저장해줘.'
)
DEDUP_FIRST_SHORT_REVISION = '아니, 순서를 바꿔서 중복 제거를 먼저 해줘.'


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


def test_priority_particle_works_for_an_explicit_order():
    text = '주문일 내림차순으로 정렬부터 하고, 주문번호별 첫 행만 남겨줘.'
    assert sort_dedup_order(conversation(text)) == 'sort'


def test_short_priority_changes_only_an_existing_pair_in_the_same_task():
    assert sort_dedup_order(conversation(SORT_FIRST_REQUEST, DEDUP_FIRST_SHORT_REVISION)) == 'deduplicate'
    assert sort_dedup_order(conversation(
        SORT_FIRST_REQUEST, DEDUP_FIRST_SHORT_REVISION, '정렬부터 해줘.')) == 'sort'
    assert sort_dedup_order(conversation(
        '주문일 오름차순으로 정렬해줘.', DEDUP_FIRST_SHORT_REVISION)) is None
    assert sort_dedup_order(conversation(
        SORT_FIRST_REQUEST, '새로운 작업: 주문일 오름차순으로 정렬부터 해줘.')) is None


def test_short_priority_does_not_revive_deduplication_in_a_filter_only_contract():
    state = conversation(REQUEST, '정렬부터 해줘.')
    state['condition_ledger'] = {'decision': 'ready', 'operations': ['filter']}
    assert sort_dedup_order(state) is None


@pytest.mark.parametrize('followup', [
    '먼저 금액을 확인하고 중복 제거 설정은 설명해줘.',
    '중복 제거를 먼저 설명해줘.',
    '중복 제거 먼저 해?',
    '중복 제거를 먼저 하면 될까요？',
])
def test_short_priority_requires_an_action_request(followup):
    assert sort_dedup_order(conversation(SORT_FIRST_REQUEST, followup)) == 'sort'


@pytest.mark.parametrize(('request_text', 'prior_sort_first', 'expected_sort_first'), [
    (REQUEST.replace('오름차순', '내림차순'), False, False),
    (SORT_FIRST_REQUEST.replace('으로 먼저 정렬한 뒤', '으로 정렬하고,'), False, True),
    (SORT_FIRST_SENTENCES, False, True),
    (SORT_FIRST_AFTER_VERB, False, True),
    (SORT_FIRST_FROM, False, True),
    (DEDUP_FIRST_REVISION, True, False),
    (DEDUP_FIRST_SHORT_REVISION, True, False),
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


@pytest.mark.parametrize('correction', [
    '아니, 주문번호 중복을 제거한 뒤 금액 오름차순으로 정렬해줘.',
    '주문번호 중복 제거를 먼저 하고 금액 오름차순으로 정렬해줘.',
])
def test_in_turn_order_correction_is_not_an_additional_result_sort(correction):
    state = conversation('주문일 내림차순으로 정렬한 뒤 주문번호 중복을 제거해줘. ' + correction)
    request = sort_request(state)
    assert sort_dedup_order(state, request) == 'deduplicate'
    assert 'pre_sorts' not in request


@pytest.mark.parametrize('later_sort', [
    '중복 제거를 먼저 하고 금액 오름차순으로 정렬해줘.',
    '중복 제거한 뒤, 아니 결과는 금액 오름차순으로 정렬해줘.',
])
def test_an_output_only_instruction_does_not_override_an_earlier_sort(later_sort):
    state = conversation('주문일 내림차순으로 정렬한 뒤 ' + later_sort)
    request = sort_request(state)
    assert sort_dedup_order(state, request) == 'sort'
    assert request['pre_sorts'][0]['field'] == '주문일'
    assert request['sorts'][0]['field'] == '금액'


@pytest.mark.parametrize('request_text', [
    ('중복주문.csv를 주문일 내림차순으로 정렬한 뒤 주문번호가 같은 행의 중복을 제거한 뒤 '
     '금액 오름차순으로 정렬해서 CSV로 저장해줘. 주문번호 중복은 첫 행만 남겨줘. 빈 값은 그대로 유지해.'),
    PRE_FINAL_REQUEST.replace('첫 행만 남기고, 결과는', '첫 행만 남기고, 그다음에 결과는'),
])
@pytest.mark.parametrize('correct', [True, False])
def test_explicit_output_sort_does_not_reverse_the_pre_dedup_sort(service, request_text, correct):
    state = conversation(request_text)
    request = sort_request(state)
    assert sort_dedup_order(state, request) == 'sort'
    assert [(item['field'], item['direction']) for item in request['pre_sorts']] == [('주문일', 'descending')]
    assert [(item['field'], item['direction']) for item in request['sorts']] == [('금액', 'ascending')]

    act, saved, fakes = service
    CURRENT['request'] = request_text
    planner = (make_planner([], sort=LATEST, sort_first=True, final_sort=AMOUNT_ASC) if correct
               else make_planner([], sort=AMOUNT_ASC))
    act('upload', planner, asset=dict(TABLE), rows=PRE_FINAL_ROWS)
    first = act('message', planner, content=request_text)
    done = press_save(act, planner)
    if correct:
        assert first['status'] == 'ready' and done['status'] == 'saved'
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded'
        rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
        assert [(row['주문번호'], row['주문일']) for row in rows] == [('B2', '2026-11-04'), ('A1', '2026-11-07')]
    else:
        assert first['status'] == 'validation_failed' and saved() == 0
        assert any('정렬한 뒤 중복' in issue for issue in first['issues'])


@pytest.mark.parametrize(('follow_up', 'expected_sort', 'order'), [
    ('아니, 정렬은 빼고 원본 순서 그대로 저장해줘.', [], [('A1', '2026-11-01'), ('B2', '2026-11-04')]),
    ('금액 오름차순으로 먼저 정렬해？', LATEST, [('B2', '2026-11-04'), ('A1', '2026-11-01')]),
])
@pytest.mark.parametrize('correct', [True, False])
def test_service_applies_the_latest_directive_but_keeps_order_across_questions(
        service, follow_up, expected_sort, order, correct):
    act, saved, fakes = service
    text = REQUEST.replace('오름차순', '내림차순') + ' ' + follow_up
    CURRENT['request'] = text
    other_sort = [] if expected_sort else LATEST
    planner = make_planner([], sort=expected_sort if correct else other_sort)
    act('upload', planner, asset=dict(TABLE), rows=SORT_FIRST_ROWS)
    first = act('message', planner, content=text)
    if not correct:
        assert first['status'] == 'validation_failed'
        press_save(act, planner)
        assert saved() == 0
        return

    assert first['status'] == 'ready'
    done = press_save(act, planner)
    assert done['status'] == 'saved'
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == order


@pytest.mark.parametrize('correct', [True, False])
def test_service_changes_only_the_presort_and_keeps_the_result_sort(service, correct):
    act, saved, fakes = service
    CURRENT['request'] = PRE_FINAL_REQUEST
    original = make_planner([], sort=LATEST, sort_first=True, final_sort=AMOUNT_ASC)
    act('upload', original, asset=dict(TABLE), rows=PRE_FINAL_ROWS)
    assert act('message', original, content=PRE_FINAL_REQUEST)['status'] == 'ready'

    text = '중복 제거 전에는 금액 오름차순으로 정렬해줘.'
    CURRENT['request'] = text
    planner = make_planner([], sort=AMOUNT_ASC if correct else LATEST, sort_first=True, final_sort=AMOUNT_ASC)
    first = act('message', planner, content=text)
    if not correct:
        assert first['status'] == 'validation_failed'
        press_save(act, planner)
        assert saved() == 0
        return

    assert first['status'] == 'ready'
    done = press_save(act, planner)
    assert done['status'] == 'saved'
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == [('A1', '2026-11-01'), ('B2', '2026-11-04')]


@pytest.mark.parametrize('correct', [True, False])
def test_service_enforces_presort_direction_independently_of_the_result_sort(service, correct):
    act, saved, fakes = service
    text = PRE_FINAL_REQUEST.replace('주문일 내림차순으로 먼저', '최신순으로 먼저')
    CURRENT['request'] = text
    planner = make_planner([], sort=LATEST if correct else SORT, sort_first=True, final_sort=AMOUNT_ASC)
    act('upload', planner, asset=dict(TABLE), rows=PRE_FINAL_ROWS)
    first = act('message', planner, content=text)
    if not correct:
        assert first['status'] == 'validation_failed'
        press_save(act, planner)
        assert saved() == 0
        return

    assert first['status'] == 'ready'
    done = press_save(act, planner)
    assert done['status'] == 'saved'
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    rows = csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff')))
    assert [(row['주문번호'], row['주문일']) for row in rows] == [('B2', '2026-11-04'), ('A1', '2026-11-07')]
