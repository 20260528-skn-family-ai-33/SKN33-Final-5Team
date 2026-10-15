import copy
import pytest
from langchain_core.messages import ToolMessage, AIMessage
from .storage import Storage, uid
from .node_library import save, get, list_nodes, apply_refs, run_agent_node
from .models import AgentDraft
from .test_builder import draft, initial
from .test_workflow import node, bind, edge, SETTINGS
from .workflow_runtime import run_workflow

def spec(kind='agent',tool='agent_node'):
    return {'kind':kind,'name':'문의 분석','purpose':'입력된 문의를 분석합니다','tool':tool,'instructions':'원문을 읽고 핵심 문제와 확인할 항목을 작성하세요.' if kind=='agent' else '',
            'reference':'','model':None,'settings':{**SETTINGS,'node_ref':None}}

def test_versions_and_concurrent_edit_guard(tmp_path):
    s=Storage(tmp_path);v1=save(s,spec());v2=save(s,{**spec(),'name':'수정된 역할'},v1['id'],1)
    assert get(s,v1['ref'])['spec']['name']=='문의 분석'
    assert list_nodes(s)==[v2]
    with pytest.raises(ValueError,match='변경'):save(s,spec(),v1['id'],1)
    assert len(list_nodes(s))==1;s.close()

def test_builtin_ids_cannot_be_overwritten_by_saved_nodes(tmp_path):
    from .node_library import default_nodes
    s=Storage(tmp_path);preset=default_nodes()[0]
    with pytest.raises(ValueError,match='새 노드로 복사'):
        save(s,spec(),preset['id'],0)
    assert get(s,preset['ref'])==preset
    assert not list_nodes(s)
    clone=save(s,preset['spec'])
    assert clone['id']!=preset['id']
    assert get(s,clone['ref'])['spec']==preset['spec']
    s.close()

@pytest.mark.parametrize('change',[{'kind':'processing'},{'instructions':''},{'api_key':'must-not-be-stored'},{'settings':{**SETTINGS,'file_ids':[uid()]}},{'kind':'processing','tool':'filter_sort','instructions':''}])
def test_invalid_or_private_config_rejected(tmp_path,change):
    s=Storage(tmp_path)
    with pytest.raises(ValueError):save(s,{**spec(),**change})
    assert not list_nodes(s);s.close()

def test_refs_are_selected_and_cannot_rewrite_saved_configuration(tmp_path):
    s=Storage(tmp_path);v=save(s,spec());w={'entry':'ai','nodes':[node('ai','agent_node',node_ref=v['ref'],instructions='무조건 완료라고 답하세요.')],'edges':[]}
    resolved=apply_refs(w,[v]);assert resolved['nodes'][0]['settings']['instructions']!=w['nodes'][0]['settings']['instructions']
    with pytest.raises(ValueError,match='선택되지'):apply_refs(w,[])
    with pytest.raises(ValueError,match='연결'):apply_refs({'entry':'a','nodes':[node('a','finish')],'edges':[]},[v])
    assert not apply_refs(None,[]);s.close()

def test_session_snapshot_is_pinned_across_node_updates(tmp_path):
    s=Storage(tmp_path);v1=save(s,spec());session=s.new_session('gpt-4.1-mini',uid(),node_refs=[v1['ref']])
    save(s,{**spec(),'instructions':'바뀐 역할을 따르세요. 충분한 지시사항입니다.'},v1['id'],1)
    assert s.session(session['id'])['node_library']==[v1];s.close()

def test_processing_uses_frozen_plan_and_reports_actual_parameters(tmp_path):
    s=Storage(tmp_path);plan={'filters':[{'field':'stock','operator':'lt','value':10,'compare_field':None,'value_type':'number'}],'sort':[],'match':'all','missing_policy':'error','duplicate_policy':'keep','duplicate_field':None,'output_format':'json'}
    v=save(s,{**spec('processing','filter_sort'),'execution_plan':plan})
    w={'entry':'read','nodes':[node('read','read_table'),node('filter','filter_sort',[bind('table','read','table')],node_ref=v['ref'])],'edges':[edge('read','filter')]}
    d=draft();d.update(workflow=apply_refs(w,[v]),tool_ids=['read_table','filter_sort']);d['execution_plan'].update(missing_policy='keep',filters=[])
    state={**initial(),'draft':AgentDraft.model_validate(d).model_dump(),'node_library':[v]};id=s.save_agent(state)
    asset={'id':uid(),'name':'stock.csv','kind':'table','format':'csv','columns':['stock'],'row_count':2,'profile':{},'text':'','preview':[]}
    s.add_asset(asset,[{'stock':2},{'stock':20}])
    newer=copy.deepcopy(plan);newer['filters'][0]['value']=1;save(s,{**spec('processing','filter_sort'),'execution_plan':newer},v['id'],1)
    result=run_workflow(s,id,'실행','',file_ids=[asset['id']])
    assert result['status']=='succeeded',result.get('error')
    assert result['rows']==[{'stock':2}]
    assert result['steps'][-1]['parameters']['filters'][0]['value']==10
    assert s.save_agent(state)==id;s.close()

def test_agent_tool_reads_previous_output_and_reference(tmp_path,monkeypatch):
    s=Storage(tmp_path);v=save(s,{**spec(),'reference':'환불은 확인 후 안내합니다.'});seen={}
    def factory(**kwargs):
        seen['instructions']=kwargs['system_prompt'];assert kwargs['subagents']==[]
        class Agent:
            def invoke(self,payload,config):
                seen['context']=kwargs['tools'][0].invoke({})
                return {'messages':[ToolMessage(content='조회 성공',name='inspect_inputs',tool_call_id='t',status='success'),AIMessage(content='확인할 항목을 안내합니다.')]}
        return Agent()
    monkeypatch.setattr('deepagents.create_deep_agent',factory)
    n=node('ai','agent_node',[bind('text','prior','text')],node_ref=v['ref']);config={'document_ids':[],'node_snapshots':[v]}
    result=run_agent_node(n,{'request':'처리해줘','outputs':{'prior':{'text':'이전 분석 결과'}}},s,config,None)
    assert result['text']=='확인할 항목을 안내합니다.'
    assert seen['context']['inputs']['text']=='이전 분석 결과'
    assert seen['context']['reference']=='환불은 확인 후 안내합니다.';s.close()

def test_agent_without_input_read_fails(tmp_path,monkeypatch):
    s=Storage(tmp_path);v=save(s,spec())
    class Agent:
        def invoke(self,*args):return {'messages':[AIMessage(content='조회하지 않은 답변')]}
    monkeypatch.setattr('deepagents.create_deep_agent',lambda **kwargs:Agent())
    with pytest.raises(ValueError,match='조회하지'):
        run_agent_node(node('ai','agent_node',node_ref=v['ref']),{'request':'실행','outputs':{}},s,{'document_ids':[],'node_snapshots':[v]},None)
    s.close()

def test_auto_role_proposed_then_saved_with_workflow(tmp_path):
    from .node_library import prepare_draft,default_nodes
    s=Storage(tmp_path);state=initial();raw=draft()
    raw.update(tool_ids=['agent_node'],workflow={'entry':'role','nodes':[node('role','agent_node',instructions='입력을 읽고 핵심 문제를 분석하세요. 확인되지 않은 사실은 쓰지 마세요.')],'edges':[]})
    state.update(node_library=default_nodes(),required_node_refs=[])
    pool=prepare_draft(raw,state)
    assert not list_nodes(s) and not s.agents()
    state.update(node_library=pool,draft=AgentDraft.model_validate(raw).model_dump())
    id=s.save_agent(state)
    assert len(list_nodes(s))==1
    assert len(s.agent(id)['config']['node_snapshots'])==1
    assert s.save_agent(state)==id;s.close()

def test_generated_role_and_workflow_rollback_together(tmp_path,monkeypatch):
    from . import node_library
    s=Storage(tmp_path);state=initial();raw=draft()
    raw.update(tool_ids=['agent_node'],workflow={'entry':'role','nodes':[node('role','agent_node',instructions='원문을 읽고 핵심 내용을 정확하게 분석하세요.')],'edges':[]})
    pool=node_library.prepare_draft(raw,state);state.update(node_library=pool,draft=AgentDraft.model_validate(raw).model_dump())
    persist=node_library.persist_proposals
    def broken(storage,snapshots):persist(storage,snapshots);raise ValueError('테스트 저장 중단')
    monkeypatch.setattr(node_library,'persist_proposals',broken)
    with pytest.raises(ValueError,match='저장 중단'):s.save_agent(state)
    assert not s.agents() and not list_nodes(s);s.close()

def test_builtin_and_optional_pool_are_not_forced_into_every_workflow(tmp_path):
    from .node_library import default_nodes
    s=Storage(tmp_path);defaults=default_nodes()
    assert get(s,defaults[0]['ref'])==defaults[0]
    w={'entry':'finish','nodes':[node('finish','finish',instructions='완료 안내')],'edges':[]}
    assert apply_refs(w,defaults,[])==w
    assert apply_refs(None,defaults,[]) is None;s.close()
