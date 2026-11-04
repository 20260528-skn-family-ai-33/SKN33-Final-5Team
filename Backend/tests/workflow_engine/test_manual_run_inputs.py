"""Manual runs must prompt for the input the saved workflow actually consumes."""
import pytest

from service.worker import handle
from tests.workflow_engine.test_workflow import bind, edge, node, saved


def test_saved_legacy_table_plan_advertises_its_required_table(tmp_path):
    from agent_builder.conditions.input_requirements import execution_requirements
    from infrastructure.persistence.ids import uid
    from infrastructure.persistence.storage import Storage
    store = Storage(tmp_path)
    try:
        plan = {'tool_ids': ['read_table', 'write_result'], 'execution_plan': {'output_format': 'csv'}}
        assert execution_requirements(plan, store)[0]['ready'] is False
        asset = store.add_asset({'id':uid(), 'name':'orders.csv', 'kind':'table', 'columns':['id']}, [{'id':1}])
        plan['document_ids'] = [asset['id']]
        requirement, = execution_requirements(plan, store)
        assert requirement['ready'] and requirement['file_ids'] == [asset['id']]
    finally:
        store.close()


def test_deletion_cannot_leave_a_dangling_legacy_run_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from infrastructure.persistence.ids import uid
    from infrastructure.persistence.storage import Storage
    from service.handlers import uploads
    from tests.design.test_builder import draft, initial
    from workflow_engine.legacy_runtime import test_agent

    store = Storage(tmp_path)
    deleting = Storage(tmp_path)
    try:
        agent_id = store.save_agent({**initial(), 'draft': draft(), 'confirmed': True})
        upload = store.add_asset({'id':uid(), 'name':'new.csv', 'format':'csv', 'kind':'table',
                                  'columns':['id']}, [{'id':1}])
        read = store.asset
        def delete_after_read(identifier):
            parsed = read(identifier)
            delete = SimpleNamespace(storage=deleting, data={'id':identifier})
            uploads.prepare_delete(delete)
            uploads.finish_delete(delete)
            return parsed
        monkeypatch.setattr(store, 'asset', delete_after_read)
        monkeypatch.setattr('workflow_engine.legacy_runtime.create_chat_model',
                            lambda **_: pytest.fail('A deleted input must be rejected before model execution'))
        with pytest.raises(ValueError, match='자료가 삭제'):
            test_agent(store, agent_id, '', '', file_ids=[upload['id']])
        assert store.tests(agent_id) == []
    finally:
        deleting.close()
        store.close()


def test_missing_document_and_wrong_replacement_are_rejected_before_execution(tmp_path):
    workflow = {'entry': 'read', 'nodes': [node('read', 'read_document')], 'edges': []}
    store, identifier, _ = saved(tmp_path, workflow)
    store.close()
    job = {'directory': str(tmp_path), 'id': identifier}
    table = handle({**job, 'action': 'run_upload', 'asset': {'name': 'table.csv', 'kind': 'table',
                    'format': 'csv', 'columns': ['amount']}, 'rows': [{'amount': 10}]})['asset']
    for files in ([], [table['id']]):
        with pytest.raises(ValueError, match='문서|자료'):
            handle({**job, 'action': 'test', 'content': '', 'file_ids': files})
    assert handle({**job, 'action': 'agent'})['tests'] == []


def test_search_without_a_saved_query_requires_real_text_even_with_file(tmp_path):
    workflow = {'entry': 'read', 'nodes': [
        node('read', 'read_document'),
        node('search', 'search_document', [bind('documents', 'read', 'documents')]),
    ], 'edges': [edge('read', 'search')]}
    store, identifier, doc = saved(tmp_path, workflow)
    store.close()
    job = {'directory': str(tmp_path), 'id': identifier}
    assert handle({**job, 'action': 'agent'})['request_required'] is True
    for content in ({}, {'content': '   '}):
        with pytest.raises(ValueError, match='실행 요청'):
            handle({**job, 'action': 'test', 'file_ids': [doc['id']], **content})
    assert handle({**job, 'action': 'agent'})['tests'] == []


def test_a_paused_run_requires_a_real_answer_and_can_then_resume(tmp_path):
    workflow = {'entry': 'read', 'nodes': [
        node('read', 'read_document'),
        node('ask', 'ask_user', question='처리를 승인할까요?'),
    ], 'edges': [edge('read', 'ask')]}
    store, identifier, doc = saved(tmp_path, workflow)
    store.close()
    job = {'directory': str(tmp_path), 'id': identifier, 'action': 'test'}
    paused = handle({**job, 'content': '', 'file_ids': [doc['id']]})
    assert paused['status'] == 'needs_input', paused.get('error')
    resume = {**job, 'id': paused['id'], 'action': 'resume_run'}
    with pytest.raises(ValueError, match='답변|입력'):
        handle({**resume, 'content': '   '})
    assert handle({**resume, 'action': 'run'})['run']['status'] == 'needs_input'
    finished = handle({**resume, 'content': '승인합니다.'})
    assert finished['status'] == 'succeeded'
    assert finished['outputs']['ask']['answer'] == '승인합니다.'


def test_run_now_uses_saved_schedule_instructions_without_fabricating_a_customer_message(tmp_path, monkeypatch):
    from contracts.models import AgentDraft
    from infrastructure.persistence.ids import uid
    from infrastructure.persistence.storage import Storage
    from tests.design.test_builder import draft, initial

    instructions = '연결한 매출표에서 오늘의 총액을 요약해 보고서로 저장하세요.'
    store = Storage(tmp_path)
    try:
        source = {'id': uid(), 'name': 'sales.csv', 'kind': 'table', 'format': 'csv', 'columns': ['매출']}
        store.add_asset(source, [{'매출': 15000}])
        guide = {'id': uid(), 'name': 'guide.txt', 'kind': 'document', 'format': 'txt', 'text': '매출 보고서 지침'}
        store.add_asset(guide, [])
        nodes = [node('read', 'read_table', file_ids=[source['id']]),
                 node('sales', 'write_result', [bind('table', 'read', 'table')], format='csv'),
                 node('guide', 'read_document', file_ids=[guide['id']]),
                 node('summarize', 'compose_answer', [bind('documents', 'guide', 'documents')]),
                 node('report', 'export_result', [bind('text', 'summarize', 'text')], format='markdown')]
        workflow = {'entry': 'read', 'nodes': nodes, 'edges': [edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])]}
        config = {**draft(), 'instructions': instructions, 'tool_ids': [n['tool'] for n in nodes],
                  'workflow': workflow, 'document_ids': [source['id'], guide['id']], 'report_destination': 'reports',
                  'schedule': {'frequency': 'daily', 'timezone': 'Asia/Seoul', 'hour': 9, 'minute': 0, 'weekday': None}}
        session = {**initial(), 'draft': AgentDraft.model_validate(config).model_dump(), 'confirmed': True}
        identifier = store.save_agent(session)
    finally:
        store.close()

    def summarize(node, state, storage, config, llm):
        assert state['request'] == instructions
        assert state['customer_message'] == ''
        assert not state['messages']
        assert state['outputs']['read']['table']['rows'] == [{'매출': 15000}]
        return {'text': '총액 15,000원'}

    monkeypatch.setattr('workflow_engine.runner.create_chat_model', lambda **_: object())
    monkeypatch.setattr('workflow_engine.executors.document.compose_answer.execute', summarize)
    job = {'id': identifier, 'directory': str(tmp_path)}
    detail = handle({**job, 'action': 'agent'})
    assert detail['request_required'] is False and detail['source']['asset_id'] == source['id']
    run = handle({**job, 'action': 'test'})
    assert run['status'] == 'succeeded', run.get('error')
    assert run['trigger'] == 'manual' and run['source_version'] == detail['source']['version']
    persisted = handle({**job, 'action': 'run', 'id': run['id']})['run']
    assert persisted['request'] == ''
    assert not [message for message in persisted['messages'] if message['role'] == 'user']
    assert '총액 15,000원' in persisted['artifact']['content']
