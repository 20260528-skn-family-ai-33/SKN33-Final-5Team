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


@pytest.mark.parametrize('keys',[[],[''],['   ']])
@pytest.mark.parametrize('tool,operation,extra',[
    ('join_tables','join_records',{'left':[],'right':[]}),
    ('compare_tables','diff',{'baseline':{'rows':[]},'candidate':{'rows':[]},'compare_columns':[],'absolute_tolerance':0}),
])
def test_alternate_join_and_compare_reject_empty_keys(tool,operation,extra,keys):
    n=node('combine',tool,operation=operation,arguments_json=json.dumps({**extra,'keys':keys}))
    with pytest.raises(ValueError,match='keys'):validate_settings(n)


@pytest.mark.parametrize('tool,operation,args',[
    ('join_tables','join',{'left':{'rows':[]},'right':{'rows':[]},'keys':[],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}),
    ('join_tables','join_records',{'left':[],'right':[],'keys':[]}),
    ('compare_tables','diff',{'baseline':{'rows':[]},'candidate':{'rows':[]},'keys':[],'compare_columns':[],'absolute_tolerance':0}),
])
def test_save_route_rejects_empty_keys_without_persisting(tmp_path,tool,operation,args):
    from tests.design.test_builder import initial,draft
    from infrastructure.persistence.storage import Storage
    from agent_builder.design.nodes.save import make_save_node
    from agent_builder.design.nodes.validation import validate_node
    store=Storage(tmp_path)
    n=node('combine',tool,operation=operation,arguments_json=json.dumps(args))
    d={**draft(),'tool_ids':[tool],'workflow':{'entry':'combine','nodes':[n],'edges':[]}}
    s={**initial(),'draft':d,'confirmed':True}
    try:
        checked=validate_node(s)
        assert checked['status']!='ready' and any('keys' in i for i in checked['issues'])
        with pytest.raises(ValueError):make_save_node(store)(s)
        assert not store.agents()
    finally:store.close()
