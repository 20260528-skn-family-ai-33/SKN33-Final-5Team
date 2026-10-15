import sys,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.models import AgentDraft
from agent_builder.storage import Storage,uid
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import run_workflow,execute_node,Facts,Fact
from agent_builder.test_builder import draft,initial
from agent_builder.tools.filter_sort import filter_sort_node

SETTINGS={'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,'question':None,'condition_field':None,'condition_operator':None}
def node(id,tool,bindings=None,**settings):return {'id':id,'tool':tool,'label':id,'bindings':bindings or [],'settings':{**SETTINGS,**settings}}
def bind(input,id,output):return {'input':input,'node_id':id,'output':output}
def edge(a,b,branch='next'):return {'source':a,'target':b,'branch':branch}
def flow(branch=False):
    nodes=[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['제목','금액'])]
    edges=[edge('read','extract')]
    if branch:
        nodes += [node('check','condition',[bind('records','extract','records')],condition_operator='has_missing'),node('ask','ask_user',[bind('records','check','records')],question='금액 누락을 확인하고 계속할까요?'),node('out_yes','export_result',[bind('records','ask','records'),bind('answer','ask','answer')],format='json'),node('out_no','export_result',[bind('records','check','records')],format='json')]
        edges += [edge('extract','check'),edge('check','ask','yes'),edge('check','out_no','no'),edge('ask','out_yes')]
    else:
        nodes += [node('out','export_result',[bind('records','extract','records')],format='json')];edges += [edge('extract','out')]
    return {'entry':'read','nodes':nodes,'edges':edges}
class FakeLLM:
    def with_structured_output(self,*args,**kwargs):return self
    def invoke(self,messages):
        data=json.loads(messages[-1]['content']);d=data['documents'][0]
        return Facts(facts=[Fact(field='제목',value='테스트',source_id=d['source_id'],quote='제목: 테스트'),Fact(field='금액',value=None,source_id=None,quote=None)])
def saved(tmp_path,w):
    store=Storage(tmp_path);s=initial();d=draft();d['workflow']=w;d['tool_ids']=list(dict.fromkeys(n['tool'] for n in w['nodes']));d['document_ids']=[]
    s.update(draft=AgentDraft.model_validate(d).model_dump(),confirmed=True)
    id=store.save_agent(s)
    asset={'id':uid(),'name':'sample.txt','kind':'document','format':'txt','text':'제목: 테스트\n작성일: 2026-10-12','pages':['제목: 테스트\n작성일: 2026-10-12'],'columns':[]};store.add_asset(asset,[])
    return store,id,asset

def test_contract_rejects_type_missing_port_and_cycles():
    w=flow();tools=[n['tool'] for n in w['nodes']]
    assert not workflow_errors(w,tools,[])
    w['nodes'][1]['bindings'][0]['output']='wrong';assert workflow_errors(w,tools,[])
    w=flow();w['nodes'][-1]['bindings'][0]=bind('records','read','documents');assert any('자료형' in s for s in workflow_errors(w,tools,[]))
    w=flow();w['edges'].append(edge('out','read'));assert any('순환' in s for s in workflow_errors(w,tools,[]))

def test_branch_contract_rejects_path_without_input():
    w=flow(True);tools=[n['tool'] for n in w['nodes']]
    assert not workflow_errors(w,tools,[])
    w['nodes'][-1]['bindings'][0]=bind('records','ask','records')
    assert any('입력' in s for s in workflow_errors(w,tools,[]))

def test_actual_extract_export_and_exact_evidence(tmp_path):
    store,id,asset=saved(tmp_path,flow());run=run_workflow(store,id,'추출', 'unused',file_ids=[asset['id']],llm=FakeLLM())
    assert run['status']=='succeeded';assert len(run['rows'])==2;assert run['rows'][0]['evidence']['quote']=='제목: 테스트';assert run['rows'][1]['value'] is None
    assert json.loads(run['artifact']['content'])[0]['value']=='테스트';assert store.agent(id)['config']['document_ids']==[];store.close()

def test_checkpoint_resume_same_id_after_restart_and_no_forged_evidence(tmp_path):
    store,id,asset=saved(tmp_path,flow(True));run=run_workflow(store,id,'추출','unused',file_ids=[asset['id']],llm=FakeLLM())
    assert run['status']=='needs_input';assert run['question']['node_id']=='ask';run_id=run['id'];store.close()
    store=Storage(tmp_path);second=run_workflow(store,id,'추출','unused',run_id=run_id,answer='누락을 표시하고 계속하세요',llm=FakeLLM())
    assert second['id']==run_id;assert second['status']=='succeeded'
    result=json.loads(second['artifact']['content']);assert result['user_confirmation']=='누락을 표시하고 계속하세요';assert result['records'][1]['value'] is None
    assert len([s for s in second['steps'] if s['node']=='extract'])==1;assert all(s['node']!='out_no' for s in second['steps']);store.close()

def test_no_missing_branch_skips_question(tmp_path):
    store,id,asset=saved(tmp_path,flow(True))
    def executor(n,s,st,c,l):
        if n['tool']=='extract_information':return {'records':[{'file_name':'sample','field':'금액','value':'10','status':'verified','evidence':None}]}
        return execute_node(n,s,st,c,l)
    run=run_workflow(store,id,'실행','unused',file_ids=[asset['id']],llm=FakeLLM(),executor=executor)
    assert run['status']=='succeeded';assert not run['question'];assert all(s['node']!='ask' for s in run['steps']);store.close()

def test_read_only_completion_and_failed_node_log(tmp_path):
    w={'entry':'read','nodes':[node('read','read_document')],'edges':[]};store,id,asset=saved(tmp_path,w)
    run=run_workflow(store,id,'읽기','unused',file_ids=[asset['id']],llm=FakeLLM());assert run['status']=='succeeded';assert run['artifact'] is None
    failed=run_workflow(store,id,'읽기','unused',llm=FakeLLM());assert failed['status']=='failed';assert failed['steps'][-1]['node']=='read';store.close()

def test_fake_quote_rejected(tmp_path):
    class Bad(FakeLLM):
        def invoke(self,messages):
            data=json.loads(messages[-1]['content']);d=data['documents'][0]
            return Facts(facts=[Fact(field='제목',value='가짜',source_id=d['source_id'],quote='가짜 원문')])
    store,id,asset=saved(tmp_path,flow());run=run_workflow(store,id,'실행','unused',file_ids=[asset['id']],llm=Bad());assert all(r['value'] is None and r['evidence'] is None for r in run['rows']);store.close()

def test_missing_keep_does_not_bypass_other_filter_and_exclude_alone():
    def ctx():return {'rows':[{'stock':None,'country':'US'},{'stock':2,'country':'KR'}],'columns':['stock','country'],'file_id':'a','trace':[]}
    c=ctx();filter_sort_node(c,[{'field':'stock','operator':'lt','value':10,'value_type':'number'},{'field':'country','operator':'eq','value':'KR','value_type':'text'}],[],missing_policy='keep');assert c['rows']==[{'stock':2,'country':'KR'}]
    c=ctx();filter_sort_node(c,[],[],missing_policy='exclude');assert len(c['rows'])==1

def test_passthrough_port_must_exist_in_actual_input():
    w=flow(True);w['nodes'][3]['bindings'].append(bind('text','check','text'))
    assert any('전달하지 않는' in s for s in workflow_errors(w,list(dict.fromkeys(n['tool'] for n in w['nodes'])),[]))

def test_semantic_branch_uses_real_user_reply(tmp_path):
    from agent_builder.workflow_runtime import Decision,InputAssessment
    class ReplyLLM:
        def with_structured_output(self,schema,**kw):self.schema=schema;return self
        def invoke(self,messages):
            data=json.loads(messages[-1]['content'])
            if self.schema==InputAssessment:return InputAssessment(valid=data['answer'] in ['해결됐어요','미해결이에요'],clarification='해결 여부를 알려주세요.')
            return Decision(decision=data['inputs']['answer']=='해결됐어요',reason='직원 응답')
    w={'entry':'ask','nodes':[node('ask','ask_user',question='해결됐나요?',instructions='해결 여부가 명확해야 합니다.'),node('check','condition',[bind('answer','ask','answer')],condition_operator='semantic',instructions='직원이 해결됐다고 답했는가'),node('yes','finish',instructions='해결 완료'),node('no','finish',instructions='미해결 문의 정리 필요')],'edges':[edge('ask','check'),edge('check','yes','yes'),edge('check','no','no')]}
    store,id,_=saved(tmp_path,w);first=run_workflow(store,id,'실행','unused',llm=ReplyLLM());assert first['status']=='needs_input'
    second=run_workflow(store,id,'실행','unused',run_id=first['id'],answer='잘 모르겠어요',llm=ReplyLLM());assert second['status']=='needs_input';assert not second.get('outputs',{}).get('check')
    final=run_workflow(store,id,'실행','unused',run_id=first['id'],answer='해결됐어요',llm=ReplyLLM());assert final['status']=='succeeded';assert final['answer']=='해결 완료';assert all(s['node']!='no' for s in final['steps']);store.close()

def test_business_deep_agent_tool_registration_and_quote_validation(tmp_path,monkeypatch):
    import agent_builder.workflow_runtime as module
    class Business:
        def invoke(self,*args,**kwargs):return {'structured_response':module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])}
    def create(**kwargs):
        assert kwargs['tools'][0].name=='inspect_evidence'
        assert kwargs['tools'][0].invoke({})[0]['quote']=='실제 원문'
        return Business()
    monkeypatch.setattr(module,'create_deep_agent',create)
    class Review:
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,*args):return module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])
    n=node('answer','compose_answer',[bind('documents','read','documents')])
    result=execute_node(n,{'request':'질문','outputs':{'read':{'documents':[{'source_id':'doc:1:0','file_id':'doc','file_name':'a.pdf','page':1,'offset':0,'text':'실제 원문'}]}}},None,{'instructions':'근거 답변'},Review())
    assert result['text']=='자료 안내';assert result['evidence'][0]['quote']=='실제 원문'


def test_original_quote_normalizes_layout_only():
    from agent_builder.workflow_runtime import original_quote
    text='네트워크 관리\n자에게 문의하십시오.'
    assert original_quote(text,'네트워크 관리자에게 문의하십시오.')==text
    assert original_quote(text,'네트워크 설정을 변경하십시오.') is None


def test_explicit_empty_filter_is_not_overridden_by_global_exclusion():
    c={'rows':[{'a':None},{'a':1}],'columns':['a'],'trace':[],'file_id':'a'}
    filter_sort_node(c,[{'field':'a','operator':'is_empty','value':None,'compare_field':None,'value_type':'text'}],[],missing_policy='exclude')
    assert c['rows']==[{'a':None}]


def test_backwards_data_binding_is_rejected_without_recursion():
    w={'entry':'read','nodes':[node('read','read_table'),node('group','aggregate_table',[bind('table','filter','table')],fields=['status']),node('filter','filter_sort',[bind('table','group','table')])],'edges':[edge('read','group'),edge('group','filter')]}
    assert workflow_errors(w,['read_table','aggregate_table','filter_sort'],[{'id':'a','kind':'table','columns':['status']}])
