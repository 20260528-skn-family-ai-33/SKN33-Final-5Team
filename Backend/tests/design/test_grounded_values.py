"""Planner-filled filter values need evidence (2026-11-11 RunPod X07, 2026-11-12 Aside AI S5)."""
import pytest

from agent_builder.design.grounded_values import ungrounded_value_questions


def state(*messages, resources=(), ledger=None, temporal=None):
    return {'messages': [{'role': 'user', 'content': m} for m in messages], 'resources': list(resources),
            **({'condition_ledger': ledger} if ledger else {}), **({'temporal_context': temporal} if temporal else {})}


def filter_draft(value, field='amount', operator='gte'):
    plan = {'filters': [{'field': field, 'operator': operator, 'value': value, 'compare_field': None, 'value_type': 'number'}],
            'sort': [], 'match': 'all', 'missing_policy': 'error', 'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'json'}
    return {'workflow': {'nodes': [{'id': 'f', 'tool': 'filter_sort', 'settings': {'execution_plan': plan}}]}}


def compute_draft(formula, column):
    return {'workflow': {'nodes': [{'id': 'c', 'tool': 'calculate_columns',
                                    'settings': {'table_operation': 'compute', 'calculation_formula': formula, 'output_column': column}}]}}


def test_x07_invented_threshold_is_asked():
    s = state('연결한 표에서 amount가 큰 행만 선별한 뒤 region별 amount 합계를 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    assert ungrounded_value_questions(s, filter_draft(0.0, operator='gt')) == ['amount 열을 선별할 기준 값은 얼마인가요?']


@pytest.mark.parametrize('request_text,draft', [
    ('연결된 표에서 amount가 20 이상인 행만 JSON으로 저장해줘.', filter_draft(20.0)),                       # R02
    ('비용이 10만원 이상인 행만 남겨줘.', filter_draft(100000.0, field='비용')),
    ('비용이 1,000,000원 초과인 행만 남겨줘.', filter_draft(1000000, field='비용', operator='gt')),
    ('지역이 서울인 행만 남겨줘.', filter_draft('서울', field='region', operator='eq')),
    ('입사일이 2020-01-01 이전인 사람만 저장해줘.', filter_draft('2020-01-01', field='hired', operator='lt')),
    ('금액이 십만원 이상인 행만 남겨줘.', filter_draft(100000, field='금액')),
    ('오십만 원 초과만', filter_draft(500000, field='금액', operator='gt')),
    ('백만원 이상', filter_draft(1000000, field='금액')),
])
def test_values_written_by_the_user_are_not_asked(request_text, draft):
    assert ungrounded_value_questions(state(request_text), draft) == []


def test_a_threshold_from_an_attached_document_or_settled_answer_is_grounded():
    policy = {'id': 'p', 'kind': 'document', 'text': '비용 기준은 300000원 이상이다.', 'pages': []}
    assert ungrounded_value_questions(state('규정의 비용 기준으로 선별해줘.', resources=[policy]), filter_draft(300000.0, field='비용')) == []
    ledger = {'slots': {'filter.value': {'status': 'specified', 'value': 50}}}
    assert ungrounded_value_questions(state('금액 기준은 위에서 정한 대로', ledger=ledger), filter_draft(50)) == []


def test_a_relative_date_from_the_calendar_context_is_grounded():
    temporal = {'user_message_anchors': [{'relative_days': {'yesterday': {'date': '2026-11-09'}}}]}
    assert ungrounded_value_questions(state('어제 이후 주문만 남겨줘.', temporal=temporal),
                                      filter_draft('2026-11-09', field='date', operator='gte')) == []


def test_column_comparisons_and_empty_checks_need_no_constant():
    draft = filter_draft(None, operator='is_empty')
    assert ungrounded_value_questions(state('비고가 빈 행만 남겨줘.'), draft) == []


def test_calendar_context_numbers_do_not_ground_an_invented_threshold():
    # 2026-11-11 RunPod X07: "00:00"/version numbers in the calendar context let 0 pass.
    temporal = {'version': 2, 'local_time': '2026-11-11T19:02:46+09:00',
                'relative_days': {'today': {'date': '2026-11-11', 'start': '2026-11-11T00:00:00+09:00'}}}
    s = state('연결한 표에서 amount가 큰 행만 선별한 뒤 region별 amount 합계를 JSON으로 저장해줘.', temporal=temporal)
    assert ungrounded_value_questions(s, filter_draft(0.0, operator='gt')) == ['amount 열을 선별할 기준 값은 얼마인가요?']
    assert ungrounded_value_questions(s, filter_draft(2.0, operator='gt')) != []


# 2026-11-12 Aside AI S5: "완료 주문" became 주문상태 is_empty.
def test_an_emptiness_filter_without_any_word_about_empty_values_is_asked():
    s = state('주문내역에서 매출이 높은 주문만 정리해줘.', '완료 주문만 대상으로 해줘.')
    assert ungrounded_value_questions(s, filter_draft(None, field='주문상태', operator='is_empty')) == \
        ['주문상태 열은 어떤 값을 기준으로 선별할까요?']


@pytest.mark.parametrize('text', ['비고가 빈 행만 남겨줘.', '담당자가 비어 있는 주문만 보여줘.', '주소가 없는 고객만 골라줘.',
                                  '연락처 누락된 행을 찾아줘.', '빈 값은 제외해줘.', '주소 미등록 고객만 골라줘.',
                                  '결제일 없음인 주문만', '연락처 공란인 행', 'missing 값 제외'])
def test_an_emptiness_filter_the_user_asked_for_is_kept(text):
    for operator in ('is_empty', 'not_empty'):
        assert ungrounded_value_questions(state(text), filter_draft(None, field='비고', operator=operator)) == []


def test_document_text_about_empty_values_does_not_ground_an_emptiness_filter():
    doc = {'id': 'p', 'kind': 'document', 'text': '빈 칸은 제출하지 않는다.', 'pages': []}
    s = state('완료 주문만 정리해줘.', resources=[doc])
    assert ungrounded_value_questions(s, filter_draft(None, field='주문상태', operator='is_empty')) != []


def test_computed_columns_are_left_to_compute_grounding():
    # Korean wording for English columns ("수량에 단가를 곱한 합계 열") must not be asked again here.
    s = state('수량에 단가를 곱한 합계 열을 만들어줘.')
    assert ungrounded_value_questions(s, compute_draft('qty*unit_price', 'total')) == []
