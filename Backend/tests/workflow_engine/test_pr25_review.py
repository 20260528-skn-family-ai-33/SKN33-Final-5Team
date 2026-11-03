import json
from types import SimpleNamespace
import pytest
from workflow_engine.executors.canonical_operation import execution_context,validate_settings
from tests.workflow_engine.test_workflow import node,bind,edge,saved
from workflow_engine.runner import run_workflow


def test_server_google_token_never_creates_user_connector(tmp_path,monkeypatch):
    monkeypatch.setenv('A4B_GOOGLE_SENDER_EMAIL','shared@example.com')
    monkeypatch.setenv('A4B_GOOGLE_ACCESS_TOKEN','shared-token')
    a=execution_context(SimpleNamespace(path=tmp_path/'a'/'db'))
    b=execution_context(SimpleNamespace(path=tmp_path/'b'/'db'))
    assert 'gmail' not in a.connectors and 'google_calendar' not in b.connectors
    connector=object()
    a=execution_context(SimpleNamespace(path=tmp_path/'a'/'db',workflow_connectors={'gmail':connector}))
    assert a.connectors['gmail'] is connector and 'gmail' not in b.connectors


@pytest.mark.parametrize('keys',[[],[''],['   ']])
def test_empty_join_keys_fail_before_execution(keys):
    n=node('join','join_tables',operation='join',arguments_json=json.dumps({'left':{'rows':[]},'right':{'rows':[]},'keys':keys,'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}))
    with pytest.raises(ValueError,match='keys'):validate_settings(n)


def test_two_runtime_files_read_separately_and_join(tmp_path):
    from infrastructure.persistence.ids import uid
    left_id,right_id=uid(),uid()
    left=node('left','read_table',file_ids=[left_id])
    right=node('right','read_table',file_ids=[right_id])
    join=node('join','join_tables',[bind('left','left','table'),bind('right','right','table')],operation='join',arguments_json=json.dumps({'keys':['id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}))
    out=node('out','write_result',[bind('table','join','table')],format='json')
    w={'entry':'left','nodes':[left,right,join,out],'edges':[edge('left','right'),edge('right','join'),edge('join','out')]}
    store,identifier,_=saved(tmp_path,w)
    try:
        store.add_asset({'id':left_id,'name':'left.csv','kind':'table','format':'csv','columns':['id','amount']},[{'id':1,'amount':10}])
        store.add_asset({'id':right_id,'name':'right.csv','kind':'table','format':'csv','columns':['id','name']},[{'id':1,'name':'A'}])
        result=run_workflow(store,identifier,'실행','unused',file_ids=[left_id,right_id])
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['join']['table']['rows']==[{'id':1,'amount':10,'name':'A'}]
    finally:store.close()
