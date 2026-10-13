"""Mocked-provider tests exercise real A4B contracts, checkpoints, SQLite and schedules."""
import copy,csv,io,json,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from agent_builder.storage import Storage,uid
from agent_builder.models import AgentDraft
from agent_builder.test_builder import draft,initial
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow_runtime import run_workflow,execute_node,validate_node_result
from agent_builder.workflow import workflow_errors
from agent_builder.reply_review import run_review,validate_report
from agent_builder.review_contracts import ReviewRequest,ReviewReport
from agent_builder.review_decisions import decide,approved_csv
from agent_builder.scheduling import Schedules
from agent_builder.worker import handle

REQUEST=ReviewRequest(customer_message='환불 방법과 처리 기간을 알려주세요.',policy_text='환불 신청 후 3일 이내에 안내합니다.',reply_draft='환불을 완료했습니다.')
def report():return {'verdict':'revise','summary':'실제 완료 근거가 없습니다.','requests':['환불 방법','처리 기간'],'findings':[{'category':'unverified_action','severity':'high','draft_quote':'환불을 완료했습니다.','evidence':[],'reason':'실행 결과 없음','suggestion':'신청 안내'}],'missing_info':[],'revised_reply':'환불 신청 후 3일 이내에 안내합니다. 실제 처리 여부는 담당자 확인이 필요합니다.'}
def output():return {'report':report(),'tool_calls':['get_customer_message','get_policy','get_reply_draft'],'usage':{'total_tokens':0}}
def seeded(tmp_path,table=False,scheduled=False):
    st=Storage(tmp_path);d=draft();s=initial()
    a={'id':uid(),'name':'정책.txt','kind':'document','format':'txt','text':REQUEST.policy_text,'pages':[REQUEST.policy_text],'columns':[],'hash':'real-policy-hash','version':2};st.add_asset(a,[])
    nodes=[node('review','reply_review')];edges=[];assets=[a['id']]
    if table:
        t={'id':uid(),'name':'문의.csv','kind':'table','format':'csv','columns':['customer_message','reply_draft'],'preview':[],'profile':{},'row_count':2,'text':''};st.add_asset(t,[{'customer_message':REQUEST.customer_message,'reply_draft':REQUEST.reply_draft}]*2);assets.insert(0,t['id'])
        nodes=[node('read','read_table'),node('review','reply_review',[bind('table','read','table')],fields=['customer_message','reply_draft'])];edges=[edge('read','review')]
    d.update(name='응대 검수',instructions='검수',tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)),document_ids=assets,workflow={'entry':nodes[0]['id'],'nodes':nodes,'edges':edges})
    d['execution_plan'].update(missing_policy='keep',duplicate_policy='keep')
    if scheduled:d.update(schedule={'frequency':'daily','timezone':'Asia/Seoul','hour':9,'minute':0,'weekday':None},report_destination='reports')
    s.update(draft=AgentDraft.model_validate(d).model_dump(),confirmed=True);id=st.save_agent(s)
    return st,id

def fake_review(monkeypatch):
    monkeypatch.setattr('agent_builder.reply_review.run_review',lambda req,llm:output())

def executed(tmp_path,monkeypatch,table=False):
    fake_review(monkeypatch);st,id=seeded(tmp_path,table)
    run=run_workflow(st,id,REQUEST.customer_message,'unused',reply_draft=REQUEST.reply_draft,llm=object())
    assert run['status']=='succeeded';return st,id,run

def test_real_tools_must_finish_before_report():
    def factory(**kwargs):
        messages=[]
        for t in kwargs['tools']:
            value=t.invoke({});assert isinstance(value,dict)
            messages.append(ToolMessage(content=json.dumps(value),name=t.name,tool_call_id=t.name))
        messages.append(AIMessage(content='',tool_calls=[{'name':'ReviewReport','args':report(),'id':'report'}]))
        class Agent:
            def invoke(self,*args):return {'structured_response':report(),'messages':messages}
        return Agent()
    assert run_review(REQUEST,object(),factory)['report']['verdict']=='revise'

def test_fabricated_tool_trace_and_early_report_rejected():
    def factory(**kwargs):
        for t in kwargs['tools']:t.invoke({})
        class Agent:
            def invoke(self,*args):return {'structured_response':report(),'messages':[AIMessage(content='',tool_calls=[{'name':'ReviewReport','args':report(),'id':'r'}]),*[ToolMessage(content='{}',name=t.name,tool_call_id=t.name) for t in kwargs['tools']]]}
        return Agent()
    with pytest.raises(ValueError,match='실제 조회'):run_review(REQUEST,object(),factory)

@pytest.mark.parametrize('change',[{'verdict':'pass'}, {'findings':[{**report()['findings'][0],'draft_quote':'없는 문장'}]}, {'findings':[{**report()['findings'][0],'category':'policy_conflict'}]}, {'findings':[{**report()['findings'][0],'category':'missing_request'}]}, {'verdict':'needs_info','findings':[],'missing_info':[]}])
def test_report_contract_checks_exact_quotes_evidence_and_verdict(change):
    with pytest.raises(ValueError):validate_report(ReviewReport.model_validate({**report(),**change}),REQUEST,REQUEST.reply_draft,{'get_customer_message','get_policy','get_reply_draft'})

def test_report_snapshot_sources_and_restart(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch);assert run['approval_status']=='pending';assert run['reviews'][0]['sources'][0]['version']==2
    assert (tmp_path/run['review_report_path']).is_file();assert run['artifact'] is None
    before=copy.deepcopy(run['outputs']);st.close();st=Storage(tmp_path)
    new=decide(st,run['id'],'review:0','approve','운영자가 수정한 최종 문장')
    assert new['reviews'][0]['decision']['edited_reply']=='운영자가 수정한 최종 문장';assert new['outputs']==before
    assert new['approval_status']=='decided';st.close()

def test_duplicate_decisions_are_idempotent_and_conflicts_rejected(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch)
    a=decide(st,run['id'],'review:0','approve','수정문');b=decide(st,run['id'],'review:0','approve','수정문');assert a==b
    with pytest.raises(ValueError,match='이미 결정'):decide(st,run['id'],'review:0','reject','')
    with pytest.raises(ValueError):decide(st,run['id'],'review:0','approve','다른 문장')
    assert st.db.execute('SELECT COUNT(*) FROM reply_review_decisions').fetchone()[0]==1;st.close()

def test_concurrent_approval_only_one_record(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch);st.close()
    def call(_):
        st=Storage(tmp_path)
        try:return decide(st,run['id'],'review:0','approve','같은 문장')
        finally:st.close()
    with ThreadPoolExecutor(max_workers=2) as pool:runs=list(pool.map(call,range(2)))
    assert runs[0]==runs[1];st=Storage(tmp_path);assert st.db.execute('SELECT COUNT(*) FROM reply_review_decisions').fetchone()[0]==1;st.close()

def test_rejected_and_pending_never_export_and_edited_only(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch,table=True)
    assert len(list(csv.reader(io.StringIO(approved_csv(st,id)['content']))))==1
    decide(st,run['id'],'review:0','reject','');decide(st,run['id'],'review:1','approve','=edited')
    rows=list(csv.DictReader(io.StringIO(approved_csv(st,id)['content'].lstrip('\ufeff'))))
    assert len(rows)==1 and rows[0]['approved_reply']=="'=edited";assert REQUEST.reply_draft not in rows[0]['approved_reply'];st.close()

def test_bad_ids_failed_runs_and_blank_approval_rejected(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch)
    with pytest.raises(ValueError):decide(st,run['id'],'unknown:0','approve','문장')
    with pytest.raises(ValueError):decide(st,run['id'],'review:0','approve',' ')
    run['status']='failed';st.save_test(run)
    with pytest.raises(ValueError):decide(st,run['id'],'review:0','approve','문장')
    st.close()

def test_schedule_uses_existing_queue_source_and_policy(tmp_path,monkeypatch):
    observed=[]
    def review(req,llm):observed.append(req);return output()
    monkeypatch.setattr('agent_builder.reply_review.run_review',review)
    st,id=seeded(tmp_path,table=True,scheduled=True);Schedules(st).enqueue_test(id,0);job=Schedules(st).claim_due()[0];st.close()
    run=handle({'directory':str(tmp_path),'action':'scheduled_run','id':job['id'],'lease_token':job['lease_token'],'api_key':'unused'})
    assert run['status']=='succeeded';assert run['approval_status']=='pending';assert len(run['reviews'])==2
    assert all(r.policy_text==REQUEST.policy_text for r in observed);assert run['source_version']==1
    st=Storage(tmp_path);assert Schedules(st).jobs(id)[0]['status']=='succeeded';assert not Schedules(st).claim_due();st.close()

def test_retry_reuses_completed_steps_and_same_snapshot(tmp_path,monkeypatch):
    fake_review(monkeypatch);st,id=seeded(tmp_path,table=True);counts={'read':0,'review':0}
    def executor(n,s,st,c,llm):
        counts[n['id']]+=1
        if n['id']=='review' and counts['review']==1:raise ValueError('시험 실패')
        return execute_node(n,s,st,c,llm)
    failed=run_workflow(st,id,'검수','unused',llm=object(),executor=executor);assert failed['status']=='failed'
    original=copy.deepcopy(failed['config_snapshot']);st.close();st=Storage(tmp_path)
    retried=run_workflow(st,id,'검수','unused',run_id=failed['id'],retry=True,llm=object(),executor=executor)
    assert retried['status']=='succeeded';assert retried['id']==failed['id'];assert retried['config_snapshot']==original;assert counts=={'read':1,'review':2};st.close()

def test_terminal_contract_prevents_unapproved_export():
    w={'entry':'review','nodes':[node('review','reply_review'),node('finish','finish')],'edges':[edge('review','finish')]}
    assert not workflow_errors(w,['reply_review','finish'],[])

def test_batch_limit_and_all_input_validation_before_model(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr('agent_builder.reply_review.run_review',lambda *a:calls.append(a))
    st,id=seeded(tmp_path,table=True);config=st.agent(id)['config'];table_id=config['document_ids'][0]
    st.db.execute('UPDATE builder_assets SET rows_json=? WHERE id=?',(json.dumps([{'customer_message':'문의','reply_draft':'초안'}]*11),table_id));st.db.commit()
    run=run_workflow(st,id,'검수','unused',llm=object());assert run['status']=='failed';assert not calls
    st.db.execute('UPDATE builder_assets SET rows_json=? WHERE id=?',(json.dumps([{'customer_message':'문의','reply_draft':'초안'},{'customer_message':'문의','reply_draft':''}]),table_id));st.db.commit()
    run=run_workflow(st,id,'검수','unused',llm=object());assert run['status']=='failed';assert not calls;st.close()

def test_newer_agent_config_does_not_change_run(tmp_path,monkeypatch):
    st,id,run=executed(tmp_path,monkeypatch);old=copy.deepcopy(run['config_snapshot']);agent=st.agent(id)
    state=initial();state.update(base_agent_id=id,base_version=1,draft={**agent['config'],'instructions':'새 기준'},confirmed=True);st.save_agent(state)
    assert st.agent(id)['version']==2;assert st.test(run['id'])['config_snapshot']==old;st.close()

def test_review_cannot_export_before_approval():
    w={'entry':'review','nodes':[node('review','reply_review'),node('export','export_result')],'edges':[edge('review','export')]}
    assert any('승인' in issue for issue in workflow_errors(w,['reply_review','export_result'],[]))

def test_compose_connected_to_reviewer_skips_duplicate_generic_correction(monkeypatch):
    import agent_builder.workflow_runtime as module
    class Business:
        def invoke(self,*args,**kwargs):return {'structured_response':module.GroundedAnswer(text='작성한 응대',citations=[module.Citation(source_id='doc:1:0',quote='정책 원문')])}
    monkeypatch.setattr(module,'create_deep_agent',lambda **kwargs:Business())
    class NoSecondModel:
        def with_structured_output(self,*args,**kwargs):raise AssertionError('중복 검토가 호출됨')
    compose=node('answer','compose_answer',[bind('documents','docs','documents')])
    reviewer=node('review','reply_review',[bind('text','answer','text')])
    config={'instructions':'응대 작성','workflow':{'nodes':[compose,reviewer],'edges':[edge('answer','review')]}}
    result=execute_node(compose,{'request':'질문','outputs':{'docs':{'documents':[{'source_id':'doc:1:0','file_name':'p.txt','page':None,'text':'정책 원문'}]}}},None,config,NoSecondModel())
    assert result['text']=='작성한 응대';assert result['evidence'][0]['quote']=='정책 원문'
