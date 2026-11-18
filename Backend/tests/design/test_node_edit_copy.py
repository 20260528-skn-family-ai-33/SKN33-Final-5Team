"""Friendly edit copy is still bound to the exact settings selected in the UI."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from agent_builder.design.node_edit_copy import (
    FIELD_LABELS, change_summary, is_node_edit_error, public_error, public_request,
    validate_visible_changes,
)


def _node(arguments=None, filters=None):
    return {'id': 'mail-2', 'label': '두 번째 메일 읽기', 'tool': 'email.list', 'bindings': [],
            'settings': {'arguments_json': json.dumps(arguments or {}, ensure_ascii=False),
                         'execution_plan': {'filters': filters or [], 'sort': [], 'match': 'all',
                                            'missing_policy': 'keep', 'duplicate_policy': 'keep'}}}


def _edit(node, rows, changes):
    return {'node_id': node['id'], 'label': node['label'], 'changes': changes,
            'request': json.dumps(node['label'], ensure_ascii=False) + ' 작업의 설정을 다음과 같이 바꿔주세요.\n'
                       + '\n'.join(rows) + '\n다른 작업의 설정은 유지해주세요.'}


def _change(path, old, new):
    return {'path': path, 'old_value': old, 'new_value': new}


def test_python_labels_match_frontend_labels_exactly():
    source = (Path(__file__).parents[3] / 'Frontend/app/node-setting-copy.ts').read_text(encoding='utf-8')
    labels = dict(re.findall(r"([a-z_]+):'([^']*)'", source.split('};', 1)[0]))
    assert FIELD_LABELS == labels


def test_friendly_gmail_multiple_changes_preserve_typed_values_and_baseline():
    node = _node({'max_results': 100, 'query': 'is:unread'})
    before = deepcopy(node)
    edit = _edit(node, ['- 조회 한도: 100 → 10', '- 검색 조건: "is:unread" → "newer_than:3d"'], [
        _change('arguments_json.max_results', 100, 10),
        _change('arguments_json.query', 'is:unread', 'newer_than:3d'),
    ])
    validate_visible_changes(edit, node)
    assert node == before
    assert '조회 한도: 100 → 10' in change_summary(edit)
    assert '검색 조건: "is:unread" → "newer_than:3d"' in change_summary(edit)
    assert 'arguments_json' not in change_summary(edit)


def test_general_numeric_and_unknown_arguments_use_safe_labels():
    node = _node({'absolute_tolerance': 0.5, 'other_registered_number': 4, 'query': '기존 조건'})
    edit = _edit(node, ['- 허용 오차: 0.5 → 0', '- 추가 설정: 4 → 8', '- 검색 조건: "기존 조건" → "새 조건"'], [
        _change('arguments_json.absolute_tolerance', 0.5, 0),
        _change('arguments_json.other_registered_number', 4, 8),
        _change('arguments_json.query', '기존 조건', '새 조건'),
    ])
    validate_visible_changes(edit, node)
    assert 'other_registered_number' not in change_summary(edit)


def test_numeric_json_representations_are_semantically_equal():
    node = _node({'max_results': 100.0})
    edit = _edit(node, ['- 조회 한도: 100 → 10.0'], [_change('arguments_json.max_results', 100, 10)])
    validate_visible_changes(edit, node)


def test_comparisons_switch_modes_and_use_actual_baseline_field_names():
    node = _node(filters=[
        {'field': 'stock', 'operator': 'lt', 'value': 30, 'compare_field': None, 'value_type': 'number'},
        {'field': 'price', 'operator': 'gte', 'value': None, 'compare_field': 'minimum', 'value_type': 'number'},
    ])
    edit = _edit(node, ['- 조건 1 (stock) 비교 기준: 숫자 30.0 → 열 "reorder_level"',
                        '- 조건 2 (price) 비교 기준: 열 "minimum" → 숫자 0'], [
        _change('execution_plan.filters[0].value', 30, None),
        _change('execution_plan.filters[0].compare_field', None, 'reorder_level'),
        _change('execution_plan.filters[1].value', None, 0),
        _change('execution_plan.filters[1].compare_field', 'minimum', None),
    ])
    validate_visible_changes(edit, node)
    assert '조건 1 비교 기준: 숫자 30 → 열 "reorder_level"' in change_summary(edit)
    assert 'execution_plan' not in change_summary(edit)
    for request in [edit['request'].replace('(stock)', '(quantity)'),
                    edit['request'].replace('숫자 30.0', '숫자 31'),
                    edit['request'].replace('열 "minimum"', '열 "other"')]:
        with pytest.raises(ValueError):
            validate_visible_changes({**edit, 'request': request}, node)


@pytest.mark.parametrize('mutate', [
    lambda edit: edit.update(node_id='mail-1'),
    lambda edit: edit.update(label='첫 번째 메일 읽기'),
    lambda edit: edit['changes'][0].update(path='arguments_json.count'),
    lambda edit: edit['changes'][0].update(old_value=99),
    lambda edit: edit['changes'][0].update(new_value=20),
    lambda edit: edit['changes'][0].update(new_value=True),
    lambda edit: edit['changes'][0].update(new_value=float('nan')),
    lambda edit: edit['changes'].reverse(),
    lambda edit: edit['changes'].append(deepcopy(edit['changes'][0])),
    lambda edit: edit.update(request=edit['request'].replace('조회 한도', '한도')),
    lambda edit: edit.update(request=edit['request'] + '\n추가 요청'),
])
def test_hidden_target_values_paths_and_order_cannot_be_changed(mutate):
    node = _node({'max_results': 100, 'count': 100, 'query': 'is:unread'})
    edit = _edit(node, ['- 조회 한도: 100 → 10', '- 검색 조건: "is:unread" → "newer_than:3d"'], [
        _change('arguments_json.max_results', 100, 10),
        _change('arguments_json.query', 'is:unread', 'newer_than:3d'),
    ])
    mutate(edit)
    with pytest.raises(ValueError):
        validate_visible_changes(edit, node)


@pytest.mark.parametrize('key', ['api_token', '__proto__', 'constructor', 'password'])
def test_sensitive_or_uneditable_hidden_paths_are_rejected(key):
    node = _node({key: 10})
    edit = _edit(node, ['- 추가 설정: 10 → 20'], [_change('arguments_json.' + key, 10, 20)])
    with pytest.raises(ValueError):
        validate_visible_changes(edit, node)


def test_ambiguous_fallback_labels_are_rejected_without_exposing_a_key():
    node = _node({'unknown_a': 10, 'unknown_b': 10})
    edit = _edit(node, ['- 추가 설정: 10 → 20'], [_change('arguments_json.unknown_a', 10, 20)])
    with pytest.raises(ValueError) as error:
        validate_visible_changes(edit, node)
    assert 'unknown_' not in str(error.value)


def test_legacy_requests_are_projected_only_when_the_entire_format_is_recognized():
    legacy = ('`메일 읽기` 작업 (노드 ID: mail-internal)의 설정을 다음과 같이 바꿔줘.\n'
              '- 조회 한도 (arguments_json.max_results): 100 → 10\n'
              '- 검색 조건 (arguments_json.query): arguments_json.max_results → execution_plan.filters[0].value\n'
              '다른 작업의 설정은 유지해줘.')
    projected = public_request(legacy)
    assert projected.startswith('"메일 읽기" 작업의 설정을 다음과 같이 바꿔주세요.')
    assert '- 조회 한도: 100 → 10' in projected
    assert '- 검색 조건: "arguments_json.max_results" → "execution_plan.filters[0].value"' in projected
    assert 'mail-internal' not in projected and '(arguments_json.query)' not in projected
    for original in [legacy + '\n추가 설명', legacy.replace('조회 한도 (', '조회 조건 ('),
                     '사용자가 설명한 arguments_json.max_results 경로를 그대로 보존해주세요.',
                     '앞 문장\n' + legacy, legacy.replace('다른 작업의 설정은 유지해줘.', '')]:
        assert public_request(original) == original
    assert public_request(projected) == projected


def test_legacy_numeric_comparison_projection_removes_machine_metadata():
    legacy = ('`조건 선별` 작업 (노드 ID: filter-internal)의 설정을 다음과 같이 바꿔줘.\n'
              '- 조건 1 (qty): 비교 열로 설정. execution_plan.filters[0].value: 3 → null; '
              'execution_plan.filters[0].compare_field: null → "limit"; value_type은 number로 유지.\n'
              '다른 작업의 설정은 유지해줘.')
    projected = public_request(legacy)
    assert '- 조건 1 (qty) 비교 기준: 숫자 3 → 열 "limit"' in projected
    assert not re.search(r'execution_plan|value_type|filter-internal|\bnull\b', projected)
    mismatched = legacy.replace('filters[0].compare_field', 'filters[1].compare_field')
    assert public_request(mismatched) == mismatched


def test_user_text_that_resembles_internal_paths_is_never_rewritten():
    node = _node({'query': 'arguments_json.max_results'})
    node['label'] = 'execution_plan.filters[0].value'
    edit = _edit(node, ['- 검색 조건: "arguments_json.max_results" → "arguments_json.max_results 보존"'], [
        _change('arguments_json.query', 'arguments_json.max_results', 'arguments_json.max_results 보존')])
    validate_visible_changes(edit, node)
    assert public_request(edit['request']) == edit['request']
    assert '"arguments_json.max_results"' in change_summary(edit)


@pytest.mark.parametrize('raw', [
    '노드의 이전 값이 변경되었습니다. 설정창을 다시 열어 주세요: arguments_json.max_results',
    '다른 단계나 설명에 이전 숫자가 남아 있어 자동 변경하지 않았습니다: execution_plan.filters[0].value',
    '현재 노드의 인자 JSON을 읽을 수 없습니다.',
    '변경한 설정을 실행할 수 없습니다: provider_body={"api_token":"hidden"}',
    '1 validation error for NodeSettings arguments_json private-provider-body',
    'HTTP 500 {"authorization":"secret"}',
])
def test_errors_have_a_reason_and_next_action_without_raw_details(raw):
    shown = public_error(raw)
    assert shown != raw
    assert '주세요' in shown
    assert not re.search(r'arguments_json|execution_plan|JSON|provider|api_token|authorization|NodeSettings|HTTP|hidden|secret', shown)


def test_old_error_detection_does_not_classify_normal_assistant_text():
    assert is_node_edit_error('노드의 이전 값이 변경되었습니다. 설정창을 다시 열어 주세요: arguments_json.max_results')
    assert not is_node_edit_error('arguments_json.max_results라는 사용자 열을 그대로 두었습니다.')
    assert not is_node_edit_error('메일을 읽고 요약을 보냈습니다.')


def test_known_failure_reasons_and_gmail_range_remain_actionable():
    for raw, reason in [
        ('다른 단계나 설명에 이전 수량이 남아 있어 자동 변경하지 않았습니다: arguments_json.max_results', '이전 수량이 남아 있어'),
        ('다른 단계나 설명에 이전 설정값이 남아 있어 자동 변경하지 않았습니다: arguments_json.query', '다른 단계나 설명에 이전 설정값'),
        ('검색 조건은 작업 범위와 다른 단계의 설명에 연결돼 있어 자동 변경하지 않았습니다.', '검색 조건은 작업 범위'),
    ]:
        assert reason in public_error(raw)
    message = public_error('변경한 설정을 실행할 수 없습니다: mail: max_results 실행 인자의 허용 규격: {"type": "positive_integer", "maximum": 500}')
    assert '1~500건' in message and '정수' in message
    assert 'max_results' not in message
    ambiguous = '여러 메일 조회 작업이 같은 요약에 연결되어 있습니다. 어느 조회 한도를 적용할지 대화에서 확인해 주세요.'
    assert public_error(ambiguous) == ambiguous


@pytest.mark.parametrize('rule', [
    {'type': 'positive_integer', 'maximum': 2500},
    {'type': 'number', 'maximum': 500},
    {'maximum': 500},
    {'type': 'positive_integer', 'maximum': 500, 'minimum': 10},
])
def test_other_argument_domains_are_not_misrepresented_as_gmail_ranges(rule):
    raw = '변경한 설정을 실행할 수 없습니다: calendar: max_results 실행 인자의 허용 규격: ' + json.dumps(rule)
    assert '1~500' not in public_error(raw)
