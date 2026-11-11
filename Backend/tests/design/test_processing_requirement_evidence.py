"""The public-9B M04 candidate was a correctly bound scheduled table export."""
import pytest

from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.processing_guard import processing_issues
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
from tests.design.test_task_omission import processing_request
from tests.workflow_engine.test_workflow import bind, edge, node


def test_m04_validated_read_export_remains_ready_with_an_extra_candidate():
    request = '한국 시간으로 매일 표를 JSON으로 저장해줘.'
    answer = '매일 오전 9시 정각으로 해줘.'
    state = processing_request([
        node('read', 'read_table'), node('out', 'write_result', [bind('table', 'read', 'table')], format='json')
    ], [edge('read', 'out')])
    state['messages'] = [dict(role='user', content=request), dict(role='user', content=answer)]
    state['resources'] = [dict(id='sales', name='매출표', kind='table', columns=['amount'])]
    state['user_timezone'] = 'Asia/Seoul'
    schedule = dict(frequency='daily', timezone='Asia/Seoul', hour=9, minute=0,
                    weekday=None, monthday=None, month_end=False)
    state['draft'].update(name='매출표 일일 보관', document_ids=['sales'], schedule=schedule,
                          report_destination='reports', requirements=[dict(description='예약 저장', source_id='user', quote=request)])
    state['draft']['execution_plan']['output_format'] = 'json'
    state['capability_assessment'].update(tool_ids=['read_table','filter_sort','write_result'], user_quotes=[request])
    state['capability_fingerprint'] = input_fingerprint(state)
    ledger = dict(tasks=[
        dict(id='r1', source_message=0, description='예약', quote=request, depends_on=[],
             spec=dict(kind='schedule', schedule=schedule)),
        dict(id='r2', source_message=0, description='JSON 보관', quote=request, depends_on=['r1'],
             spec=dict(kind='export', format='json')),
    ], unresolved=[])
    state.update(requirement_required=True, requirement_ledger=ledger, requirement_digest=plan_digest(ledger),
                 requirement_fingerprint=ledger_fingerprint(state))
    assert processing_issues(state, state['draft']) == []
    result = validate_node(state)
    assert result['status'] == 'ready', result['issues']


def test_missing_mail_summary_cannot_be_excused_by_a_source_only_recommendation():
    from tests.design.test_node_completeness import state as mail_state
    state = mail_state(summary=False)
    state['capability_assessment']['tool_ids'] = ['email.list']
    assert processing_issues(state, state['draft'])


def test_serializing_an_unprocessed_table_still_omits_the_calculation():
    state = processing_request()
    state['draft']['workflow']['nodes'].append(node('out', 'export_result', operation='serialize'))
    assert processing_issues(state, state['draft'])


def test_active_ledger_cannot_turn_an_explicit_group_aggregation_into_a_copy():
    from agent_builder.design.requirement_ledger import extract_plan

    request = '매출표에서 region별 amount 합계를 집계하고 JSON으로 저장해줘.'
    state = processing_request([
        node('read', 'read_table'), node('out', 'write_result', [bind('table', 'read', 'table')], format='json'),
    ], [edge('read', 'out')])
    state['messages'] = [dict(role='user', content=request)]
    state['resources'] = [dict(id='sales', kind='table', name='매출표', columns=['region', 'amount'])]
    state['capability_assessment'].update(tool_ids=['read_table', 'write_result'], user_quotes=[request])
    state['capability_fingerprint'] = input_fingerprint(state)
    state['draft'].update(document_ids=['sales'], requirements=[dict(description='집계', source_id='user', quote=request)])
    tasks = [dict(source_message=0, description='매출표 읽기', spec=dict(kind='read', file_ids=['sales'])),
             dict(source_message=0, description='JSON 저장', spec=dict(kind='export', format='json'))]
    omitted = dict(tasks=[dict(**task, id=f'r{i}', quote=request, depends_on=[] if i==1 else ['r1'])
                          for i,task in enumerate(tasks, 1)], unresolved=[])
    state.update(requirement_required=True, requirement_ledger=omitted, requirement_digest=plan_digest(omitted),
                 requirement_fingerprint=ledger_fingerprint(state))
    assert validate_node(state)['status'] != 'ready'
    with pytest.raises(ValueError, match='집계'):
        extract_plan(dict(tasks=tasks, unresolved=[]), state)


def test_same_tool_and_value_column_do_not_cover_a_different_requested_group():
    from agent_builder.design.requirement_ledger import extract_plan

    request = '연결한 표의 store별 units 합계를 집계해줘.'
    state = {
        'messages': [dict(role='user', content=request)],
        'resources': [dict(id='sales', kind='table', columns=['region', 'store', 'units'])],
    }
    candidate = dict(source_message=0, description='매장별 합계',
                     spec=dict(kind='aggregate', fields=['region'], aggregation='sum', sum_field='units'))
    with pytest.raises(ValueError, match='집계'):
        extract_plan(dict(tasks=[candidate], unresolved=[]), state)


def test_cancelled_or_quoted_processing_request_is_not_a_processing_requirement():
    state = processing_request()
    state['messages'].append(dict(role='user', content='계산하지 말고 원본만 "정렬해줘"라는 파일명으로 저장해줘.'))
    state['capability_fingerprint'] = input_fingerprint(state)
    assert processing_issues(state, state['draft']) == []


@pytest.mark.parametrize('unresolved', ['결과 열 이름을 알려주세요.', {'key':'compute.output_column', 'text':'결과 열 이름을 알려주세요.'}])
def test_unresolved_coverage_keeps_user_question_visible(unresolved):
    from agent_builder.design.requirement_coverage import coverage_report
    from tests.design.test_requirement_coverage import seed

    state = seed()
    state['requirement_ledger']['unresolved'] = [unresolved]
    state['requirement_digest'] = plan_digest(state['requirement_ledger'])
    report = coverage_report(state, state['draft'])
    assert report['issues'] and '결과 열 이름을 알려주세요.' in report['issues'][0]
    assert 'sequence item' not in report['issues'][0] and 'validation error' not in report['issues'][0]
