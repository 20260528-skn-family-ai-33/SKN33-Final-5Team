"""Source-grounded sum + row count, preserving zero rows and both branches."""
import csv
import base64
import io
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from agent_builder.design.grouped_totals_request import grouped_totals_packet
from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check, validate_plan
from tests.design.test_manual_kit_requirement_regressions import REGIONAL_REQUEST


def upload_profile(columns, rows):
    def numeric(value):
        try:return isinstance(value,str) and bool(value.strip()) and math.isfinite(float(value))
        except ValueError:return False
    result = {}
    for column in columns:
        values = [r.get(column) for r in rows]
        filled = [v for v in values if v is not None and v != '']
        result[column] = dict(types=list({('boolean' if isinstance(v,bool) else 'number' if isinstance(v,(float,int)) else 'string') for v in filled}),
                              missing=len(values)-len(filled), numericStrings=sum(numeric(v) for v in filled))
    return dict(profile=result, row_count=len(rows))


def state():
    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    with source.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        columns, rows = reader.fieldnames, list(reader)
    return dict(messages=[dict(role='user', content=REGIONAL_REQUEST)], resources=[
        dict(id='table-source', name='주문내역.csv', kind='table', columns=columns, **upload_profile(columns,rows))])


def test_complete_request_needs_no_inference_and_count_uses_original_rows():
    class NoModel:
        def invoke(self, messages):
            pytest.fail('A complete source-grounded request must not loop in extraction')
    result = make_requirement_check('fixture', NoModel(), 'enforce')(state())
    assert result['requirement_route'] == 'plan', result.get('requirement_failure')
    tasks = result['requirement_ledger']['tasks']
    assert tasks[3]['depends_on'] == tasks[5]['depends_on'] == [tasks[2]['id']]
    assert tasks[7]['depends_on'] == [tasks[4]['id'], tasks[6]['id']]
    assert tasks[1]['spec']['filters'] == [dict(field='주문상태', operator='eq', value='완료', compare_field=None, value_type='text')]
    assert result['trace'][-1]['model_calls'] == 0


@pytest.mark.parametrize('replacement', [
    ' 추가로 서울만 남겨줘.', ' 이메일도 보내줘.', ' 단, 수량 0은 제외해줘.',
    ' 합계가 100000 이상인 지역만 남겨줘.',
])
def test_unconsumed_request_never_uses_partial_projection(replacement):
    current = state()
    current['messages'][0]['content'] += replacement
    assert grouped_totals_packet(current) is None


@pytest.mark.parametrize('change', ['file', 'column', 'second_source', 'later_change', 'base_agent', 'collision', 'ambiguous_formula'])
def test_ambiguous_or_changed_context_uses_general_extractor(change):
    current = state()
    if change == 'file':
        current['resources'][0]['name'] = 'other.csv'
    elif change == 'column':
        current['resources'][0]['columns'].remove('주문상태')
    elif change == 'second_source':
        current['resources'].append({**current['resources'][0], 'id':'other'})
    elif change == 'later_change':
        current['messages'].append(dict(role='user', content='서울만 남겨줘.'))
    elif change == 'base_agent':
        current['base_agent_id'] = 'saved'
    elif change == 'collision':
        current['messages'][0]['content'] = REGIONAL_REQUEST.replace('총매출', '주문건수')
    else:
        current['messages'][0]['content'] = REGIONAL_REQUEST.replace('수량', 'order-qty')
        current['resources'][0]['columns'].append('order-qty')
    assert grouped_totals_packet(current) is None


def test_columns_status_aliases_and_sort_direction_are_not_fixture_constants():
    current = state()
    replacements = {'주문내역':'입고내역', '주문상태':'입고상태', '주문금액':'입고액', '주문건수':'입고건수',
                    '완료':'승인', '주문':'입고', '수량':'박스수', '단가':'박스가격', '지역':'창고', '총매출':'총액',
                    '오름차순':'내림차순', '곱하기':'×'}
    for old, new in replacements.items():
        current['messages'][0]['content'] = current['messages'][0]['content'].replace(old, new)
        current['resources'][0]['name'] = current['resources'][0]['name'].replace(old, new)
        current['resources'][0]['columns'] = [c.replace(old, new) for c in current['resources'][0]['columns']]
        current['resources'][0]['profile'] = {c.replace(old,new):v for c,v in current['resources'][0]['profile'].items()}
    specs = [t['spec'] for t in extract_plan(grouped_totals_packet(current), current)['tasks']]
    assert specs[1]['filters'][0]['value'] == '승인'
    assert specs[2] == dict(kind='compute', formula='박스수 * 박스가격', output_column='입고액')
    assert specs[3]['sum_field'] == '입고액'
    assert specs[7]['sort'][0]['direction'] == 'descending'
    assert specs[8]['fields'] == ['창고', '총액', '입고건수']


@pytest.mark.parametrize('mutation', ['threshold', 'status', 'formula', 'count', 'rename', 'sort', 'format', 'lineage'])
def test_corrupted_or_cached_plans_cannot_bypass_source_validation(mutation):
    current = state()
    plan = extract_plan(grouped_totals_packet(current), current)
    tasks = plan['tasks']
    if mutation == 'threshold':
        tasks[1]['spec']['filters'].append(dict(field='수량', operator='gt', value=0, compare_field=None, value_type='number'))
    elif mutation == 'status': tasks[1]['spec']['filters'][0]['value'] = '취소'
    elif mutation == 'formula': tasks[2]['spec']['formula'] = '수량 + 단가'
    elif mutation == 'count': del tasks[5]
    elif mutation == 'rename': tasks[4]['spec']['target'] = '총액'
    elif mutation == 'sort': tasks[7]['spec']['sort'][0]['direction'] = 'descending'
    elif mutation == 'format': tasks[-1]['spec']['format'] = 'json'
    else: tasks[5]['depends_on'] = [tasks[4]['id']]
    with pytest.raises(ValueError):
        validate_plan(deepcopy(plan), current)


def sealed_state():
    current = state()
    return {**current, **make_requirement_check('fixture', mode='enforce')(current)}


def controlled_draft(current):
    from agent_builder.design.native_settings import expand_node_settings
    from agent_builder.design.grouped_totals_decoder import grouped_totals_steps
    from tests.design.test_feedback_service_scenarios import order_draft
    draft = order_draft(current['resources'][0]['id'])
    steps = grouped_totals_steps(current)
    nodes = [dict(id='step_'+str(i), label=step['tool'], tool=step['tool'],
                  settings={**step['settings'], 'operation':step['operation']},
                  bindings=[dict(input=b['input'], node_id='step_'+str(b['step']), output=b['output']) for b in step['bindings']])
             for i,step in enumerate(steps, 1)]
    draft.update(instructions=REGIONAL_REQUEST, requirements=[dict(source_id='user', quote=REGIONAL_REQUEST, description='지역별 합계와 행 개수')],
                 tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),
                 workflow=dict(entry=nodes[0]['id'], nodes=nodes, edges=[
                     dict(source=a['id'], target=b['id'], branch='next') for a,b in zip(nodes,nodes[1:])]))
    return expand_node_settings(draft)


def test_decoder_requires_common_source_both_group_results_and_exact_csv_columns(monkeypatch):
    from contracts.models import AgentDraft
    from jsonschema import Draft202012Validator
    from agent_builder.design.grouped_totals_decoder import constrain_grouped_totals
    from agent_builder.design.operation_steps import schema_for
    from tests.design.test_beta_feedback import native_schema
    from workflow_engine.catalog.canonical import operation_variants
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    current = sealed_state()
    original = AgentDraft.model_json_schema()
    base = native_schema()
    aggregate = deepcopy(base['$defs']['WorkflowNode']['anyOf'][0])
    aggregate['properties']['tool'] = {'enum':['aggregate_table']}
    base['$defs']['WorkflowNode']['anyOf'].append(aggregate)
    base['$defs']['WorkflowNode']['anyOf'] += operation_variants(
        original['$defs']['WorkflowNode'], original['$defs']['Binding'],
        {'join_tables','filter_sort','json_transform','export_result'}, set(current['resources'][0]['columns']))
    wire = schema_for(base)
    result = constrain_grouped_totals(wire, current)
    slots = result['$defs']['WorkflowDefinition']['properties']['next_steps']['properties']
    count = Draft202012Validator(slots['step_6']['anyOf'][0]['properties']['bindings'])
    count.validate([dict(input='table', step=3, output='table')])
    assert not count.is_valid([dict(input='table', step=4, output='table')])
    join = Draft202012Validator(slots['step_8']['anyOf'][0]['properties']['bindings'])
    join.validate([dict(input='left', step=5, output='table'), dict(input='right', step=7, output='table')])
    assert not join.is_valid([dict(input='left', step=5, output='table')])
    output = Draft202012Validator(slots['step_12']['anyOf'][0]['properties']['settings']['properties']['arguments_json'])
    output.validate({'path':'지역별매출.csv','columns':['지역','총매출','주문건수']})
    assert not output.is_valid({'path':'지역별매출.csv','columns':['지역','주문건수']})
    assert constrain_grouped_totals(wire, {**current, 'requirement_digest':'stale'}) == wire


@pytest.mark.parametrize('wrong', ['count_source', 'lost_branch', 'join_key', 'extra_filter',
                                 'missing_status', 'global_exclude', 'count_error'])
def test_graph_changes_cannot_pass_save_coverage(wrong):
    from agent_builder.design.requirement_coverage import coverage_report
    current = sealed_state()
    draft = controlled_draft(current)
    assert coverage_report(current, draft)['issues'] == []
    nodes = draft['workflow']['nodes']
    if wrong == 'count_source': nodes[5]['bindings'][0]['node_id'] = nodes[3]['id']
    elif wrong == 'lost_branch': nodes[-1]['bindings'][0]['node_id'] = nodes[4]['id']
    elif wrong == 'join_key': nodes[7]['settings']['arguments_json']['keys'] = ['총매출']
    elif wrong == 'extra_filter': nodes[1]['settings']['execution_plan']['filters'].append(dict(field='수량',operator='gt',value=0,compare_field=None,value_type='number'))
    elif wrong == 'missing_status': nodes[1]['settings']['execution_plan']['missing_policy'] = 'keep'
    elif wrong == 'global_exclude':
        draft['execution_plan']['missing_policy'] = 'exclude'
        for node in (nodes[3],nodes[5]): node['settings']['execution_plan'] = None
    else: nodes[5]['settings']['execution_plan']['missing_policy'] = 'error'
    assert coverage_report(current, draft)['issues']


def test_explicit_aggregate_policy_overrides_inherited_policy():
    from agent_builder.design.requirement_coverage import coverage_report
    current = sealed_state()
    draft = controlled_draft(current)
    draft['execution_plan']['missing_policy'] = 'exclude'
    assert coverage_report(current, draft)['issues'] == []


@pytest.mark.parametrize('profile', [None, {}, {'types':['string'],'missing':0,'numericStrings':1},
                                   {'types':['string','number'],'missing':0,'numericStrings':0}])
def test_unknown_or_mixed_group_type_uses_general_extractor(profile):
    current = state()
    current['resources'][0]['profile']['지역'] = profile
    assert grouped_totals_packet(current) is None


@pytest.mark.parametrize('variant', ['original', 'captured_model', 'cancelled_invalid_number', 'different_data',
                                     'empty_status', 'zero_completed', 'numeric_group', 'empty_group'])
def test_sum_count_save_execute_csv(variant, tmp_path, monkeypatch):
    import service.handlers.design as worker
    from service.worker import handle
    from tests.design.test_feedback_service_scenarios import UnexpectedConditionCall
    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    with source.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        columns, rows = reader.fieldnames, list(reader)
    with source.with_name('t02_expected.csv').open(encoding='utf-8-sig', newline='') as file:
        expected = list(csv.DictReader(file))
    request = REGIONAL_REQUEST
    if variant == 'cancelled_invalid_number':
        rows.append({**rows[0], '주문상태':'취소', '수량':'숫자 아님'})
    elif variant == 'empty_status':
        rows += [{**rows[0],'주문상태':missing,'수량':'7','단가':'10'} for missing in ('',None,'  ')]
    elif variant == 'zero_completed':
        rows = [{**rows[0],'주문상태':'취소'}]
        expected = []
    elif variant == 'numeric_group':
        request = request.replace('지역','연령')
        columns = [c.replace('지역','연령') for c in columns]
        rows = [{**{k:v for k,v in rows[0].items() if k != '지역'},'연령':age,'수량':'1','단가':'10'} for age in ('12','8')]
        expected = [{'연령':age,'총매출':'10','주문건수':'1'} for age in ('8','12')]
    elif variant == 'empty_group':
        rows = [{**rows[0],'지역':'','수량':'7','단가':'10'}, {**rows[0],'지역':'서울','수량':'0','단가':'10'}]
        # Sorting places missing group values last; neither aggregate may drop them.
        expected = [{'지역':'서울','총매출':'0','주문건수':'1'}, {'지역':'','총매출':'70','주문건수':'1'}]
    elif variant == 'different_data':
        rows = [{**rows[0], '지역':'제주', '수량':'0', '단가':'999'},
                {**rows[0], '지역':'제주', '수량':'2', '단가':'7'},
                {**rows[0], '지역':'강원', '수량':'3', '단가':'5'},
                {**rows[0], '지역':'강원', '주문상태':'취소', '수량':'100', '단가':'500'}]
        expected = [{'지역':'강원','총매출':'15','주문건수':'1'}, {'지역':'제주','총매출':'14','주문건수':'2'}]
    build = worker.build_graph
    class Capabilities:
        def invoke(self, messages):
            return {'content':json.dumps(dict(decision='existing_tools', reason='표 계산과 그룹 집계',
                tool_ids=['read_table','calculate_columns','filter_sort','aggregate_table','join_tables','json_transform','export_result'],
                missing_conditions=[],missing_actions=[],user_quotes=[request]))}
    class NoExtraction:
        def invoke(self, messages): pytest.fail('Complete contract must not invoke requirement model')
    monkeypatch.setattr(worker,'build_graph',lambda *a,**kw:build(*a,**{**kw,
        'capability_mode':'enforce','capability_transport':Capabilities(), 'requirement_mode':'enforce',
        'requirement_transport':NoExtraction(),'condition_mode':'enforce',
        'condition_transport':UnexpectedConditionCall(),'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    result = handle({**base,'action':'new_session','model':'fixture','credential_id':'fixture'})
    sid = result['id']
    result = handle({**base,'action':'upload','id':sid,'revision':0,
                     'asset':dict(name='주문내역.csv',kind='table',format='csv',columns=columns, **upload_profile(columns,rows)),'rows':rows})
    current = state()
    current['messages'][0]['content'] = request
    current['resources'][0].update(columns=columns, **upload_profile(columns,rows))
    current['resources'][0]['id'] = result['resources'][0]['id']
    current.update(make_requirement_check('fixture', mode='enforce')(current))
    draft = controlled_draft(current)
    draft['instructions'] = request
    draft['requirements'][0]['quote'] = request
    if variant == 'captured_model':
        recorded = json.loads(source.with_name('t02_model_response.json').read_text(encoding='utf-8'))
        draft = json.loads(json.dumps(recorded['draft'], ensure_ascii=False).replace('table-source', current['resources'][0]['id']))
        # Keep the captured response immutable. The current contract deliberately
        # requires null groups to survive the join of the two aggregate branches.
        from workflow_engine.catalog.canonical import parse_arguments
        join = next(node for node in draft['workflow']['nodes'] if node['tool'] == 'join_tables')
        args = parse_arguments(join['settings'])
        assert args['null_keys'] == 'error'
        join['settings']['arguments_json'] = json.dumps({**args, 'null_keys': 'match'})
    def conversation(actual):
        assert not actual.get('issues'), actual['issues']
        return dict(draft=deepcopy(draft))
    result = handle({**base,'action':'message','id':sid,'revision':result['revision'],'content':request},
                    conversation=conversation)
    assert result['status'] == 'ready', result.get('issues')
    result = handle({**base,'action':'confirm','id':sid,'revision':result['revision']},
                    conversation=conversation)
    assert result['status'] == 'saved', result.get('issues')
    run = handle({**base,'action':'test','id':result['saved_agent_id'],'content':''})
    assert run['status'] == 'succeeded', run.get('error')
    reader = csv.DictReader(io.StringIO(base64.b64decode(run['artifact']['content']).decode('utf-8-sig')))
    assert reader.fieldnames == ['연령' if variant == 'numeric_group' else '지역','총매출','주문건수']
    assert list(reader) == expected
