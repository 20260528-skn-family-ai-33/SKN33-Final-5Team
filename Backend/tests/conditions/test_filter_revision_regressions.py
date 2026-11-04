"""Check conversation revisions against the filters that run on boundary rows."""
import json
from copy import deepcopy

import pytest

from agent_builder.capabilities.capability_judgement import assess_capability
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.nodes.validation import validate_node
from tests.conditions.test_condition_readiness import Fake, state, workflow_draft
from workflow_engine.executors.table.filter_sort import execute


def conversation(request, revision=None):
    session = state(request)
    if revision:
        session['messages'].extend([{'role': 'assistant', 'content': '이 조건으로 저장할까요?'},
                                    {'role': 'user', 'content': revision}])
    return session


def audited(session, rows, predicates):
    draft = workflow_draft(session)
    filters = [{'field': field, 'operator': operator, 'value': value, 'value_type': 'number',
                'compare_field': None} for field, operator, value in predicates]
    draft['execution_plan']['filters'] = filters
    read_node, filter_node, _ = draft['workflow']['nodes']
    filter_node['settings']['execution_plan']['filters'] = deepcopy(filters)
    table = {'id': 'stock', 'kind': 'table', 'name': 'stock.csv', 'columns': list(rows[0]),
             'row_count': len(rows), 'profile': {}}
    draft['document_ids'] = read_node['settings']['file_ids'] = [table['id']]
    current = {**session, 'resources': [table], 'draft': draft}
    return validate_node({**current, **audit_conditions(current)})


def result_rows(session, rows, predicates):
    validation = audited(session, rows, predicates)
    assert validation['issues'] == []
    draft = validation['draft']
    node = draft['workflow']['nodes'][1]
    assert node['settings']['execution_plan']['filters'] == draft['execution_plan']['filters']
    inputs = {'outputs': {'read': {'table': {'rows': rows, 'columns': list(rows[0])}}}}
    return execute(node, inputs, None, draft, None)['table']['rows']


def test_same_threshold_on_qty_and_amount_has_separate_operators():
    session = conversation('qty가 30 이상인 행은 제외하고 amount가 30 이상인 행만 남겨 JSON으로 저장해줘.')
    rows = [{'qty': 15, 'amount': 30}, {'qty': 30, 'amount': 60}, {'qty': 31, 'amount': 62}]
    assert result_rows(session, rows, [('qty', 'lt', 30), ('amount', 'gt', 30)]) == [rows[0]]


def test_revision_from_at_least_to_greater_than_excludes_equal_value():
    session = conversation('amount가 30 이상인 행만 남겨줘.', '아니, amount가 30 초과로 바꿔줘.')
    rows = [{'amount': value} for value in (29, 30, 31)]
    assert result_rows(session, rows, [('amount', 'gte', 30)]) == [rows[2]]


def test_new_task_offers_and_accepts_only_its_own_quotes():
    receipt = '올려준 영수증 이미지 파일 내용을 엑셀파일로 정리해줘'
    sales = '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해서 회의용 표로 만들어줘.'
    session = state(receipt)
    session['messages'].extend([{'role': 'assistant', 'content': '분석할 파일을 먼저 올려 주세요.'},
                                {'role': 'user', 'content': sales}])
    packet = {'decision': 'existing_tools', 'tool_ids': ['read_table'], 'missing_conditions': [],
              'missing_actions': [], 'reason': '표 자료를 읽어 처리합니다.', 'user_quotes': [sales]}

    class RecordingTransport(Fake):
        def invoke(self, messages):
            self.request = json.loads(messages[1]['content'])
            return super().invoke(messages)

    transport = RecordingTransport([packet])
    assert assess_capability(session, transport)['user_quotes'] == [sales]
    assert transport.request['user_quote_catalog'] == [sales]
    for variant in transport.schema['anyOf']:
        assert variant['properties']['user_quotes']['items']['enum'] == [sales]
    with pytest.raises(ValueError, match='원문 근거'):
        assess_capability(session, Fake([{**packet, 'user_quotes': [receipt]}]))


def test_revision_from_include_to_exclude_rejects_the_old_range():
    session = conversation('amount가 30 이상인 행만 남겨 JSON으로 저장해줘.',
                           '아니, amount가 30 이상인 행은 제외하고 저장해줘.')
    rows = [{'amount': value} for value in (20, 30, 40)]
    assert result_rows(session, rows, [('amount', 'lt', 30)]) == [rows[0]]
    assert audited(session, rows, [('amount', 'gte', 30)])['issues']


def test_total_amount_and_amount_do_not_share_a_column_match():
    session = conversation('total_amount가 30 이상인 행만 남기고, amount가 30 이상인 행은 제외해서 JSON으로 저장해줘.')
    rows = [{'total_amount': 40, 'amount': value} for value in (20, 30, 40)]
    assert result_rows(session, rows, [('total_amount', 'gte', 30), ('amount', 'lt', 30)]) == [rows[0]]
    assert audited(session, rows, [('total_amount', 'gte', 30), ('amount', 'gte', 30)])['issues']


def test_changing_upper_bound_preserves_lower_bound():
    session = conversation('amount가 30 이상이고 amount가 100 이하인 행만 남겨 JSON으로 저장해줘.',
                           '하한은 그대로 두고 amount가 200 이하인 행까지 남겨줘.')
    rows = [{'amount': value} for value in (29, 30, 200, 201)]
    assert result_rows(session, rows, [('amount', 'gte', 30), ('amount', 'lte', 200)]) == rows[1:3]
    assert audited(session, rows, [('amount', 'gte', 30), ('amount', 'lte', 100)])['issues']


@pytest.mark.parametrize(('revision', 'operator', 'expected'), [
    ('아니, 30 초과인 행만 남겨줘.', 'gt', 31),
    ('아니, 30을 넘는 행만 남겨줘.', 'gt', 31),
    ('아니, 30 이상인 행은 빼고 나머지만 보여줘.', 'lt', 29),
])
def test_unnamed_revision_does_not_restore_the_old_bound(revision, operator, expected):
    session = conversation('amount가 30 이상인 행만 남겨줘.', revision)
    rows = [{'amount': n} for n in (29, 30, 31)]
    assert result_rows(session, rows, [('amount', operator, 30)]) == [{'amount': expected}]
