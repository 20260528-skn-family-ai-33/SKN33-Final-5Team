"""Derived edit descriptions follow typed settings and actual data dependencies."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.gmail_slack import gmail_slack_reply
from agent_builder.design.gmail_summary import gmail_summary_reply
from agent_builder.design.node_edit import apply_node_edit, proof_issue
from agent_builder.design.node_edit_descriptions import (
    descriptions_to_check, sync_descriptions, sync_node_text,
)
from contracts.models import AgentDraft, ExecutionPlan, NodeSettings


def _node(identifier, tool, bindings=None, **settings):
    defaults = dict(file_ids=[], query=None, fields=[], instructions=None, format=None,
                    question=None, condition_field=None, condition_operator=None)
    return dict(id=identifier, tool=tool, label=identifier, bindings=bindings or [],
                settings=NodeSettings(**{**defaults, **settings}).model_dump())


def _bind(parent, port='table', output=None):
    return dict(input=port, node_id=parent, output=output or port)


def _edge(parent, child):
    return dict(source=parent, target=child, branch='next')


def _plan(value, field='amount'):
    return ExecutionPlan(filters=[dict(field=field, operator='gte', value=value,
                                      compare_field=None, value_type='number')],
                         sort=[], match='all', missing_policy='keep', duplicate_policy='keep',
                         duplicate_field=None, output_format='csv').model_dump()


def _table(value=1000000.0, extra=False):
    plan = _plan(value)
    nodes = [_node('read', 'read_table'),
             _node('filter', 'filter_sort', [_bind('read')], execution_plan=deepcopy(plan),
                   instructions=json.dumps(plan, ensure_ascii=False, indent=4)),
             _node('save', 'write_result', [_bind('filter')], format='csv')]
    edges = [_edge('read', 'filter'), _edge('filter', 'save')]
    if extra:
        nodes.insert(2, _node('other', 'filter_sort', [_bind('filter')],
                             execution_plan=_plan(value, 'unrelated'),
                             instructions=json.dumps(_plan(value, 'unrelated'))))
        nodes[-1]['bindings'] = [_bind('other')]
        edges = [_edge('read', 'filter'), _edge('filter', 'other'), _edge('other', 'save')]
    quote = '100만원 이상 자료를 CSV로 저장해줘'
    return AgentDraft(name='조건별 표 저장', purpose=quote,
                      instructions='금액 100만원 이상인 행만 저장합니다. ' + json.dumps(plan),
                      explanation='금액 100만원 이상인 행을 CSV로 만듭니다.',
                      tool_ids=['read_table', 'filter_sort', 'write_result'], document_ids=[],
                      requirements=[dict(description=quote, source_id='user', quote=quote)],
                      questions=[], unsupported=[], execution_plan=deepcopy(plan),
                      workflow=dict(entry='read', nodes=nodes, edges=edges)).model_dump()


def _filter_edit(old, new):
    return dict(node_id='filter', label='filter', changes=[dict(
        path='execution_plan.filters[0].value', old_value=old, new_value=new)])


def _synchronize(before, edit, new):
    after = deepcopy(before)
    old_node = next(node for node in before['workflow']['nodes'] if node['id'] == edit['node_id'])
    target = next(node for node in after['workflow']['nodes'] if node['id'] == edit['node_id'])
    target['settings']['execution_plan']['filters'][0]['value'] = new
    sync_node_text(old_node, target, edit['changes'])
    reconciled = sync_descriptions({}, before, after, edit, 1, 1)
    return after, reconciled


def test_currency_description_and_legacy_json_mirror_follow_actual_csv_plan():
    before = _table()
    original = deepcopy(before)
    edit = _filter_edit(1000000.0, 1500000)
    after, reconciled = _synchronize(before, edit, 1500000)
    assert reconciled
    for key in ('purpose', 'instructions', 'explanation'):
        assert '100만원' not in after[key]
        assert '1000000' not in after[key]
    target = after['workflow']['nodes'][1]
    assert json.loads(target['settings']['instructions']) == target['settings']['execution_plan']
    assert '1500000' in after['instructions']
    assert '"filters"' not in after['instructions']
    assert 'execution_plan' not in after['instructions']
    assert after['execution_plan'] == before['execution_plan']
    assert after['requirements'] == before['requirements']
    assert after['workflow']['nodes'][0] == before['workflow']['nodes'][0]
    assert after['workflow']['nodes'][-1] == before['workflow']['nodes'][-1]
    assert before == original
    assert descriptions_to_check(before, after, edit, reconciled) == ['조건별 표 저장', 'read', 'filter', 'save']


@pytest.mark.parametrize('old,new', [(-100.5, -200.25), (0, 0.5), (1.25, 3.75), ('1000000', '1500000')])
def test_table_description_uses_typed_values_without_numeric_substitution(old, new):
    before = _table(old, extra=True)
    # The same value in another condition has a different meaning.
    before['workflow']['nodes'][1]['settings']['execution_plan']['filters'].append(
        dict(field='second', operator='gte', value=old, compare_field=None, value_type='number'))
    before['workflow']['nodes'][1]['settings']['instructions'] = json.dumps(
        before['workflow']['nodes'][1]['settings']['execution_plan'], indent=2)
    edit = _filter_edit(old, new)
    after, reconciled = _synchronize(before, edit, new)
    assert reconciled
    changed = after['workflow']['nodes'][1]['settings']['execution_plan']['filters']
    assert changed[0]['value'] == new and type(changed[0]['value']) is type(new)
    assert changed[1]['value'] == old
    assert after['workflow']['nodes'][2] == before['workflow']['nodes'][2]
    assert json.dumps(new) in after['instructions'] and json.dumps(old) in after['instructions']
    assert json.dumps(old) not in '\n'.join(descriptions_to_check(before, after, edit, True))


def test_freeform_node_description_and_old_name_remain_guarded():
    before = _table()
    before['name'] = '100만원 이상 자료'
    before['workflow']['nodes'][1]['settings']['instructions'] = '금액 100만원 이상 자료만 남기세요.'
    edit = _filter_edit(1000000.0, 1500000)
    after, reconciled = _synchronize(before, edit, 1500000)
    check = descriptions_to_check(before, after, edit, reconciled)
    assert '100만원 이상 자료' in check
    assert '금액 100만원 이상 자료만 남기세요.' in check
    assert after['workflow']['nodes'][1]['settings']['instructions'] == before['workflow']['nodes'][1]['settings']['instructions']


def _mail_state(slack=False):
    prompt = ('Gmail에서 안 읽은 메일 최대 5건을 요약해서 Slack C0CH769U6BX에 보내줘'
              if slack else 'Gmail 최근 5개 메일 요약해줘')
    state = dict(messages=[dict(role='user', content=prompt)], resources=[], required_node_refs=[],
                 trace=[], node_library=[], model='gpt-4.1-mini', credential_id='fixture')
    reply = (gmail_slack_reply if slack else gmail_summary_reply)(state)
    assert reply
    return {**state, **reply, 'status': 'ready'}


def _popup(node, old, new):
    request = (f"`{node['label']}` 작업 (노드 ID: {node['id']})의 설정을 다음과 같이 바꿔줘.\n"
               f'- 조회 한도 (arguments_json.max_results): {old} → {new}\n'
               '다른 작업의 설정은 유지해줘.')
    return dict(request=request, node_id=node['id'], label=node['label'], changes=[dict(
        path='arguments_json.max_results', old_value=old, new_value=new)])


@pytest.mark.parametrize('slack', [False, True])
def test_mail_count_repeated_edits_and_failed_correction_preserve_quotes(slack):
    current = _mail_state(slack)
    requirements = deepcopy(current['draft']['requirements'])
    for old, new in ((5, 10), (10, 20)):
        reader = next(node for node in current['draft']['workflow']['nodes'] if node['id'] == 'mail')
        if old == 10:
            stale = _popup(reader, 999, new)
            failed = apply_node_edit({**current, 'messages': current['messages'] +
                                      [dict(role='user', content=stale['request'])], 'node_edit': stale})
            assert failed['edit_route'] == 'stop'
            assert '이전 값이 변경' in failed['issues'][0]
            current = {**current, **failed}
        edit = _popup(reader, old, new)
        submitted = {**current, 'messages': current['messages'] +
                     [dict(role='user', content=edit['request'])], 'node_edit': edit}
        result = apply_node_edit(submitted)
        assert result['edit_route'] == 'validate', result.get('issues')
        current = {**submitted, **result, 'status': 'ready'}
        assert proof_issue(current) is None
        assert current['draft']['requirements'] == requirements
        assert f'최대 {new}건' in current['draft']['workflow']['nodes'][1]['settings']['instructions']
        for key in ('purpose', 'instructions', 'explanation'):
            assert f'{old}건' not in current['draft'][key]
            assert f'{old}개' not in current['draft'][key]


def test_mail_summary_sync_follows_bindings_through_extra_steps_and_shuffled_nodes():
    current = _mail_state()
    before = deepcopy(current['draft'])
    nodes = before['workflow']['nodes']
    reader, summary = nodes
    middle = _node('middle', 'search_document', [_bind('mail', 'documents')], query='계약')
    summary['bindings'] = [_bind('middle', 'documents')]
    unrelated = _node('unrelated', 'compare_tables', operation='diff', arguments_json=json.dumps(
        dict(absolute_tolerance=5, format='csv')))
    before['workflow']['nodes'] = [unrelated, summary, middle, reader]
    before['workflow']['edges'] = [_edge('mail', 'middle'), _edge('middle', 'summary')]
    after = deepcopy(before)
    target = next(node for node in after['workflow']['nodes'] if node['id'] == 'mail')
    target['settings']['arguments_json'] = json.dumps(dict(max_results=10))
    edit = _popup(reader, 5, 10)
    sync_node_text(reader, target, edit['changes'])
    assert sync_descriptions(current, before, after, edit, len(current['messages']), len(current['messages']))
    changed = next(node for node in after['workflow']['nodes'] if node['id'] == 'summary')
    assert '최대 10건' in changed['settings']['instructions']
    for identifier in ('middle', 'unrelated'):
        assert next(node for node in after['workflow']['nodes'] if node['id'] == identifier) == next(
            node for node in before['workflow']['nodes'] if node['id'] == identifier)
    assert before['requirements'] == after['requirements']


def test_ambiguous_multiple_mail_readers_are_refused_in_plain_language():
    current = _mail_state()
    before = deepcopy(current['draft'])
    other = deepcopy(before['workflow']['nodes'][0])
    other['id'] = 'other_mail'
    before['workflow']['nodes'].append(other)
    before['workflow']['nodes'][1]['bindings'].append(_bind('other_mail', 'documents'))
    edit = _popup(before['workflow']['nodes'][0], 5, 10)
    after = deepcopy(before)
    with pytest.raises(ValueError, match='여러 메일 조회 작업'):
        sync_descriptions(current, before, after, edit, len(current['messages']), len(current['messages']))


def test_independent_reader_with_same_count_keeps_its_own_summary_and_settings():
    current = _mail_state()
    before = current['draft']
    reader, summary = before['workflow']['nodes']
    other_reader, other_summary = deepcopy(reader), deepcopy(summary)
    other_reader['id'] = 'other_mail'
    other_summary['id'] = 'other_summary'
    other_summary['bindings'] = [_bind('other_mail', 'documents')]
    before['workflow']['nodes'] = [other_summary, summary, other_reader, reader]
    before['workflow']['edges'] += [_edge('summary', 'other_mail'), _edge('other_mail', 'other_summary')]
    for key in ('name', 'purpose', 'instructions', 'explanation'):
        before[key] = '메일 두 묶음 조회와 요약'
    original_others = deepcopy((other_reader, other_summary))
    edit = _popup(reader, 5, 10)
    submitted = {**current, 'node_edit': edit, 'messages': current['messages'] +
                 [dict(role='user', content=edit['request'])]}
    result = apply_node_edit(submitted)
    assert result['edit_route'] == 'validate', result.get('issues')
    revised = {**submitted, **result}
    assert proof_issue(revised) is None
    nodes = {node['id']: node for node in result['draft']['workflow']['nodes']}
    assert '최대 10건' in nodes['summary']['settings']['instructions']
    assert (nodes['other_mail'], nodes['other_summary']) == original_others


def test_generic_count_field_syncs_only_unambiguous_target_label():
    before = _node('search', 'custom.search', operation='execute',
                   arguments_json=json.dumps(dict(limit=5, threshold=5)))
    before['label'] = '검색 결과 최대 5개 읽기'
    after = deepcopy(before)
    changes = [dict(path='arguments_json.limit', old_value=5, new_value=10)]
    sync_node_text(before, after, changes)
    assert after['label'] == '검색 결과 최대 10개 읽기'
    assert after['settings'] == before['settings']
    before['label'] = '5개 조회 후 5개 비교'
    after = deepcopy(before)
    sync_node_text(before, after, changes)
    assert after['label'] == before['label']
    before['label'] = '최근 5개월의 검색 결과'
    after = deepcopy(before)
    sync_node_text(before, after, changes)
    assert after['label'] == before['label']
