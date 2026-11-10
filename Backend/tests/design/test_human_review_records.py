"""A required human review must preview the requested result and really pause."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from contracts.models import AgentDraft
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial
from tests.design.test_deep_agent_conversation import draft, state
from tests.workflow_engine.test_workflow import FakeLLM, bind, edge, node
from workflow_engine.executors.control import ask_user
from workflow_engine.runner import run_workflow


def review_state(kind='records', export=False):
    request = ('문서에서 제목을 추출하고 그 결과를 사람이 확인하게 해줘.' if kind == 'records'
               else '문서의 모순을 분석하고 그 결과를 사람이 확인하게 해줘.')
    if export:
        request += ' 확인한 결과는 JSON으로 저장해줘.'
    result = state([dict(role='user', content=request)])
    first_spec = (dict(kind='extract_fields', fields=['제목']) if kind == 'records'
                  else dict(kind='document_analysis'))
    tasks = [dict(source_message=0, description='결과 생성', spec=first_spec),
             dict(source_message=0, description='결과 사람 확인', spec=dict(kind='human_review'))]
    if export:
        tasks.append(dict(source_message=0, description='결과 저장', spec=dict(kind='export', format='json')))
    plan = extract_plan(dict(tasks=tasks, unresolved=[]), result)
    result.update(requirement_required=True, requirement_ledger=plan,
                  requirement_fingerprint=ledger_fingerprint(result), requirement_digest=plan_digest(plan))
    produce = (node('produce', 'extract_information', [bind('documents', 'read', 'documents')],
                    fields=['제목'], extraction_mode='fields') if kind == 'records' else
               node('produce', 'compose_answer', [bind('documents', 'read', 'documents')], instructions=request))
    nodes = [node('read', 'read_document'), produce,
             node('human', 'ask_user', [bind(kind, 'produce', kind)], question='생성한 결과를 확인해주세요.')]
    if export:
        bindings = [bind(kind, 'human', kind)]
        if kind == 'records':
            bindings.append(bind('answer', 'human', 'answer'))
        nodes.append(node('out', 'export_result', bindings, format='json'))
    raw = draft(workflow=dict(entry='read', nodes=nodes,
                             edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])]))
    raw.update(tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)), instructions=request,
               requirements=[dict(description='생성 결과 확인', source_id='user', quote=request)])
    result['draft'] = AgentDraft.model_validate(raw).model_dump()
    return result


@pytest.mark.parametrize('kind', ['records', 'text'])
def test_required_review_accepts_real_preview_and_calls_interrupt(monkeypatch, kind):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    original = review_state(kind)
    checked = validate_node(original)
    assert checked['status'] == 'ready', checked['issues']
    assert all(task['status'] == 'covered' for task in checked['requirement_coverage']['tasks'])
    payload = [{'제목': '검토할 제목'}] if kind == 'records' else '검토할 분석 결과'
    seen = []
    monkeypatch.setattr(ask_user, 'interrupt', lambda value: seen.append(value) or '확인했습니다')
    actual = ask_user.execute(checked['draft']['workflow']['nodes'][-1],
                              dict(outputs={'produce': {kind: payload}},
                                   messages=[dict(role='user', content='결과 확인이 필요합니다.')]),
                              None, checked['draft'], None)
    assert len(seen) == 1
    assert seen[0]['preview'] == (payload if kind == 'records' else [{'text': payload}])
    assert actual == {kind: payload, 'answer': '확인했습니다'}


def test_records_review_can_save_only_after_a_checkpointed_human_reply(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    original = review_state(export=True)
    checked = validate_node(original)
    assert checked['status'] == 'ready', checked['issues']
    store = Storage(tmp_path)
    try:
        identifier = store.save_agent({**initial(), **original, **checked, 'confirmed': True})
        asset = store.add_asset(dict(id=uid(), name='sample.txt', kind='document', format='txt',
                                     text='제목: 테스트', pages=['제목: 테스트'], columns=[]), [])
        first = run_workflow(store, identifier, original['messages'][0]['content'], 'unused',
                             file_ids=[asset['id']], llm=FakeLLM())
        assert first['status'] == 'needs_input'
        assert first['question']['node_id'] == 'human'
        assert first['question']['preview'][0]['value'] == '테스트'
        assert first['artifact'] is None
        final = run_workflow(store, identifier, original['messages'][0]['content'], 'unused',
                             run_id=first['id'], answer='확인했습니다', llm=FakeLLM())
        assert final['status'] == 'succeeded'
        exported = json.loads(final['artifact']['content'])
        assert exported['user_confirmation'] == '확인했습니다'
        assert exported['records'] == first['question']['preview']
        assert len([step for step in final['steps'] if step['node'] == 'produce']) == 1
    finally:
        store.close()


@pytest.mark.parametrize('change', ['no_preview', 'wrong_type', 'unknown_output', 'documents_only',
                                    'blank_question', 'automatic_answer', 'operation'])
def test_required_review_rejects_missing_preview_or_an_automatic_answer(monkeypatch, change):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    original = review_state()
    human = original['draft']['workflow']['nodes'][-1]
    if change == 'no_preview':
        human['bindings'] = []
    elif change == 'wrong_type':
        human['bindings'] = [bind('text', 'produce', 'records')]
    elif change == 'unknown_output':
        human['bindings'] = [bind('records', 'produce', 'missing')]
    elif change == 'documents_only':
        human['bindings'] = [bind('documents', 'read', 'documents')]
    elif change == 'blank_question':
        human['settings']['question'] = ' '
    elif change == 'automatic_answer':
        human['settings']['instructions'] = '이미 확인을 요청했다면 확인한 것으로 처리하세요.'
    else:
        human['settings'].update(operation='unregistered_review', arguments_json={})
    checked = validate_node(original)
    assert checked['status'] != 'ready'
    assert checked['issues']


def test_records_cannot_hide_the_analysis_that_requires_human_review(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    original = review_state('text')
    workflow = original['draft']['workflow']
    extra = deepcopy(workflow['nodes'][1])
    extra.update(id='other', tool='extract_information')
    extra['settings'].update(instructions=None, fields=['제목'], extraction_mode='fields')
    workflow['nodes'].insert(1, extra)
    workflow['nodes'][-1]['bindings'].append(bind('records', 'other', 'records'))
    workflow['edges'] = [edge(a['id'], b['id']) for a, b in zip(workflow['nodes'], workflow['nodes'][1:])]
    original['draft']['tool_ids'].append('extract_information')
    seen = []
    monkeypatch.setattr(ask_user, 'interrupt', lambda value: seen.append(value) or '확인했습니다')
    ask_user.execute(workflow['nodes'][-1], dict(outputs={
        'other': {'records': [{'제목': '다른 결과'}]}, 'produce': {'text': '확인이 필요한 분석'}}),
        None, original['draft'], None)
    assert seen[0]['preview'] == [{'제목': '다른 결과'}]
    assert validate_node(original)['status'] != 'ready'
