import pytest

from agent_builder.storage import uid
from agent_builder.test_workflow import bind, edge, node, saved
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import SearchTerms, execute_node, run_workflow


@pytest.mark.parametrize('operator', ['empty', 'not_empty'])
@pytest.mark.parametrize('port,value,is_empty', [
    ('documents', [], True),
    ('documents', [{'text': '실제 문서 본문'}], False),
    ('records', [], True),
    ('records', [{'field': '제목', 'value': '테스트'}], False),
    ('table', {'rows': [], 'columns': ['제목']}, True),
    ('table', {'rows': [{'제목': '테스트'}], 'columns': ['제목']}, False),
    ('reviews', [], True),
    ('reviews', [{'report': {'verdict': 'pass'}}], False),
    ('text', '', True),
    ('text', '완료', False),
    ('answer', '', True),
    ('answer', '예', False),
])
def test_empty_conditions_use_the_connected_input(operator, port, value, is_empty):
    condition = node('check', 'condition', [bind(port, 'source', port)],
                     condition_operator=operator)
    result = execute_node(condition, {'outputs': {'source': {port: value}}},
                          None, {}, None)

    assert result['decision'] is (is_empty if operator == 'empty' else not is_empty)
    assert result[port] == value


class QueryTermsLLM:
    def with_structured_output(self, schema, **kwargs):
        assert schema is SearchTerms
        return self

    def invoke(self, messages):
        return SearchTerms(keywords=[messages[-1]['content']])


@pytest.mark.parametrize('operator', ['empty', 'not_empty'])
@pytest.mark.parametrize('source,has_documents', [
    ('read', True), ('read', False), ('search', True), ('search', False),
])
def test_document_condition_routes_real_workflow(tmp_path, operator, source, has_documents):
    nodes = [node('read', 'read_document')]
    edges = []
    if source == 'search':
        nodes.append(node('search', 'search_document', [bind('documents', 'read', 'documents')],
                          query='테스트' if has_documents else '검색되지않는단어'))
        edges.append(edge('read', 'search'))
    nodes.extend([
        node('check', 'condition', [bind('documents', source, 'documents')],
             condition_operator=operator),
        node('yes', 'finish', instructions='조건 충족'),
        node('no', 'finish', instructions='조건 미충족'),
    ])
    edges.extend([edge(source, 'check'), edge('check', 'yes', 'yes'), edge('check', 'no', 'no')])
    workflow = {'entry': 'read', 'nodes': nodes, 'edges': edges}
    assert not workflow_errors(workflow, list(dict.fromkeys(n['tool'] for n in nodes)), [])

    store, agent_id, asset = saved(tmp_path, workflow)
    try:
        if source == 'read' and not has_documents:
            asset = {**asset, 'id': uid(), 'text': '', 'pages': ['']}
            store.add_asset(asset, [])
        run = run_workflow(store, agent_id, '문서 존재 여부 확인', 'unused',
                           file_ids=[asset['id']], llm=QueryTermsLLM())
        expected = has_documents if operator == 'not_empty' else not has_documents
        branch = 'yes' if expected else 'no'

        assert run['status'] == 'succeeded', run.get('error')
        assert bool(run['outputs'][source]['documents']) is has_documents
        assert run['outputs']['check']['documents'] == run['outputs'][source]['documents']
        assert run['outputs']['check']['decision'] is expected
        assert [step['node'] for step in run['steps']] == (
            ['read'] + (['search'] if source == 'search' else []) + ['check', branch]
        )
        assert run['answer'] == ('조건 충족' if expected else '조건 미충족')
    finally:
        store.close()
