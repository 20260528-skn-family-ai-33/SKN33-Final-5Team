"""Service/runtime reproductions from the November 13 PR 103 review."""
import json
from copy import deepcopy

import pytest

from tests.workflow_engine.test_runtime_request_review import run_rows
from tests.workflow_engine.test_review_memo_contract import Model, GOOD, EVIDENCE, OPINION
from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST
from workflow_engine.executors.document.review_memo import compose_review_memo


@pytest.mark.parametrize('operator,expected', [('=', ['', None]), ('!=', ['완료', '취소'])])
@pytest.mark.parametrize('quote', ['"', "'"])
def test_empty_comparisons_select_the_right_rows(tmp_path, operator, expected, quote):
    result=run_rows(tmp_path, f'상태 {operator} {quote}{quote}', ['완료', '', None, '취소'])
    assert result['status']=='succeeded', result.get('error')
    assert result['rows']==[{'상태':value} for value in expected]


@pytest.mark.parametrize('name', ['김다영', '이현우'])
def test_runtime_request_reaches_both_memo_model_calls(name):
    model=Model([GOOD, GOOD])
    request=f'후속 조치에 담당자 {name} 이름을 넣어줘'
    result=compose_review_memo({'instructions':CONTRACT_REQUEST}, {'instructions':CONTRACT_REQUEST},
        dict(text='이전 분석', answer=OPINION), EVIDENCE, model,
        state=dict(request=request, messages=[dict(role='user',content=request)]))
    assert result
    for messages in model.messages:
        context=json.loads(messages[-1]['content'])
        assert context['execution_request']==request
        assert context['user_messages']==[request]
        assert context['human_review']==OPINION


@pytest.mark.parametrize('title', ['계약 요약', '핵심 계약 요약'])
def test_multiword_first_heading_is_preserved(title):
    data=deepcopy(GOOD); data['sections'][title]=data['sections'].pop('요약')
    model=Model([data,data])
    request=f'최종 메모는 {title}/주요 발견/검토 의견/후속 조치 제목으로 작성해줘'
    result=compose_review_memo({'instructions':request},{'instructions':request},
                              dict(text='분석',answer=OPINION),EVIDENCE,model)
    assert result['text'].startswith('## '+title+'\n')
    assert model.schema['properties']['sections']['required'][0]==title


@pytest.mark.parametrize('content', ['다시 실행해줘', '지금 시작해주세요', 'run', ''])
def test_new_file_launch_commands_enqueue_once(tmp_path, monkeypatch, content):
    from tests.workflow_engine.test_library_schedule_sources import workflow, upload, spec
    from infrastructure.persistence.storage import Storage
    from workflow_engine.scheduling.library_sources import LibrarySources, library_export
    monkeypatch.setenv('A4B_STORAGE','sqlite')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS',raising=False)
    store=Storage(tmp_path)
    try:
        upload(store); identifier=workflow(store); manager=LibrarySources(store)
        selection=manager.save(identifier,spec('new'),0)
        refs=library_export(store,selection,'table')['candidates']
        result=manager.resolve('test',identifier,refs,request=content)
        assert result['enqueued_count']==1
        assert manager.resolve('test',identifier,refs,request=content)['enqueued_count']==0
    finally:store.close()


@pytest.mark.parametrize('renamed',['stock','wrong_column'])
def test_child_compiles_against_the_parent_transformed_table(tmp_path,renamed):
    from tests.design.test_node_connections import reusable,save
    from tests.workflow_engine.test_workflow import node,bind,edge
    from workflow_engine.runner import run_workflow
    from infrastructure.persistence.ids import uid
    store, child, config=reusable(tmp_path)
    try:
        identifier=uid()
        store.add_asset(dict(id=identifier,name='replacement.csv',kind='table',format='csv',columns=['raw_stock']),
                        [{'raw_stock':8},{'raw_stock':3}])
        nodes=[node('read','read_table',file_ids=[identifier]),
               node('rename','calculate_columns',[bind('table','read','table')],table_operation='rename',rename_from='raw_stock',rename_to=renamed),
               node('child','saved_workflow',[bind('table','rename','table')],agent_id=child,agent_version=1,output_node='filter',output_port='table'),
               node('write','write_result',[bind('table','child','table')],format='csv')]
        parent=deepcopy(config)
        parent.update(document_ids=[identifier],tool_ids=['read_table','calculate_columns','saved_workflow','write_result'],
                      workflow=dict(entry='read',nodes=nodes,edges=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]))
        parent_id=save(store,parent)
        run=run_workflow(store,parent_id,'execute','unused',file_ids=[identifier])
        if renamed=='stock':
            assert run['status']=='succeeded',run.get('error')
            assert run['rows']==[{'stock':8}]
        else:
            assert run['status']=='failed'
            assert '열' in run['error']
        assert store.asset(identifier)['columns']==['raw_stock']
    finally:store.close()


def test_compose_answer_passes_execution_request_into_memo_route():
    from workflow_engine.executors.document.compose_answer import _compose_documents
    from tests.workflow_engine.test_workflow import node
    model=Model([GOOD,GOOD])
    request='후속 조치 담당자는 김다영으로 적어줘'
    result=_compose_documents(node('memo','compose_answer',instructions=CONTRACT_REQUEST),
        dict(request=request,messages=[dict(role='user',content=request)]),None,
        dict(instructions=CONTRACT_REQUEST),model,
        [dict(source_id='doc:1',file_id='doc',page=1,text='월 이용료는 100000원입니다.')],
        dict(text='분석',answer=OPINION),[])
    assert result['text']
    assert all(json.loads(messages[-1]['content'])['execution_request']==request for messages in model.messages)


@pytest.mark.parametrize('missing',[False,True])
def test_conditional_human_review_only_pauses_when_clause_missing(tmp_path,missing):
    from tests.workflow_engine.test_workflow import node,bind,edge,saved
    from workflow_engine.runner import run_workflow,execute_node
    from agent_builder.design.request_guards import review_workflow_issues
    from tests.design.test_beta_feedback import request
    nodes=[node('read','read_document'),node('analysis','extract_information',[bind('documents','read','documents')],fields=['조항']),
           node('check','condition',[bind('records','analysis','records')],condition_operator='has_missing'),
           node('review','ask_user',[bind('records','check','records')],question='검토 의견?'),
           node('end','finish')]
    flow=dict(entry='read',nodes=nodes,edges=[edge('read','analysis'),edge('analysis','check'),edge('check','review','yes'),edge('check','end','no'),edge('review','end')])
    assert not review_workflow_issues(request('누락된 조항이 있는 경우에만 사람 검토'),dict(workflow=flow))
    store,identifier,asset=saved(tmp_path,flow)
    def executor(n,s,st,c,llm):
        if n['id']=='analysis':
            return {'records':[dict(field='조항',value=None if missing else '계약 조항',source_id=None,quote=None)]}
        return execute_node(n,s,st,c,llm)
    try:
        run=run_workflow(store,identifier,'실행','',file_ids=[asset['id']],executor=executor,llm=object())
        assert run['status']==('needs_input' if missing else 'succeeded'),run.get('error')
        if missing:
            done=run_workflow(store,identifier,'실행','',run_id=run['id'],answer='추가 확인',executor=executor,llm=object())
            assert done['status']=='succeeded',done.get('error')
            assert sum(s['node']=='review' for s in done['steps'])==1
    finally:store.close()
