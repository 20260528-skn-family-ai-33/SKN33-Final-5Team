from copy import deepcopy
import pytest
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.ids import uid
from tests.design.test_builder import draft, initial
from tests.workflow_engine.test_beta_feedback import table_flow
from service.worker import handle


@pytest.mark.parametrize('columns,content,success', [
    (['id','amount'],'amount >= 100',True),
    (['id'],'amount >= 100',False),
])
def test_replacement_file_columns_used_through_parsing_validation_and_execution(tmp_path,columns,content,success):
    store=Storage(tmp_path)
    original_id=uid()
    store.add_asset(dict(id=original_id,name='orders.csv',kind='table',format='csv',columns=['id']),[dict(id='old')])
    flow=table_flow();flow['nodes'][0]['settings']['file_ids']=[original_id]
    config={**draft(),'workflow':flow,'document_ids':[original_id],'tool_ids':['read_table','write_result']}
    identifier=store.save_agent({**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})
    original=deepcopy(store.agent(identifier));store.close()
    job=dict(directory=str(tmp_path),id=identifier)
    rows=[{k:v for k,v in row.items() if k in columns} for row in [dict(id='a',amount=50),dict(id='b',amount=150)]]
    asset=handle({**job,'action':'run_upload','asset':dict(name='orders.csv',kind='table',format='csv',columns=columns),'rows':rows})['asset']
    if success:
        run=handle({**job,'action':'test','content':content,'file_ids':[asset['id']]})
        assert run['status']=='succeeded',run.get('error')
        assert run['rows']==[dict(id='b',amount=150)]
    else:
        with pytest.raises(ValueError):handle({**job,'action':'test','content':content,'file_ids':[asset['id']]})
    store=Storage(tmp_path)
    try:assert store.agent(identifier)==original
    finally:store.close()


@pytest.mark.parametrize('preview_source',['analysis','review'])
def test_record_review_pauses_once_and_passes_the_same_records_to_memo(tmp_path,preview_source):
    from tests.workflow_engine.test_workflow import node,bind,edge,saved,FakeLLM
    from workflow_engine.runner import run_workflow,execute_node
    nodes=[node('read','read_document'),node('analysis','extract_information',[bind('documents','read','documents')],fields=['제목','금액']),
        node('review','ask_user',[bind('records','analysis','records')],question='검토 의견은?'),
        node('memo','compose_answer',[bind('documents','read','documents'),bind('records',preview_source,'records'),
                                      bind('answer','review','answer')],instructions='최종 메모')]
    flow=dict(entry='read',nodes=nodes,edges=[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])])
    store,identifier,asset=saved(tmp_path,flow)
    def executor(n,s,st,c,l):
        if n['id']=='memo':
            assert s['outputs'][preview_source]['records']==s['outputs']['analysis']['records']
            assert s['outputs']['review']['answer']=='금액은 추가 확인'
            return {'text':'최종 메모: 금액은 추가 확인'}
        return execute_node(n,s,st,c,l)
    try:
        paused=run_workflow(store,identifier,'계약 검토','',file_ids=[asset['id']],llm=FakeLLM(),executor=executor)
        assert paused['status']=='needs_input' and 'memo' not in paused['outputs']
        assert paused['question']['preview']==paused['outputs']['analysis']['records']
        done=run_workflow(store,identifier,'계약 검토','',run_id=paused['id'],answer='금액은 추가 확인',llm=FakeLLM(),executor=executor)
        assert done['status']=='succeeded',done.get('error')
        assert sum(s['node']=='review' for s in done['steps'])==1
        assert '최종 메모' in done['answer']
    finally:store.close()
