"""Folder documents produce one grounded artifact without silently dropping inputs."""
from types import SimpleNamespace

import pytest

from contracts.models import AgentDraft
from infrastructure.persistence.asset_library import AssetLibrary
from infrastructure.persistence.storage import Storage
from tests.workflow_engine.test_gmail_scheduling import initial, draft, node, bind, edge, SPEC
from workflow_engine.executors.document import compose_answer
from workflow_engine.runner import run_workflow
from workflow_engine.scheduling.library_sources import LibrarySources, library_export, source_tags


def test_folder_documents_make_one_report_with_both_sources(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL', 'http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS', raising=False)
    store = Storage(tmp_path)
    try:
        library = AssetLibrary(store)
        folder = library.create_folder('주간 업무보고')
        for name, text in [('영업.txt', '영업팀은 고객 상담 3건을 완료했습니다.'),
                           ('개발.txt', '개발팀은 로그인 오류를 수정했습니다.')]:
            library.upload(dict(name=name, kind='document', format='txt', text=text,
                                pages=[text], columns=[], profile={}, processing_status='ready'), [], folder['id'])
        nodes = [node('read', 'read_document'),
                 node('summary', 'compose_answer', [bind('documents', 'read', 'documents')]),
                 node('save', 'export_result', [bind('text', 'summary', 'text')], format='markdown')]
        config = {**draft(), 'instructions': '모든 팀의 완료 업무를 한 보고서로 정리하세요.',
                  'tool_ids': [n['tool'] for n in nodes], 'document_ids': [], 'schedule': SPEC,
                  'report_destination': 'reports', 'workflow': {'entry': 'read', 'nodes': nodes,
                  'edges': [edge('read', 'summary'), edge('summary', 'save')]}}
        identifier = store.save_agent({**initial(), 'confirmed': True,
                                     'draft': AgentDraft.model_validate(config).model_dump()})
        manager = LibrarySources(store)
        selection = manager.save(identifier, dict(mode='all', space='personal', team_id=None,
                                 folder_id=folder['id'], asset_ids=[], target_node_id='read'), 0)
        refs = manager.resolve('test', identifier, library_export(store, selection, 'document')['candidates'])
        assert refs['status'] == 'resolved' and len(refs['refs']) == 2
        view = manager.stage(refs['run_id'], store.agent(identifier),
                             {**library_export(store, selection, 'document', refs['refs']), 'selection': selection})
        evidence = []
        answer = {}

        def create(**kwargs):
            evidence.extend(kwargs['tools'][0].invoke({}))
            answer.update(text='\n'.join(item['quote'] for item in evidence),
                          citations=[{'evidence_id': item['evidence_id']} for item in evidence])
            return SimpleNamespace(invoke=lambda *args, **kw: {'structured_response': answer})

        class Reviewer:
            def with_structured_output(self, *args, **kwargs):
                return self

            def invoke(self, *args):
                return SimpleNamespace(model_dump=lambda: answer)

        monkeypatch.setattr('workflow_engine.executors.agent_factory.create_deep_agent', create)
        result = run_workflow(view, identifier, '', 'unused', llm=Reviewer(),
                              forced_run_id=refs['run_id'], run_tags=source_tags(view))
        assert result['status'] == 'succeeded', result.get('error')
        assert {item['file_name'] for item in evidence} == {'영업.txt', '개발.txt'}
        assert '고객 상담 3건' in result['artifact']['content']
        assert '로그인 오류' in result['artifact']['content']
        assert result['artifact']['name'].endswith('.md')
        assert len(result['library_source_files']) == 2
        assert len(store.tests(identifier)) == 1
    finally:
        store.close()


def test_large_folder_report_refuses_partial_context_before_search_or_model(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('A folder-wide report must not silently replace the full input with search results')

    monkeypatch.setattr(compose_answer, 'execute_node', unexpected)
    documents = [dict(source_id=str(i), file_id=str(i), file_name=f'{i}.txt',
                      page=None, offset=0, text='가' * 23000) for i in range(2)]
    summary = node('summary', 'compose_answer', [bind('documents', 'read', 'documents')])
    with pytest.raises(ValueError, match='45,000.*폴더를 나누'):
        compose_answer.execute(summary, {'request': '전체 보고서', 'outputs': {'read': {'documents': documents}}},
                               SimpleNamespace(snapshot={'selection': {'mode': 'all'}}, items={'0': {}, '1': {}}),
                               {'instructions': '모든 문서를 정리하세요.', 'workflow': {'nodes': [], 'edges': []}}, None)


def test_folder_table_input_keeps_unrelated_large_policy_document_search(monkeypatch):
    calls = []

    def search(node, state, *args):
        calls.append(node['tool'])
        assert state['outputs']['_input']['documents'][0]['file_id'] == 'separate-policy'
        return {'documents': []}

    monkeypatch.setattr(compose_answer, 'execute_node', search)
    documents = [dict(source_id='policy:0', file_id='separate-policy', file_name='정책.txt',
                      page=None, offset=0, text='가' * 45001)]
    summary = node('summary', 'compose_answer', [bind('documents', 'policy', 'documents')])
    result = compose_answer.execute(summary, {'request': '관련 정책', 'outputs': {'policy': {'documents': documents}}},
                                    SimpleNamespace(snapshot={'selection': {'mode': 'all'}}, items={'folder-table': {}}),
                                    {'instructions': '관련 정책 검색', 'workflow': {'nodes': [], 'edges': []}}, None)
    assert calls == ['search_document']
    assert not result['evidence']
