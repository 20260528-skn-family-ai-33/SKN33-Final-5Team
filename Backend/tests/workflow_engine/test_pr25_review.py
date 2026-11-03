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
        from agent_builder.conditions.input_requirements import preflight_execution
        requirements=preflight_execution(store.agent(identifier)['config'],store,[left_id,right_id])
        assert all(r['ready'] for r in requirements)
        assert [r['file_ids'] for r in requirements] == [[left_id],[right_id]]
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


def test_single_saved_table_reads_new_run_file(tmp_path):
    from infrastructure.persistence.ids import uid
    old,new=uid(),uid()
    w={'entry':'read','nodes':[node('read','read_table',file_ids=[old]),node('out','write_result',[bind('table','read','table')],format='json')],'edges':[edge('read','out')]}
    store,identifier,_=saved(tmp_path,w)
    try:
        for i,value in [(old,10),(new,99)]:
            store.add_asset({'id':i,'name':i+'.csv','kind':'table','columns':['amount']},[{'amount':value}])
        agent=store.agent(identifier);agent['config']['document_ids']=[old]
        r=run_workflow(store,identifier,'새 자료로 실행','unused',file_ids=[new],agent_snapshot=agent)
        assert r['status']=='succeeded',r.get('error')
        assert r['rows']==[{'amount':99}]
        assert r['outputs']['read']['table']['file_id']==new
    finally:store.close()


@pytest.mark.parametrize('file_id', [None, 'original-id'])
def test_native_aggregation_and_sort_accept_derived_tables_without_file_id(file_id):
    from workflow_engine.executors.table.aggregate_table import execute as aggregate
    from workflow_engine.executors.table.filter_sort import execute as sort
    table = {'rows':[{'item':'A','amount':30},{'item':'A','amount':10},{'item':'B','amount':10}],
             'columns':['item','amount'], 'review_report':True}
    if file_id is not None:table['file_id']=file_id
    plan = {'filters':[], 'sort':[{'field':'item','direction':'ascending','value_type':'text'}],
            'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'json'}
    a=node('aggregate','aggregate_table',[bind('table','upstream','table')],fields=['item'],aggregation='sum',sum_field='amount')
    grouped=aggregate(a,{'outputs':{'upstream':{'table':table}}},None,{'execution_plan':plan},None)
    s=node('sort','filter_sort',[bind('table','aggregate','table')],execution_plan=plan)
    result=sort(s,{'outputs':{'aggregate':grouped}},None,{'execution_plan':plan},None)['table']
    assert result['rows']==[{'item':'A','sum':40},{'item':'B','sum':10}]
    assert result['review_report'] is True
    assert result.get('file_id')==file_id
    assert ('file_id' in result)==(file_id is not None)


def test_single_saved_document_reads_new_run_file(tmp_path):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.executors.sources import read_source
    store=Storage(tmp_path)
    try:
        for i,text in [('old','이전 문서'),('new','새 문서')]:
            store.add_asset({'id':i,'kind':'document','name':i+'.txt','format':'txt','text':text},[])
        result=read_source(node('read','read_document',file_ids=['old']),{'file_ids':['new'],'outputs':{}},store,{'document_ids':['old']},'documents')
        assert [d['text'] for d in result['documents']]==['새 문서']
    finally:store.close()


@pytest.mark.parametrize('replacement',['reversed','ambiguous','renamed'])
def test_saved_join_replacement_never_reads_old_files(tmp_path,replacement):
    from infrastructure.persistence.ids import uid
    old_left,old_right,new_left,new_right,extra=[uid() for _ in range(5)]
    left=node('left','read_table',file_ids=[old_left]);right=node('right','read_table',file_ids=[old_right])
    join=node('join','join_tables',[bind('left','left','table'),bind('right','right','table')],operation='join',arguments_json=json.dumps({'keys':['id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}))
    out=node('out','write_result',[bind('table','join','table')],format='json')
    w={'entry':'left','nodes':[left,right,join,out],'edges':[edge('left','right'),edge('right','join'),edge('join','out')]}
    store,identifier,_=saved(tmp_path,w)
    try:
        for i,name,rows in [(old_left,'left.csv',[{'id':1,'amount':1}]),(old_right,'right.csv',[{'id':1,'name':'OLD'}]),(new_left,'left.csv' if replacement!='renamed' else 'unknown.csv',[{'id':1,'amount':99}]),(new_right,'right.csv',[{'id':1,'name':'NEW'}]),(extra,'left.csv',[{'id':1,'amount':500}])]:
            store.add_asset({'id':i,'name':name,'kind':'table','columns':list(rows[0])},rows)
        agent=store.agent(identifier);agent['config']['document_ids']=[old_left,old_right]
        ids=[new_right,new_left]+([extra] if replacement=='ambiguous' else [])
        r=run_workflow(store,identifier,'새 자료 결합','unused',file_ids=ids,agent_snapshot=agent)
        if replacement=='reversed':
            assert r['status']=='succeeded',r.get('error')
            assert r['rows']==[{'id':1,'amount':99,'name':'NEW'}]
        else:
            assert r['status']=='failed' and '새 실행 파일' in r['error']
            assert not r['outputs']
    finally:store.close()


def test_wrong_kind_run_input_cannot_silently_reuse_saved_table(tmp_path):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.executors.sources import read_source
    store=Storage(tmp_path)
    try:
        store.add_asset({'id':'old','name':'old.csv','kind':'table','columns':['amount']},[{'amount':10}])
        store.add_asset({'id':'new','name':'new.txt','kind':'document','text':'새 문서'},[])
        with pytest.raises(ValueError,match='새 실행 자료의 종류'):
            read_source(node('read','read_table',file_ids=['old']),{'file_ids':['new'],'outputs':{}},store,{'document_ids':['old']},'table')
    finally:store.close()


@pytest.mark.parametrize('inputs', ['replacement_only', 'one_exact', 'both_exact', 'shared_original'])
def test_saved_join_duplicate_names_require_distinct_source_mapping(tmp_path, inputs):
    from infrastructure.persistence.ids import uid
    old_left, old_right, new = uid(), uid(), uid()
    right_source = old_left if inputs == 'shared_original' else old_right
    left = node('left', 'read_table', file_ids=[old_left])
    right = node('right', 'read_table', file_ids=[right_source])
    combine = node('join', 'join_tables', [bind('left', 'left', 'table'), bind('right', 'right', 'table')],
                   operation='join', arguments_json=json.dumps({'keys':['id'], 'how':'left',
                       'cardinality':'many_to_one', 'null_keys':'error', 'max_rows':10}))
    out = node('out', 'write_result', [bind('table', 'join', 'table')], format='json')
    workflow = {'entry':'left', 'nodes':[left, right, combine, out],
                'edges':[edge('left', 'right'), edge('right', 'join'), edge('join', 'out')]}
    store, identifier, _ = saved(tmp_path, workflow)
    try:
        for identifier_, amount in [(old_left, 10), (old_right, 20), (new, 99)]:
            store.add_asset({'id':identifier_, 'name':'report.csv', 'kind':'table',
                             'columns':['id', 'amount']}, [{'id':1, 'amount':amount}])
        agent = store.agent(identifier)
        agent['config']['document_ids'] = [old_left, old_right]
        files = [old_left, old_right] if inputs == 'both_exact' else [old_left] if inputs == 'one_exact' else [new]
        result = run_workflow(store, identifier, '이번 실행 자료 사용', 'unused',
                              file_ids=files, agent_snapshot=agent)
        if inputs in ('replacement_only', 'one_exact'):
            assert result['status'] == 'failed', result
            assert '새 실행 파일' in result['error']
            assert not result['outputs']
        else:
            assert result['status'] == 'succeeded', result.get('error')
            expected = [old_left, old_right] if inputs == 'both_exact' else [new, new]
            assert [result['outputs'][name]['table']['file_id'] for name in ('left', 'right')] == expected
    finally:
        store.close()
