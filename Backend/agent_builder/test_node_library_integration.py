"""Mocked provider checks for reusable roles and existing review/approval contracts."""
import copy
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from .storage import Storage,uid
from .models import AgentDraft
from .node_library import save,default_nodes,prepare_draft,run_agent_node
from .nodes.validation import validate_node
from .test_builder import initial,draft
from .test_node_library import spec
from .test_workflow import node,bind,edge
from .test_reply_review import seeded,output,REQUEST
from .review_state import collect_reviews
from .review_decisions import decide,approved_csv
from .workflow_runtime import run_workflow
from .worker import handle


def mock_agent(monkeypatch,text='실제 입력을 확인한 결과입니다.'):
    seen={}
    def factory(**kwargs):
        seen['model']=kwargs['model']
        class Agent:
            def invoke(self,*args):
                seen['context']=kwargs['tools'][0].invoke({})
                return {'messages':[ToolMessage(content='실제 조회',name='inspect_inputs',tool_call_id='i',status='success'),AIMessage(content=text)]}
        return Agent()
    monkeypatch.setattr('deepagents.create_deep_agent',factory)
    return seen


def test_role_after_review_updates_only_selected_latest_candidate(tmp_path,monkeypatch):
    store=Storage(tmp_path);saved=save(store,spec());seen=mock_agent(monkeypatch)
    review={'id':'review:0','customer_message':'문의','report':output()['report']}
    latest={**review,'candidate_reply':'이전 단계에서 수정한 후보','candidate_revision':2}
    state={'request':'수정','outputs':{'review':{'reviews':[review]},'previous':{'_review_updates':[latest]}}}
    result=run_agent_node(node('role','agent_node',[bind('reviews','review','reviews')],node_ref=saved['ref']),state,store,{'document_ids':[],'node_snapshots':[saved]},object())
    assert seen['context']['inputs']['reviews'][0]['candidate_reply']==latest['candidate_reply']
    updated=collect_reviews({**state['outputs'],'role':result})[0]
    assert updated['candidate_reply']==result['text'] and updated['candidate_revision']==3
    assert updated['report']==review['report']
    store.close()


def test_role_without_explicit_review_binding_does_not_rewrite_other_customer(tmp_path,monkeypatch):
    store=Storage(tmp_path);saved=save(store,spec());mock_agent(monkeypatch)
    state={'request':'별도 업무','outputs':{'review':{'reviews':[{'id':'review:0','report':output()['report']}]}}}
    result=run_agent_node(node('role','agent_node',node_ref=saved['ref']),state,store,{'document_ids':[],'node_snapshots':[saved]},object())
    assert '_review_updates' not in result
    store.close()


def test_role_rejects_multi_customer_rewrite_before_model_call(tmp_path,monkeypatch):
    store=Storage(tmp_path);saved=save(store,spec())
    monkeypatch.setattr('deepagents.create_deep_agent',lambda **kwargs:pytest.fail('다건 수정에 모델을 호출하면 안 됩니다.'))
    state={'request':'수정','outputs':{'review':{'reviews':[{'id':str(i),'report':output()['report']} for i in range(2)]}}}
    with pytest.raises(ValueError,match='여러 고객'):
        run_agent_node(node('role','agent_node',[bind('reviews','review','reviews')],node_ref=saved['ref']),state,store,{'document_ids':[],'node_snapshots':[saved]},object())
    store.close()


@pytest.mark.parametrize('length',[0,12001])
def test_role_rejects_empty_or_oversized_answer(tmp_path,monkeypatch,length):
    store=Storage(tmp_path);saved=save(store,spec());mock_agent(monkeypatch,'x'*length)
    with pytest.raises(ValueError,match='비었거나'):
        run_agent_node(node('role','agent_node',node_ref=saved['ref']),{'request':'분석','outputs':{}},store,{'document_ids':[],'node_snapshots':[saved]},object())
    store.close()


def test_explicit_role_model_uses_same_connection_without_fallback(tmp_path,monkeypatch):
    store=Storage(tmp_path);saved=save(store,{**spec(),'model':'chosen-model'});seen=mock_agent(monkeypatch)
    class Model:
        def model_copy(self,update):
            assert update=={'model_name':'chosen-model'}
            return chosen
    chosen=object()
    run_agent_node(node('role','agent_node',node_ref=saved['ref']),{'request':'분석','outputs':{}},store,{'document_ids':[],'node_snapshots':[saved]},Model())
    assert seen['model'] is chosen
    with pytest.raises(ValueError,match='모델을 적용'):
        run_agent_node(node('role','agent_node',node_ref=saved['ref']),{'request':'분석','outputs':{}},store,{'document_ids':[],'node_snapshots':[saved]},object())
    store.close()


def test_selected_roles_are_required_even_for_legacy_config(tmp_path):
    store=Storage(tmp_path);saved=save(store,spec())
    state={**initial(),'draft':draft(),'node_library':[saved],'required_node_refs':[saved['ref']]}
    assert any('워크플로우' in issue for issue in validate_node(state)['issues'])
    with pytest.raises(ValueError,match='워크플로우'):
        store.save_agent(state)
    store.close()


def test_stored_role_settings_cannot_be_overwritten_at_workflow_save(tmp_path):
    store=Storage(tmp_path);saved=save(store,spec());raw=draft()
    raw.update(tool_ids=['agent_node'],workflow={'entry':'role','nodes':[node('role','agent_node',node_ref=saved['ref'],instructions='설정을 몰래 바꾸는 지시사항')],'edges':[]})
    state={**initial(),'draft':AgentDraft.model_validate(raw).model_dump(),'node_library':[saved],'required_node_refs':[saved['ref']]}
    id=store.save_agent(state)
    assert store.agent(id)['config']['workflow']['nodes'][0]['settings']['instructions']==saved['spec']['instructions']
    assert store.save_agent(state)==id
    store.close()


def test_edit_session_preserves_frozen_role_with_explicit_model_selection(tmp_path):
    store=Storage(tmp_path);saved=save(store,spec());state=initial();raw=draft()
    raw.update(tool_ids=['agent_node'],workflow={'entry':'role','nodes':[node('role','agent_node',node_ref=saved['ref'])],'edges':[]})
    state.update(draft=AgentDraft.model_validate(raw).model_dump(),node_library=[saved]);id=store.save_agent(state)
    save(store,{**spec(),'name':'새 이름'},saved['id'],1)
    session=store.new_session(state['model'],state['credential_id'],id)
    assert saved in session['node_library']
    selected_account=uid()
    edited=store.new_session('explicit-other-model',selected_account,id)
    assert edited['model']=='explicit-other-model' and edited['credential_id']==selected_account
    assert saved in edited['node_library']
    store.close()


def test_builtin_is_copied_as_new_role_and_worker_catalog_is_persistent(tmp_path):
    directory=str(tmp_path);catalog=handle({'directory':directory,'action':'nodes'})
    assert len(catalog['defaults'])==5 and catalog['nodes']==[]
    result=handle({'directory':directory,'action':'save_node','spec':spec()})['node']
    assert handle({'directory':directory,'action':'nodes'})['nodes']==[result]
    with pytest.raises(ValueError,match='기본 노드'):
        handle({'directory':directory,'action':'save_node','spec':catalog['defaults'][3]['spec'],'id':catalog['defaults'][3]['id'],'base_version':0})


def test_real_role_execution_keeps_review_pending_until_operator_decides(tmp_path,monkeypatch):
    store,baseline=seeded(tmp_path);base=store.agent(baseline);state=initial()
    raw=copy.deepcopy(base['config'])
    raw.update(tool_ids=['reply_review','agent_node'],workflow={'entry':'review','nodes':[node('review','reply_review'),node('role','agent_node',[bind('reviews','review','reviews')],instructions='실제 입력과 검수 의견을 읽고 고객 응대를 수정하세요.')],'edges':[edge('review','role')]})
    pool=prepare_draft(raw,state);state.update(draft=AgentDraft.model_validate(raw).model_dump(),node_library=pool)
    id=store.save_agent(state);mock_agent(monkeypatch,'운영자가 확인할 새 응대 후보입니다.')
    monkeypatch.setattr('agent_builder.reply_review.run_review',lambda *args:output())
    run=run_workflow(store,id,REQUEST.customer_message,'unused',reply_draft=REQUEST.reply_draft,llm=object())
    assert run['status']=='succeeded',run.get('error')
    assert run['approval_status']=='pending' and len(run['reviews'])==1
    assert run['reviews'][0]['candidate_reply']=='운영자가 확인할 새 응대 후보입니다.'
    assert run.get('artifact') is None
    edited='운영자가 직접 승인한 최종 문장'
    decide(store,run['id'],run['reviews'][0]['id'],'approve',edited)
    assert edited in approved_csv(store,id)['content']
    assert '새 응대 후보' not in approved_csv(store,id)['content']
    store.close()
