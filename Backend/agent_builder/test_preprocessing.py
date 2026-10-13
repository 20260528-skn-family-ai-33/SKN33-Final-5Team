import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.preprocessing import DatasetStore,ROOT,adapt_example,pilot
from agent_builder.storage import Storage,uid
from agent_builder.workflow_runtime import run_workflow
from agent_builder.scheduling import Schedules
from agent_builder.test_scheduling import seed

ROWS=[json.loads(l) for l in (ROOT/'clean_requests.jsonl').read_text().splitlines()]

def test_import_normalizes_deduplicates_and_is_idempotent(tmp_path):
    st=Storage(tmp_path);ds=DatasetStore(st);result=ds.import_directory();assert result['raw_rows']==32;assert result['clean_rows']==30;assert result['duplicates_removed']==2
    assert result['compatibility']=={'compatible':12,'unsupported':12,'needs_clarification':6}
    assert ds.import_directory()==result
    assert st.db.execute('SELECT count(*) FROM preprocessing_examples').fetchone()[0]==30
    assert not st.agents()
    raw,clean=st.db.execute('SELECT raw_json,clean_json FROM preprocessing_examples LIMIT 1').fetchone();assert json.loads(raw)['request']!=json.loads(clean)['request']
    st.close()

def test_split_leakage_rejected_without_partial_import(tmp_path):
    source=tmp_path/'input';source.mkdir()
    for name in ['raw_requests.jsonl','train.jsonl','validation.jsonl','test.jsonl']:(source/name).write_bytes((ROOT/name).read_bytes())
    row=json.loads((source/'train.jsonl').read_text().splitlines()[0]);row['id']='different-id'
    with (source/'test.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    st=Storage(tmp_path/'db');ds=DatasetStore(st)
    with pytest.raises(ValueError,match='누출'):ds.import_directory(source)
    assert st.db.execute('SELECT count(*) FROM preprocessing_examples').fetchone()[0]==0;st.close()

@pytest.mark.parametrize('row',[r for r in ROWS if adapt_example(r)['status']=='compatible'],ids=lambda r:r['id'])
def test_adapted_examples_execute_real_registered_nodes(tmp_path,row):
    adapted=adapt_example(row);st=Storage(tmp_path);asset=adapted['resources'][0];source=next(iter(row['context']['sources']))
    st.add_asset(asset,pilot.SOURCES[source]['rows']);id=uid()
    agent={'id':id,'version':1,'config':adapted['draft'],'model':'not-used','credential_id':'not-used'}
    st.db.execute('INSERT INTO agent_versions VALUES(?,?,?)',(id,1,json.dumps(agent)));st.db.commit()
    run=run_workflow(st,id,row['request'],'');assert run['status']=='succeeded',run
    expected=pilot.mock_run(row)
    if source=='inventory':assert [r['product'] for r in run['rows']]==['A','D']
    elif source=='sales':assert run['rows']==[{'sum':200}]
    elif source=='expenses':assert run['rows']==[{'id':'E-2','amount':101}]
    elif source=='contacts':assert run['rows']==[{'id':'C-1','email':''}]
    assert (tmp_path/run['report_path']).is_file();st.close()

def test_bad_reference_not_converted_to_ready():
    row=copy.deepcopy(ROWS[0]);row['target']['workflow']['nodes'][1]['inputs']['rows']['ref']['node_id']='future'
    assert adapt_example(row)['status']=='invalid'

def test_cancel_queued_job_and_protect_running(tmp_path):
    st,id=seed(tmp_path);s=Schedules(st);j=s.enqueue_test(id,0)
    assert s.cancel_test(id,j['id'])['status']=='cancelled';assert not s.claim_due();assert s.cancel_test(id,j['id'])['status']=='cancelled'
    j=s.enqueue_test(id,0);assert s.claim_due()
    with pytest.raises(ValueError,match='취소할 수'):s.cancel_test(id,j['id'])
    with pytest.raises(ValueError):s.cancel_test(uid(),j['id'])
    st.close()

@pytest.mark.parametrize('bad',[None,'','NaN','Infinity',True,'n/a'])
def test_sum_invalid_values_fail_instead_of_zero(tmp_path,bad):
    from agent_builder.workflow_runtime import execute_node
    from agent_builder.models import NodeSettings
    node={'tool':'aggregate_table','bindings':[{'input':'table','node_id':'read','output':'table'}],'settings':NodeSettings(aggregation='sum',sum_field='amount',file_ids=[],query=None,fields=[],instructions=None,format=None,question=None,condition_field=None,condition_operator=None).model_dump()}
    with pytest.raises(ValueError):execute_node(node,{'outputs':{'read':{'table':{'columns':['amount'],'rows':[{'amount':bad}],'file_id':'x'}}}},None,{},None)

def test_invalid_node_output_stops_downstream_and_records_failure(tmp_path):
    from agent_builder.workflow_runtime import execute_node
    st,id=seed(tmp_path);seen=[]
    def executor(node,*args):
        seen.append(node['tool'])
        if node['tool']=='read_table':return {'table':{'rows':'not-a-table','columns':[]}}
        return execute_node(node,*args)
    run=run_workflow(st,id,'실행','',executor=executor)
    assert run['status']=='failed';assert seen==['read_table'];assert run['artifact'] is None;st.close()

def test_decimal_sum_and_grouping(tmp_path):
    from agent_builder.workflow_runtime import execute_node
    from agent_builder.models import NodeSettings
    node={'tool':'aggregate_table','bindings':[{'input':'table','node_id':'read','output':'table'}],'settings':NodeSettings(aggregation='sum',sum_field='amount',file_ids=[],query=None,fields=['team'],instructions=None,format=None,question=None,condition_field=None,condition_operator=None).model_dump()}
    state={'outputs':{'read':{'table':{'columns':['team','amount'],'rows':[{'team':'A','amount':'0.1'},{'team':'A','amount':'0.2'},{'team':'B','amount':'-1'}],'file_id':'x'}}}}
    assert execute_node(node,state,None,{},None)['table']['rows']==[{'team':'A','sum':0.3},{'team':'B','sum':-1}]
