"""Synthetic service fixtures; real registered table operations and workflow runtime."""
import copy,json
import pytest
from agent_builder.models import AgentDraft,NodeSettings
from agent_builder.registry import TOOLS
from agent_builder.storage import Storage,uid
from agent_builder.test_builder import draft,initial
from agent_builder.test_calculate_columns_integration import node,settings
from agent_builder.test_workflow import bind,edge
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import execute_node,run_workflow

def join_node():
    return node('join','join_tables',settings(join_keys=['item'],join_how='left',join_cardinality='many_to_one'),
                [bind('left','orders','table'),bind('right','prices','table')])

def test_saved_multi_file_join_compute_export_preserves_sources(tmp_path):
    storage=Storage(tmp_path)
    ids=[]
    datasets=[(['item','qty'],[{'item':'A','qty':3},{'item':'B','qty':4}]),
              (['item','price'],[{'item':'A','price':10},{'item':'B','price':2.5}])]
    for columns,rows in datasets:
        asset={'id':uid(),'name':'synthetic.csv','kind':'table','format':'csv','columns':columns,
               'profile':{},'row_count':len(rows),'preview':rows,'text':''}
        storage.add_asset(asset,rows);ids.append(asset['id'])
    nodes=[node('orders','read_table',settings(file_ids=[ids[0]])),
           node('prices','read_table',settings(file_ids=[ids[1]])),join_node(),
           node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='price*qty',output_column='total'),[bind('table','join','table')]),
           node('save','write_result',settings(format='json'),[bind('table','calc','table')])]
    w={'entry':'orders','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}
    tools=['read_table','join_tables','calculate_columns','write_result']
    assert 'join_tables' in {t['id'] for t in TOOLS}
    assert not workflow_errors(w,tools,[storage.asset(i) for i in ids])
    config={**draft(),'tool_ids':tools,'document_ids':ids,'workflow':w,'schedule':None,'report_destination':None}
    from agent_builder.input_requirements import preflight_execution
    req=preflight_execution(config,storage,ids)
    assert [r['file_ids'] for r in req]==[[ids[0]],[ids[1]]]
    with pytest.raises(ValueError,match='모두 연결'):preflight_execution(config,storage,[ids[0]])
    aid=storage.save_agent({**initial(),'draft':AgentDraft.model_validate(config).model_dump(),'confirmed':True})
    result=run_workflow(storage,aid,'가상 주문과 가격을 결합해 합계를 저장','unused',file_ids=ids)
    expected=[{'item':'A','qty':3,'price':10,'total':30},{'item':'B','qty':4,'price':2.5,'total':10}]
    assert result['status']=='succeeded',result.get('error')
    assert result['rows']==expected
    assert json.loads((tmp_path/result['report_path']).read_text(encoding='utf8'))==expected
    assert [storage.rows(i) for i in ids]==[d[1] for d in datasets]
    storage.close()

def test_join_rejects_unresolved_policy_invalid_keys_and_duplicates():
    left={'rows':[{'item':'A','qty':3}],'columns':['item','qty'],'file_id':'l'}
    right={'rows':[{'item':'A','price':1},{'item':'A','price':2}],'columns':['item','price'],'file_id':'r'}
    state={'outputs':{'orders':{'table':left},'prices':{'table':right}}};before=copy.deepcopy(state)
    with pytest.raises(ValueError):execute_node(join_node(),state,None,{},None)
    assert state==before
    n=join_node();n['settings']['join_how']=None
    with pytest.raises(ValueError):execute_node(n,state,None,{},None)
    n=join_node();right['rows']=[{'item':'','price':1}]
    with pytest.raises(ValueError):execute_node(n,state,None,{},None)

def test_empty_inner_join_keeps_columns_and_sources():
    state={'outputs':{'orders':{'table':{'rows':[{'item':'A','qty':1}],'columns':['item','qty'],'file_id':'l'}},
                      'prices':{'table':{'rows':[{'item':'B','price':2}],'columns':['item','price'],'file_id':'r'}}}}
    n=join_node();n['settings']['join_how']='inner'
    result=execute_node(n,state,None,{},None)['table']
    assert result['rows']==[] and result['columns']==['item','qty','price'] and result['source_file_ids']==['l','r']

def test_left_join_with_empty_right_keeps_declared_columns_with_nulls():
    state={'outputs':{'orders':{'table':{'rows':[{'item':'A','qty':1}],'columns':['item','qty']}},
                      'prices':{'table':{'rows':[],'columns':['item','price']}}}}
    result=execute_node(join_node(),state,None,{},None)['table']
    assert result['columns']==['item','qty','price']
    assert result['rows']==[{'item':'A','qty':1,'price':None}]

def test_multiple_execution_files_require_explicit_reader_choice():
    class S:
        def asset(self,id):return {'id':id,'kind':'table','columns':['item']}
        def rows(self,id):return [{'item':id}]
    reader=node('r','read_table',settings())
    with pytest.raises(ValueError,match='명시'):
        execute_node(reader,{'file_ids':['a','b'],'outputs':{}},S(),{'document_ids':[]},None)
    reader['settings']['file_ids']=['b']
    assert execute_node(reader,{'file_ids':['a','b'],'outputs':{}},S(),{'document_ids':[]},None)['table']['file_id']=='b'
    other=node('other','read_table',settings(file_ids=['a']))
    config={'document_ids':['a','b'],'workflow':{'nodes':[reader,other]}}
    with pytest.raises(ValueError,match='모두 연결'):
        execute_node(reader,{'file_ids':['b'],'outputs':{}},S(),config,None)

def test_verifier_checks_join_columns_and_propagates_to_calculation():
    from a4b_verifier.contracts import VerificationInput
    from a4b_verifier.adapters import draft_from_backend
    from a4b_verifier.validation import code_checks
    from agent_builder.nodes.verification import current_tools,resource_document
    assets=[{'id':'left','kind':'table','columns':['item','qty'],'preview':[]},
            {'id':'right','kind':'table','columns':['item','price'],'preview':[]}]
    nodes=[node('orders','read_table',settings(file_ids=['left'])),node('prices','read_table',settings(file_ids=['right'])),join_node(),
           node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='price*qty',output_column='total'),[bind('table','join','table')])]
    wf={'entry':'orders','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}
    raw={**draft(),'workflow':wf,'document_ids':['left','right']}
    data=VerificationInput.model_validate({'contract_version':'a4b.verifier.v1','request_id':'join',
         'user_request':'표 결합 후 계산','documents':[resource_document(a) for a in assets],
         'tools':current_tools(),'candidate_draft':draft_from_backend(raw,draft_id='draft',version='1')})
    assert not [f for f in code_checks(data) if f.severity=='block']
    data.candidate_draft.workflow.nodes[2].settings['join_keys']=['absent']
    assert 'invalid_join_columns' in {f.code for f in code_checks(data)}

def test_only_unambiguous_two_reader_fanin_is_serialized_and_audited():
    from agent_builder.workflow import normalize_connections
    nodes=[node('orders','read_table',settings(file_ids=['a'])),node('prices','read_table',settings(file_ids=['b'])),join_node()]
    fanin={'entry':'orders','nodes':nodes,'edges':[edge('orders','join'),edge('prices','join')]}
    audit=[];normalized=normalize_connections(copy.deepcopy(fanin),audit=audit)
    assert normalized['edges']==[edge('orders','prices'),edge('prices','join')]
    assert audit==[{'type':'serialize_independent_table_reads','first':'orders','second':'prices','target':'join'}]
    resources=[{'id':'a','kind':'table','columns':['item','qty']},{'id':'b','kind':'table','columns':['item','price']}]
    assert not workflow_errors(normalized,['read_table','join_tables'],resources)
    branches=copy.deepcopy(fanin)
    for e in branches['edges']:e['branch']='yes'
    assert normalize_connections(branches)['edges']==[edge('orders','prices'),edge('prices','join')]
    fanin['edges'].append(edge('orders','prices'))
    audit=[];normalize_connections(fanin,audit=audit)
    assert not audit
