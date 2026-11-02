"""Synthetic table comparison using actual local operation and service graph."""
import json,copy
import pytest
from pydantic import ValidationError
from agent_builder.models import AgentDraft,NodeSettings
from agent_builder.storage import Storage,uid
from agent_builder.test_builder import draft,initial
from agent_builder.test_calculate_columns_integration import node,settings
from agent_builder.test_workflow import bind,edge
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import execute_node,run_workflow

def compare_node():
    return node('compare','compare_tables',settings(compare_keys=['item'],compare_columns=['price'],compare_tolerance=0.01),
                [bind('baseline','before','table'),bind('candidate','after','table')])

def test_saved_two_table_compare_and_json_artifact(tmp_path):
    storage=Storage(tmp_path);ids=[]
    datasets=[[{'item':'A','price':10},{'item':'B','price':20}],
              [{'item':'A','price':11},{'item':'C','price':30}]]
    for rows in datasets:
        asset={'id':uid(),'name':'synthetic.csv','kind':'table','format':'csv','columns':['item','price'],
               'profile':{},'row_count':len(rows),'preview':rows,'text':''}
        storage.add_asset(asset,rows);ids.append(asset['id'])
    nodes=[node('before','read_table',settings(file_ids=[ids[0]])),node('after','read_table',settings(file_ids=[ids[1]])),
           compare_node(),node('save','export_result',settings(format='json'),[bind('text','compare','text')])]
    w={'entry':'before','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}
    tools=['read_table','compare_tables','export_result']
    assert not workflow_errors(w,tools,[storage.asset(i) for i in ids])
    config={**draft(),'tool_ids':tools,'document_ids':ids,'workflow':w,'schedule':None,'report_destination':None}
    aid=storage.save_agent({**initial(),'draft':AgentDraft.model_validate(config).model_dump(),'confirmed':True})
    result=run_workflow(storage,aid,'가상 표 차이 보고서','unused',file_ids=ids)
    assert result['status']=='succeeded',result.get('error')
    difference=json.loads(result['artifact']['content'])
    assert difference=={'added':[datasets[1][1]],'deleted':[datasets[0][1]],'changed':[{'key':['A'],'column':'price','before':10,'after':11}]}
    assert [storage.rows(i) for i in ids]==datasets
    storage.close()

def test_duplicate_keys_or_invalid_policy_are_rejected_without_source_change():
    left={'rows':[{'item':'A','price':10}],'columns':['item','price']}
    right={'rows':[{'item':'A','price':11},{'item':'A','price':12}],'columns':['item','price']}
    state={'outputs':{'before':{'table':left},'after':{'table':right}}};original=copy.deepcopy(state)
    with pytest.raises(ValueError):execute_node(compare_node(),state,None,{},None)
    assert state==original
    with pytest.raises(ValidationError):NodeSettings.model_validate({**settings(),'compare_tolerance':True})

def test_comparison_tolerance_only_applies_to_numeric_values():
    left={'rows':[{'item':'A','price':10.0}],'columns':['item','price']}
    right={'rows':[{'item':'A','price':10.005}],'columns':['item','price']}
    state={'outputs':{'before':{'table':left},'after':{'table':right}}}
    assert json.loads(execute_node(compare_node(),state,None,{},None)['text'])['changed']==[]
    right['rows'][0]['price']='10.0'
    assert len(json.loads(execute_node(compare_node(),state,None,{},None)['text'])['changed'])==1

def test_verifier_rejects_nonexistent_compare_column():
    from a4b_verifier.contracts import VerificationInput
    from a4b_verifier.adapters import draft_from_backend
    from a4b_verifier.validation import code_checks
    from agent_builder.nodes.verification import current_tools,resource_document
    assets=[{'id':id,'kind':'table','columns':['item','price'],'preview':[]} for id in ('left','right')]
    nodes=[node('before','read_table',settings(file_ids=['left'])),node('after','read_table',settings(file_ids=['right'])),compare_node()]
    wf={'entry':'before','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}
    raw={**draft(),'workflow':wf,'document_ids':['left','right']}
    data=VerificationInput.model_validate({'contract_version':'a4b.verifier.v1','request_id':'compare',
         'user_request':'두 표 비교','documents':[resource_document(a) for a in assets],
         'tools':current_tools(),'candidate_draft':draft_from_backend(raw,draft_id='draft',version='1')})
    assert not [f for f in code_checks(data) if f.severity=='block']
    data.candidate_draft.workflow.nodes[2].settings['compare_columns']=['absent']
    assert 'invalid_compare_columns' in {f.code for f in code_checks(data)}
