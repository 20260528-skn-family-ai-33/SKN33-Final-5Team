"""Latest real export requests govern both the ledger and executable graph."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.export_intent import explicit_export_intent
from agent_builder.design.output_selection_request import literal_output_format, output_selection_issues
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import (
    extract_plan, ledger_fingerprint, make_requirement_check, plan_digest,
)
from tests.design.test_feedback_requirement_extraction import packet, state
from tests.design.test_feedback_service_scenarios import order_draft


def followup(*replies):
    current = state()
    current['messages'].extend(dict(role='user', content=reply) for reply in replies)
    return current


def raw_export(fmt):
    raw = packet()
    if fmt is None:
        raw['tasks'] = [t for t in raw['tasks'] if t['spec']['kind'] not in ('export', 'select_fields')]
    else:
        raw['tasks'][-1]['spec']['format'] = fmt
    return raw


def sealed(current, plan):
    return {**current, 'requirement_required': True, 'requirement_ledger': plan,
            'requirement_fingerprint': ledger_fingerprint(current),
            'requirement_digest': plan_digest(plan)}


def json_draft():
    draft = order_draft('table-source')
    writer = draft['workflow']['nodes'][-1]
    writer['bindings'][0]['input'] = 'value'
    writer['settings'].update(operation='write_json',
                              arguments_json=json.dumps(dict(path='고액주문.json')))
    return draft


@pytest.mark.parametrize('reply', ['CSV 저장은 취소해줘.', 'CSV저장취소', 'CSV로저장하지마'])
@pytest.mark.parametrize('ack', [False, True])
def test_specific_csv_cancellation_rejects_ledger_and_graph(reply, ack):
    current = followup(reply, *(['네.'] if ack else []))
    intent = explicit_export_intent(current)
    assert intent['cancelled']
    assert intent['formats'] == set()
    assert intent['forbidden_formats'] == {'csv'}
    assert literal_output_format(current) is None
    with pytest.raises(ValueError, match='취소한 파일 저장'):
        extract_plan(packet(), current)
    cancelled_plan = extract_plan(raw_export(None), current)
    assert not any(t['spec']['kind'] == 'export' for t in cancelled_plan['tasks'])
    # Even a correctly sealed cancellation ledger must reject an extra writer.
    assert coverage_report(sealed(current, cancelled_plan), order_draft('table-source'))['issues']
    assert output_selection_issues(current, order_draft('table-source'))
    without_writer = order_draft('table-source')
    without_writer['workflow']['nodes'].pop()
    without_writer['workflow']['edges'].pop()
    assert coverage_report(sealed(current, cancelled_plan), without_writer)['issues'] == []
    # Re-signing stale extraction cannot bypass current user intent either.
    stale_plan = extract_plan(packet(), state())
    assert coverage_report(sealed(current, stale_plan), order_draft('table-source'))['issues']


@pytest.mark.parametrize('reply', ['JSON 저장은 취소해줘.', 'JSON만 취소해줘.', 'JSON으로 저장하지 마.'])
def test_unrequested_json_cancellation_keeps_csv(reply):
    current = followup(reply, '네.')
    assert explicit_export_intent(current)['formats'] == {'csv'}
    assert not explicit_export_intent(current)['cancelled']
    plan = extract_plan(packet(), current)
    assert coverage_report(sealed(current, plan), order_draft('table-source'))['issues'] == []


@pytest.mark.parametrize('cancelled, remaining', [('csv', 'json'), ('json', 'csv')])
@pytest.mark.parametrize('ack', [False, True])
def test_cancel_one_of_two_formats_and_resume(cancelled, remaining, ack):
    current = followup('같은 열을 JSON으로도 저장해줘.', f'{cancelled.upper()}만 취소해줘.',
                       *(['네.'] if ack else []))
    intent = explicit_export_intent(current)
    assert intent['formats'] == {remaining}
    assert intent['forbidden_formats'] == {cancelled}
    assert not intent['cancelled']
    assert not multiple_outputs(current)
    with pytest.raises(ValueError):
        extract_plan(raw_export(cancelled), current)
    plan = extract_plan(raw_export(remaining), current)
    draft = json_draft() if remaining == 'json' else order_draft('table-source')
    assert coverage_report(sealed(current, plan), draft)['issues'] == []
    current['messages'].append(dict(role='user', content=f'{cancelled.upper()}으로도 저장해줘.'))
    intent = explicit_export_intent(current)
    assert intent['formats'] == {'csv', 'json'}
    assert intent['forbidden_formats'] == set()
    assert multiple_outputs(current)
    raw = packet()
    raw['tasks'].append(dict(source_message=2, description='JSON 저장', spec=dict(kind='export', format='json')))
    plan = extract_plan(raw, current)
    assert any('복수 결과 파일' in issue for issue in coverage_report(sealed(current, plan), draft)['issues'])


@pytest.mark.parametrize('reply', ['같은 열을 JSON으로 저장해줘.', 'CSV 말고 JSON으로 저장해줘.',
                                   '저장 형식을 JSON으로 변경해줘.'])
@pytest.mark.parametrize('ack', [False, True])
def test_later_single_format_replacement_rejects_csv_and_accepts_json(reply, ack):
    current = followup(reply, *(['네.'] if ack else []))
    assert literal_output_format(current) == (None if '변경' in reply else 'json')
    with pytest.raises(ValueError, match='결과 파일 저장'):
        extract_plan(packet(), current)
    plan = extract_plan(raw_export('json'), current)
    assert coverage_report(sealed(current, plan), order_draft('table-source'))['issues']
    assert coverage_report(sealed(current, plan), json_draft())['issues'] == []
    stale_plan = extract_plan(packet(), state())
    assert coverage_report(sealed(current, stale_plan), order_draft('table-source'))['issues']
    if '변경' not in reply:
        missing = raw_export('json')
        missing['tasks'].pop()
        assert extract_plan(missing, current)['tasks'][-1]['spec'] == dict(kind='export', format='json')


def test_cancelled_format_stays_forbidden_when_other_export_is_active():
    current = followup('CSV 저장은 취소해줘.', 'JSON으로 저장해줘.', '네.')
    plan = extract_plan(raw_export('json'), current)
    assert explicit_export_intent(current)['forbidden_formats'] == {'csv'}
    draft = json_draft()
    extra = deepcopy(order_draft('table-source')['workflow']['nodes'][-1])
    extra['id'] = 'cancelled_csv'
    draft['workflow']['nodes'].insert(-1, extra)
    draft['workflow']['edges'] = [
        dict(source=a['id'], target=b['id'], branch='next')
        for a, b in zip(draft['workflow']['nodes'], draft['workflow']['nodes'][1:])]
    assert coverage_report(sealed(current, plan), draft)['issues']


def test_requirement_check_retries_stale_csv_and_preserves_json_after_ack():
    current = followup('같은 열을 JSON으로 저장해줘.')

    class Transport:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            raw = packet() if self.calls == 1 else raw_export('json')
            return {'content': json.dumps(raw, ensure_ascii=False)}

    transport = Transport()
    check = make_requirement_check('fixture', transport, 'enforce')
    result = check(current)
    assert transport.calls == 2
    assert result['requirement_route'] == 'plan'
    resumed = {**current, **result}
    resumed['messages'] = current['messages'] + [dict(role='user', content='네.')]
    again = check(resumed)
    assert again['requirement_route'] == 'plan'
    assert again['requirement_ledger']['tasks'][-1]['spec'] == dict(kind='export', format='json')


@pytest.mark.parametrize('reply', ['CSV 저장은 취소해줘.', 'JSON으로 저장해줘.'])
def test_format_validation_does_not_depend_on_a_named_column_projection(reply):
    current = state()
    current['messages'] = [dict(role='user', content='첨부 표를 CSV로 저장해줘.'),
                           dict(role='user', content=reply), dict(role='user', content='네.')]
    raw = dict(tasks=[dict(source_message=0, description='원본 읽기',
                           spec=dict(kind='read', file_ids=['table-source'])),
                      dict(source_message=0, description='CSV 저장', spec=dict(kind='export', format='csv'))],
               unresolved=[])
    with pytest.raises(ValueError):
        extract_plan(raw, current)
    if '취소' in reply:
        raw['tasks'].pop()
    else:
        raw['tasks'][-1]['source_message'] = 1
        raw['tasks'][-1]['spec']['format'] = 'json'
    assert extract_plan(raw, current)['unresolved'] == []


@pytest.mark.parametrize('cancelled, remaining', [('CSV', 'json'), ('JSON', 'csv')])
def test_combined_format_request_cancellation_clears_old_multiple_output_scope(cancelled, remaining):
    current = followup('CSV+JSON으로 저장해줘.', f'{cancelled} 저장은 취소해줘.', '네.')
    assert explicit_export_intent(current)['formats'] == {remaining}
    assert not multiple_outputs(current)
    plan = extract_plan(raw_export(remaining), current)
    draft = json_draft() if remaining == 'json' else order_draft('table-source')
    assert coverage_report(sealed(current, plan), draft)['issues'] == []


def cancelled_processing_draft(terminal='records'):
    draft = order_draft('table-source')
    count = 3 if terminal == 'table' else 5
    draft['workflow']['nodes'] = draft['workflow']['nodes'][:count]
    draft['workflow']['edges'] = draft['workflow']['edges'][:count - 1]
    draft['tool_ids'] = list(dict.fromkeys(n['tool'] for n in draft['workflow']['nodes']))
    return draft


@pytest.mark.parametrize('terminal', ['table', 'records', 'ask_user', 'finish'])
def test_cancelled_export_still_proves_final_processed_rows(terminal):
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.workflow_engine.test_workflow import node, bind, edge

    current = followup('CSV 저장은 취소해줘.', '네.')
    draft = cancelled_processing_draft(terminal)
    if terminal == 'ask_user':
        draft['workflow']['nodes'].append(node('done', 'ask_user', [bind('records', 'columns', 'records')],
                                               question='처리 결과를 확인해주세요.'))
        draft['workflow']['edges'].append(edge('columns', 'done'))
    elif terminal == 'finish':
        draft['workflow']['nodes'].append(node('done', 'finish', instructions='처리가 완료되었습니다.'))
        draft['workflow']['edges'].append(edge('columns', 'done'))
    assert numeric_filter_issues(current, draft) == []
    # An omitted writer without an actual cancellation remains an error.
    assert numeric_filter_issues(state(), draft)


@pytest.mark.parametrize('mistake', ['missing', 'threshold', 'or', 'bypass', 'literal', 'overwrite', 'text'])
def test_cancelled_export_cannot_drop_numeric_proof(mistake):
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.workflow_engine.test_workflow import node, bind, edge

    current = followup('CSV 저장은 취소해줘.', '네.')
    draft = cancelled_processing_draft()
    nodes = draft['workflow']['nodes']
    plan = deepcopy(nodes[2]['settings']['execution_plan'])
    nodes[2]['settings']['execution_plan'] = plan
    if mistake == 'missing':
        plan['filters'] = plan['filters'][:1]
    elif mistake == 'threshold':
        plan['filters'][1]['value'] = 1
    elif mistake == 'or':
        plan['match'] = 'any'
    elif mistake == 'bypass':
        nodes[3]['bindings'][0]['node_id'] = 'compute'
    elif mistake == 'literal':
        args = json.loads(nodes[-1]['settings']['arguments_json'])
        args['rows'] = [{'주문번호': 'WRONG', '주문금액': 1}]
        nodes[-1]['settings']['arguments_json'] = json.dumps(args)
    elif mistake == 'overwrite':
        nodes[3:] = [node('changed', 'calculate_columns', [bind('table', 'filter', 'table')],
                         table_operation='compute', calculation_formula='1', output_column='주문금액')]
        draft['workflow']['edges'] = draft['workflow']['edges'][:2] + [edge('filter', 'changed')]
    else:
        nodes[3]['settings'].update(output_port='text', arguments_json=json.dumps(dict(path=['columns'])))
        nodes.pop()
        draft['workflow']['edges'].pop()
    assert numeric_filter_issues(current, draft)


@pytest.mark.parametrize('bad_branch', [False, True])
def test_cancelled_export_proves_every_terminal_branch(bad_branch):
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.workflow_engine.test_workflow import node, bind, edge

    current = followup('CSV 저장은 취소해줘.')
    draft = cancelled_processing_draft('table')
    nodes = draft['workflow']['nodes']
    passthrough = {**deepcopy(draft['execution_plan']), 'filters': [], 'sort': []}
    nodes.extend([node('check', 'condition', [bind('table', 'filter', 'table')], condition_operator='empty'),
                  node('yes', 'filter_sort', [bind('table', 'check', 'table')], execution_plan=passthrough),
                  node('no', 'filter_sort', [bind('table', 'compute' if bad_branch else 'check', 'table')],
                       execution_plan=passthrough)])
    draft['workflow']['edges'].extend([edge('filter', 'check'), edge('check', 'yes', 'yes'), edge('check', 'no', 'no')])
    assert bool(numeric_filter_issues(current, draft)) == bad_branch
