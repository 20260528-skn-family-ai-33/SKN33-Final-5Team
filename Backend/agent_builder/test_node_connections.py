import copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.storage import uid,Storage
from agent_builder.test_scheduling import seed
from agent_builder.test_builder import initial
from agent_builder.test_workflow import node,bind,edge
from agent_builder.test_reply_review import seeded,output
from agent_builder.models import AgentDraft
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import run_workflow

def plan(field,operator,value):return {'filters':[{'field':field,'operator':operator,'value':value,'compare_field':None,'value_type':'number'}],'sort':[],'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'csv'}
def save(st,config,model='unused',credential='fixture'):
    state=initial();state.update(draft=AgentDraft.model_validate(config).model_dump(),confirmed=True,model=model,credential_id=credential)
    return st.save_agent(state)

def test_two_filter_instances_have_different_rules_and_writer_format(tmp_path):
    st,id=seed(tmp_path);d=copy.deepcopy(st.agent(id)['config']);d['schedule']=None
    nodes=[node('read','read_table'),node('f1','filter_sort',[bind('table','read','table')],execution_plan=plan('stock','gt',5)),node('f2','filter_sort',[bind('table','f1','table')],execution_plan=plan('stock','lt',15)),node('write','write_result',[bind('table','f2','table')],format='json')]
    d.update(workflow={'entry':'read','nodes':nodes,'edges':[edge('read','f1'),edge('f1','f2'),edge('f2','write')]},tool_ids=['read_table','filter_sort','write_result'])
    parent=save(st,d);run=run_workflow(st,parent,'execute','')
    assert run['status']=='succeeded',run.get('error');assert [r['code'] for r in run['rows']]==['C'];assert run['artifact']['mime'].startswith('application/json')
    assert run['steps'][1]['parameters']['filters'][0]['operator']=='gt';assert run['steps'][2]['parameters']['filters'][0]['operator']=='lt';st.close()

def test_review_connects_to_condition_report_writer_without_approving(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);d=copy.deepcopy(st.agent(id)['config'])
    d['workflow']['nodes'] += [node('decision','condition',[bind('reviews','review','reviews')],condition_operator='any_revision'),node('report','review_summary',[bind('reviews','decision','reviews')]),node('save','write_result',[bind('table','report','table')],format='csv'),node('done','finish',instructions='통과 안내')]
    d['workflow']['edges'] += [edge('review','decision'),edge('decision','report','yes'),edge('decision','done','no'),edge('report','save')]
    d['tool_ids']=['read_table','reply_review','condition','review_summary','write_result','finish']
    assert not workflow_errors(d['workflow'],d['tool_ids'],[st.asset(a) for a in d['document_ids']])
    monkeypatch.setattr('agent_builder.reply_review.run_review',lambda *a:output());parent=save(st,d);run=run_workflow(st,parent,'review','unused',llm=object())
    assert run['status']=='succeeded',run.get('error');assert run['approval_status']=='pending';assert all(r['approval_state']=='pending' for r in run['rows']);assert run['artifact']['name']=='검수_보고서.csv';st.close()

def reusable(st):
    st,id=seed(st);d=copy.deepcopy(st.agent(id)['config']);d['schedule']=None
    d.update(tool_ids=['read_table','filter_sort'],workflow={'entry':'read','nodes':[node('read','read_table'),node('filter','filter_sort',[bind('table','read','table')],execution_plan=plan('stock','gt',5))],'edges':[edge('read','filter')]})
    child=save(st,d);return st,child,d

def test_saved_workflow_reused_twice_with_forwarded_outputs_and_version_pin(tmp_path):
    st,child,d=reusable(tmp_path)
    cfg=copy.deepcopy(d);cfg['workflow']={'entry':'read','nodes':[node('read','read_table'),node('first','saved_workflow',[bind('table','read','table')],agent_id=child,agent_version=1,output_node='filter',output_port='table'),node('second','saved_workflow',[bind('table','first','table')],agent_id=child,agent_version=1,output_node='filter',output_port='table'),node('write','write_result',[bind('table','second','table')],format='csv')],'edges':[edge('read','first'),edge('first','second'),edge('second','write')]};cfg['tool_ids']=['read_table','saved_workflow','write_result']
    parent=save(st,cfg);run=run_workflow(st,parent,'execute','unused')
    assert run['status']=='succeeded',run.get('error');assert [r['code'] for r in run['rows']]==['A','C'];assert run['steps'][1]['dependency']=={'agent_id':child,'version':1};assert run['steps'][1]['child_steps']
    state=initial();d['workflow']['nodes'][1]['settings']['execution_plan']=plan('stock','lt',5);state.update(draft=AgentDraft.model_validate(d).model_dump(),base_agent_id=child,base_version=1,confirmed=True,model='unused',credential_id='fixture');st.save_agent(state)
    again=run_workflow(st,parent,'execute','unused');assert [r['code'] for r in again['rows']]==['A','C'];st.close()

def test_saved_workflow_rejects_recursive_reference_and_wrong_account(tmp_path):
    st,child,d=reusable(tmp_path);cfg=copy.deepcopy(d);cfg.update(tool_ids=['saved_workflow'],workflow={'entry':'call','nodes':[node('call','saved_workflow',agent_id=child,agent_version=1,output_node='filter',output_port='table')],'edges':[]})
    state=initial();state.update(draft=AgentDraft.model_validate(cfg).model_dump(),base_agent_id=child,base_version=1,confirmed=True,model='unused',credential_id='fixture')
    with pytest.raises(ValueError,match='재귀'):st.save_agent(state)
    with pytest.raises(ValueError,match='같은 모델'):save(st,cfg,credential='different')
    st.close()

def test_saved_flow_waits_and_resumes_through_parent_checkpoint(tmp_path):
    st,child,d=reusable(tmp_path)
    cfg=copy.deepcopy(d);cfg['tool_ids']=['read_table','ask_user'];cfg['workflow']={'entry':'read','nodes':[node('read','read_table'),node('wait','ask_user',question='이어서 진행할까요?')],'edges':[edge('read','wait')]}
    waiting=save(st,cfg)
    parent=copy.deepcopy(d);parent['tool_ids']=['read_table','saved_workflow','write_result'];parent['workflow']={'entry':'read','nodes':[node('read','read_table'),node('call','saved_workflow',[bind('table','read','table')],agent_id=waiting,agent_version=1,output_node='read',output_port='table'),node('write','write_result',[bind('table','call','table')],format='csv')],'edges':[edge('read','call'),edge('call','write')]}
    id=save(st,parent);run=run_workflow(st,id,'execute','unused');assert run['status']=='needs_input';assert run['question']['question']=='이어서 진행할까요?';rid=run['id'];st.close()
    st=Storage(tmp_path);done=run_workflow(st,id,'execute','unused',run_id=rid,answer='계속 진행')
    assert done['status']=='succeeded',done.get('error');assert done['id']==rid;assert len(done['rows'])==3;st.close()

def test_filter_after_aggregate_validates_the_new_columns(tmp_path):
    st,id=seed(tmp_path);d=copy.deepcopy(st.agent(id)['config']);d['schedule']=None
    d.update(tool_ids=['read_table','aggregate_table','filter_sort'],workflow={'entry':'read','nodes':[node('read','read_table'),node('aggregate','aggregate_table',[bind('table','read','table')],aggregation='count',fields=[]),node('filter','filter_sort',[bind('table','aggregate','table')],execution_plan=plan('count','gte',2))],'edges':[edge('read','aggregate'),edge('aggregate','filter')]})
    assert not workflow_errors(d['workflow'],d['tool_ids'],[st.asset(a) for a in d['document_ids']],d['execution_plan'])
    id=save(st,d);assert run_workflow(st,id,'execute','')['rows']==[{'count':3}]
    d['workflow']['nodes'][-1]['settings']['execution_plan']=plan('stock','gte',2)
    assert any('앞 단계 출력' in v for v in workflow_errors(d['workflow'],d['tool_ids'],[st.asset(a) for a in d['document_ids']],d['execution_plan']));st.close()

def test_catalog_exposes_actual_output_schema(tmp_path):
    from agent_builder.reusable_workflows import catalog
    st,child,d=reusable(tmp_path);items=catalog(st,'unused','fixture');entry=next(a for a in items if a['id']==child)
    assert next(o for o in entry['outputs'] if o['node_id']=='filter')['columns']==['code','stock','reorder_level'];st.close()

def test_reused_table_workflow_does_not_construct_a_model(tmp_path,monkeypatch):
    st,child,d=reusable(tmp_path);cfg=copy.deepcopy(d);cfg.update(tool_ids=['saved_workflow'],workflow={'entry':'call','nodes':[node('call','saved_workflow',agent_id=child,agent_version=1,output_node='filter',output_port='table')],'edges':[]})
    id=save(st,cfg)
    monkeypatch.setattr('agent_builder.workflow_runtime.ChatOpenAI',lambda **kw:(_ for _ in ()).throw(AssertionError('pure graph requested a model')))
    assert run_workflow(st,id,'execute','')['status']=='succeeded';st.close()

def test_reused_flow_accepts_explicit_run_file_override(tmp_path):
    st,child,d=reusable(tmp_path);cfg=copy.deepcopy(d);cfg.update(tool_ids=['saved_workflow'],workflow={'entry':'call','nodes':[node('call','saved_workflow',agent_id=child,agent_version=1,output_node='filter',output_port='table')],'edges':[]});id=save(st,cfg)
    asset={'id':uid(),'name':'new.csv','kind':'table','format':'csv','columns':['code','stock','reorder_level'],'profile':{},'text':''};st.add_asset(asset,[{'code':'NEW','stock':8,'reorder_level':10}])
    run=run_workflow(st,id,'execute','',file_ids=[asset['id']]);assert run['status']=='succeeded';assert run['rows']==[{'code':'NEW','stock':8,'reorder_level':10}];st.close()

def test_saved_text_output_is_presented_as_the_parent_answer(tmp_path):
    st,child,d=reusable(tmp_path);text=copy.deepcopy(d);text.update(tool_ids=['finish'],workflow={'entry':'done','nodes':[node('done','finish',instructions='재사용 흐름의 실제 안내')],'edges':[]});text_id=save(st,text)
    parent=copy.deepcopy(d);parent.update(tool_ids=['saved_workflow'],workflow={'entry':'call','nodes':[node('call','saved_workflow',agent_id=text_id,agent_version=1,output_node='done',output_port='text')],'edges':[]});id=save(st,parent)
    run=run_workflow(st,id,'execute','');assert run['status']=='succeeded';assert run['answer']=='재사용 흐름의 실제 안내';st.close()
