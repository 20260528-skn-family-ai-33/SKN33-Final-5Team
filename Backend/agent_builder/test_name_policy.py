from copy import deepcopy
import pytest
from langgraph.types import Command
from agent_builder.capability_gate import require_save_ready
from agent_builder.graph import build_graph
from agent_builder.nodes.validation import validate_node
from agent_builder.storage import Storage
from agent_builder.test_builder import initial
from agent_builder.test_condition_readiness import workflow_draft, state

@pytest.mark.parametrize('name',['inspect_resource',' list_tools ','validate_workflow','inspect_inputs','read_table','filter_sort'])
def test_helper_name_fails_validation_and_save(name):
    s=state(); s['draft']=workflow_draft(s); s['draft']['name']=name;s['confirmed']=True
    assert any('도구 식별자' in issue for issue in validate_node(s)['issues'])
    with pytest.raises(ValueError,match='도구 식별자'):
        require_save_ready(s)

@pytest.mark.parametrize('name',['거래 조건 선별','inspect_resource 자료 검토','거래 list_tools 점검'])
def test_ordinary_names_pass_name_gate(name):
    s=state(); s['draft']=workflow_draft(s); s['draft']['name']=name;s['confirmed']=True
    assert not any('도구 식별자' in issue for issue in validate_node(s)['issues'])
    require_save_ready(s)

def test_graph_repairs_name_then_saves_without_changing_workflow(tmp_path):
    s=state();store=Storage(tmp_path);cfg={'configurable':{'thread_id':s['session_id']}}
    original=workflow_draft(s); calls=[]
    def planner(current):
        calls.append(deepcopy(current));out=deepcopy(original)
        out['name']='inspect_resource' if len(calls)==1 else '비용 조건 선별'
        return {'draft':out,'confirmed':False,'repair_count':len(calls),'saved_agent_id':None}
    graph,conn=build_graph(store,conversation=planner,capability_mode='off',condition_mode='off',verifier_mode='off')
    result=graph.invoke(s,cfg)
    assert len(calls)==2 and result['status']=='ready' and not store.agents()
    assert result['draft']['name']=='비용 조건 선별'
    assert result['draft']['workflow']==original['workflow']
    saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    assert saved['status']=='saved' and len(store.agents())==1
    assert store.agent(saved['saved_agent_id'])['config']['name']=='비용 조건 선별'
    conn.close();store.close()


def test_direct_storage_cannot_bypass_helper_name_guard(tmp_path):
    s=state();s['draft']=workflow_draft(s);s['draft']['name']='inspect_resource';s['confirmed']=True
    store=Storage(tmp_path)
    with pytest.raises(ValueError,match='도구 식별자'):
        store.save_agent(s)
    assert not store.agents()
    store.close()


def test_repeated_bad_name_stops_and_can_recover_after_user_message(tmp_path):
    s=state();store=Storage(tmp_path);cfg={'configurable':{'thread_id':s['session_id']}}
    original=workflow_draft(s);calls=[]
    def planner(current):
        calls.append(1);out=deepcopy(original)
        out['name']='비용 조건 선별' if any('이름은 비용 조건 선별' in m['content'] for m in current['messages']) else 'inspect_resource'
        return {'draft':out,'confirmed':False,'repair_count':current.get('repair_count',0)+1,'saved_agent_id':None}
    graph,conn=build_graph(store,conversation=planner,capability_mode='off',condition_mode='off',verifier_mode='off')
    blocked=graph.invoke(s,cfg)
    assert blocked['status']=='validation_failed' and not store.agents() and len(calls)==2
    resumed=graph.invoke({**graph.get_state(cfg).values,'messages':blocked['messages']+[{'role':'user','content':'이름은 비용 조건 선별로 해줘'}], 'repair_count':0,'confirmed':False},cfg)
    assert resumed['status']=='ready' and resumed['draft']['name']=='비용 조건 선별'
    assert resumed['draft']['workflow']==original['workflow'] and not store.agents()
    saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    assert saved['status']=='saved'
    conn.close();store.close()
