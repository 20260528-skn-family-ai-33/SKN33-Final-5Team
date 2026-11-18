"""PR151 review reproductions for inherited formats, polite commands and filenames."""
from copy import deepcopy
import base64
import csv
import json
from pathlib import Path

import pytest

from agent_builder.design.export_intent import explicit_export_intent
from agent_builder.design.grouped_totals_contract import contract_filename, grouped_contract
from agent_builder.design.output_selection_request import export_intent_issues, literal_output_format
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import (
    extract_plan, ledger_fingerprint, make_requirement_check, plan_digest,
)
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial, draft
from tests.design.test_feedback_requirement_extraction import state as order_state, packet
from tests.design.test_grouped_totals_request import state as regional_state, controlled_draft
from tests.workflow_engine.test_workflow import node, bind, edge, FakeLLM
from workflow_engine.runner import run_workflow


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return {'content': json.dumps(self.response, ensure_ascii=False)}


def seal(current, raw):
    current = {**initial(), **current}
    plan = extract_plan(raw, current)
    return {**current, 'requirement_required': True, 'requirement_ledger': plan,
            'requirement_fingerprint': ledger_fingerprint(current),
            'requirement_digest': plan_digest(plan)}


def inherited_writer_case(override=None):
    messages = [dict(role='user', content=text) for text in [
        '첨부 표를 CSV로 저장해줘.', '같은 표를 JSON으로도 저장해줘.', 'CSV만 취소해줘.',
    ]]
    current = dict(messages=messages, resources=[
        dict(id='table-source', name='table.json', kind='table', format='json', columns=['amount'])])
    raw = dict(tasks=[
        dict(source_message=0, description='표 읽기', spec=dict(kind='read', file_ids=['table-source'])),
        dict(source_message=2, description='JSON 저장', spec=dict(kind='export', format='json')),
    ], unresolved=[])
    current = seal(current, raw)
    nodes = [node('read', 'read_table', file_ids=['table-source']),
             node('inherited', 'write_result', [bind('table', 'read', 'table')], format=override),
             node('json', 'write_result', [bind('table', 'read', 'table')], format='json')]
    candidate = draft()
    candidate.update(document_ids=['table-source'], tool_ids=['read_table', 'write_result'],
                     instructions='\n'.join(m['content'] for m in messages),
                     requirements=[dict(source_id='user', quote=m['content'], description='저장 요청')
                                   for m in messages],
                     workflow=dict(entry='read', nodes=nodes, edges=[edge('read', 'inherited'), edge('inherited', 'json')]))
    candidate['execution_plan']['output_format'] = 'csv'
    current.update(draft=AgentDraft.model_validate(candidate).model_dump(), confirmed=True, status='ready')
    return current


@pytest.mark.parametrize('override', [None, 'csv'])
def test_cancelled_inherited_csv_is_rejected_by_coverage(override):
    current = inherited_writer_case(override)
    assert explicit_export_intent(current)['forbidden_formats'] == {'csv'}
    assert any('취소한 파일 저장 형식' in issue
               for issue in coverage_report(current, current['draft'])['issues'])


def test_cancelled_inherited_csv_cannot_be_persisted(tmp_path):
    current = inherited_writer_case()
    storage = Storage(str(tmp_path))
    try:
        storage.add_asset(current['resources'][0], [{'amount': 10}])
        with pytest.raises(ValueError, match='취소한 파일 저장 형식'):
            storage.save_agent(current)
        assert storage.agents() == []
    finally:
        storage.close()


def test_explicit_json_override_takes_precedence_over_global_csv():
    current = inherited_writer_case('json')
    assert export_intent_issues(current, current['draft']) == []
    assert coverage_report(current, current['draft'])['issues'] == []


def test_inherited_wrong_format_is_rejected_after_single_format_replacement():
    current = inherited_writer_case()
    current['messages'] = current['messages'][:1] + [dict(role='user', content='같은 표를 JSON으로 저장해줘.')]
    assert any('저장 형식' in issue for issue in export_intent_issues(current, current['draft']))


@pytest.mark.parametrize('suffix', ['하세요.', '해 주세요.', '해주세요.', '해줘.'])
def test_t02_initial_polite_save_uses_the_same_complete_contract(suffix):
    current = regional_state()
    current['messages'][0]['content'] = current['messages'][0]['content'].replace('저장해줘.', '저장' + suffix)
    transport = Transport(dict(tasks=[], unresolved=[]))
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert result['requirement_route'] == 'plan', result.get('requirement_failure')
    assert transport.calls == 0
    assert len(result['requirement_ledger']['tasks']) == 10
    assert result['requirement_ledger']['tasks'][-1]['spec'] == dict(kind='export', format='csv')
    assert grouped_contract(current)['formats'] == {'csv'}


@pytest.mark.parametrize('reply, expected', [
    ('같은 열을 JSON으로 저장하세요.', 'json'),
    ('CSV 저장은 취소하세요.', None),
    ('파일 저장은 취소하세요.', None),
])
@pytest.mark.parametrize('acknowledge', [False, True])
def test_t02_polite_followup_accepts_current_plan_and_rejects_stale_csv(reply, expected, acknowledge):
    current = regional_state()
    original = make_requirement_check('fixture', mode='enforce')(current)['requirement_ledger']
    raw = dict(tasks=[{key: task[key] for key in ('source_message', 'description', 'spec')}
                      for task in original['tasks']], unresolved=[])
    stale = deepcopy(raw)
    current['messages'].append(dict(role='user', content=reply))
    if acknowledge:
        current['messages'].append(dict(role='user', content='네.'))
    if expected:
        raw['tasks'][-1].update(source_message=1, spec=dict(kind='export', format=expected))
    else:
        raw['tasks'].pop()
    transport = Transport(raw)
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert result['requirement_route'] == 'plan', result.get('requirement_failure')
    assert transport.calls == 1
    formats = [task['spec']['format'] for task in result['requirement_ledger']['tasks']
               if task['spec']['kind'] == 'export']
    assert formats == ([expected] if expected else [])
    with pytest.raises(ValueError):
        extract_plan(stale, current)


def test_polite_named_column_export_is_projected_and_checked():
    current = order_state()
    current['messages'][-1]['content'] = current['messages'][-1]['content'].replace('저장해줘.', '저장하세요.')
    raw = packet()
    raw['tasks'].pop()
    assert literal_output_format(current) == 'csv'
    assert extract_plan(raw, current)['tasks'][-1]['spec'] == dict(kind='export', format='csv')


@pytest.mark.parametrize('reply', [
    '파일 저장은 취소하지 마세요.',
    'CSV로 저장하지 마세요라는 문구를 설명해 주세요.',
    '“같은 열을 JSON으로 저장하세요.”라는 예시야.',
    '‘CSV 저장은 취소하세요.’라는 예시야.',
    '같은 열을 JSON으로 저장하세요라는 문구를 확인했어.',
    '예를 들어 파일 저장은 취소하세요.',
])
def test_polite_negative_and_quoted_examples_keep_current_csv(reply):
    current = order_state()
    current['messages'].append(dict(role='user', content=reply))
    intent = explicit_export_intent(current)
    assert not intent['cancelled']
    assert intent['formats'] == {'csv'}
    assert extract_plan(packet(), current)['tasks'][-1]['spec']['format'] == 'csv'


@pytest.mark.parametrize('name, fmt, expected', [
    ('지역별매출.CSV', 'csv', '지역별매출.CSV'),
    ('지역별매출.CsV', 'csv', '지역별매출.CsV'),
    ('지역별매출.csv', 'csv', '지역별매출.csv'),
    ('지역별매출.CSV', 'json', '지역별매출.json'),
])
def test_contract_preserves_exact_filename_until_format_changes(name, fmt, expected):
    assert contract_filename(dict(output=name), fmt) == expected


@pytest.mark.parametrize('reply, expected_name', [
    (None, '지역별매출.CSV'),
    ('같은 열을 CSV로 저장하세요.', '지역별매출.CSV'),
    ('같은 열을 JSON으로 저장하세요.', '지역별매출.json'),
])
def test_t02_uppercase_filename_is_validated_saved_and_returned(reply, expected_name, tmp_path):
    current = regional_state()
    current['messages'][0]['content'] = current['messages'][0]['content'].replace('지역별매출.csv', '지역별매출.CSV')
    first = make_requirement_check('fixture', mode='enforce')(current)
    assert first['requirement_route'] == 'plan', first.get('requirement_failure')
    raw = dict(tasks=[{key: task[key] for key in ('source_message', 'description', 'spec')}
                      for task in first['requirement_ledger']['tasks']], unresolved=[])
    if reply:
        current['messages'].append(dict(role='user', content=reply))
        raw['tasks'][-1].update(source_message=1, spec=dict(kind='export', format='json' if 'JSON' in reply else 'csv'))
    current = seal(current, raw)
    candidate = controlled_draft(current)
    writer = candidate['workflow']['nodes'][-1]
    arguments = writer['settings']['arguments_json']
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    # The oracle is the caller's exact filename, independently of the decoder.
    assert arguments['path'] == expected_name
    candidate.update(instructions='\n'.join(m['content'] for m in current['messages']),
                     requirements=[dict(source_id='user', quote=m['content'], description='파일 요청')
                                   for m in current['messages']])
    current.update(draft=candidate, confirmed=True, status='ready')
    assert coverage_report(current, candidate)['issues'] == []
    storage = Storage(str(tmp_path))
    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    with source.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    try:
        storage.add_asset(current['resources'][0], rows)
        identifier = storage.save_agent(current)
        run = run_workflow(storage, identifier, '', 'unused', llm=FakeLLM())
        assert run['status'] == 'succeeded', run.get('error')
        assert run['artifact']['name'] == expected_name
        artifact = run['artifact']
        content = base64.b64decode(artifact['content']) if artifact.get('encoding') == 'base64' else artifact['content'].encode('utf-8')
        if expected_name.endswith('.CSV'):
            assert content == source.with_name('t02_expected.csv').read_bytes()
        else:
            assert json.loads(content) == [
                {'지역': '경기', '총매출': 110000, '주문건수': 3},
                {'지역': '부산', '총매출': 320000, '주문건수': 2},
                {'지역': '서울', '총매출': 310000, '주문건수': 4},
            ]
    finally:
        storage.close()
