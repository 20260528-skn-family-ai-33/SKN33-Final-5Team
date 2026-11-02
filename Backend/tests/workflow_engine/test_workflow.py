import sys,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pytest
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage; from infrastructure.persistence.ids import uid
from workflow_engine.workflow import workflow_errors
from workflow_engine.runner import run_workflow, execute_node; from workflow_engine.executors.schemas import Facts, Fact
from tests.design.test_builder import draft, initial
from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node

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
    from workflow_engine.executors.schemas import Decision, InputAssessment
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
    import workflow_engine.executors.schemas as module
    from workflow_engine.executors import agent_factory
    class Business:
        def invoke(self,*args,**kwargs):return {'structured_response':module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])}
    def create(**kwargs):
        assert kwargs['tools'][0].name=='inspect_evidence'
        assert kwargs['tools'][0].invoke({})[0]['quote']=='실제 원문'
        return Business()
    monkeypatch.setattr(agent_factory,'create_deep_agent',create)
    class Review:
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,*args):return module.GroundedAnswer(text='자료 안내',citations=[module.Citation(source_id='doc:1:0',quote='실제 원문')])
    n=node('answer','compose_answer',[bind('documents','read','documents')])
    result=execute_node(n,{'request':'질문','outputs':{'read':{'documents':[{'source_id':'doc:1:0','file_id':'doc','file_name':'a.pdf','page':1,'offset':0,'text':'실제 원문'}]}}},None,{'instructions':'근거 답변'},Review())
    assert result['text']=='자료 안내';assert result['evidence'][0]['quote']=='실제 원문'


def test_original_quote_normalizes_layout_only():
    from documents.resources.quotes import original_quote
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


def test_receipt_style_mistakes_explain_the_wrong_link_and_the_document_path():
    # The draft Qwen produced for "영수증 이미지를 엑셀로 정리": documents wired to a table-only
    # tool and an edge to a finish node that does not exist.
    w={'entry':'read','nodes':[node('read','read_document'),node('save','write_result',[bind('table','read','table')],format='csv')],
       'edges':[edge('read','save'),edge('save','finish')]}
    issues=workflow_errors(w,['read_document','write_result'],[])
    missing=next(s for s in issues if s.startswith('존재하지 않는 노드에 연결했습니다'))
    assert 'save → finish' in missing and '없는 노드: finish' in missing
    port=next(s for s in issues if s.startswith('입출력 포트가 존재하지 않습니다: save'))
    assert 'read(read_document)의 출력은 documents' in port and 'table 자료가 필요' in port
    assert 'extract_information(records) → export_result' in port
    extracted={'entry':'read','nodes':[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['상품명','금액']),
               node('save','write_result',[bind('table','extract','records')],format='csv')],'edges':[edge('read','extract'),edge('extract','save')]}
    tools3=['read_document','extract_information','write_result']
    assert any(s.startswith('입출력 자료형이 맞지 않습니다: save') and 'write_result 대신 export_result(records)' in s for s in workflow_errors(extracted,tools3,[]))
    extracted['nodes'][2]['bindings']=[bind('table','extract','table')]
    assert any(s.startswith('입출력 포트가 존재하지 않습니다: save') and 'write_result 대신 export_result(records)' in s for s in workflow_errors(extracted,tools3,[]))
    w['nodes'][1]['bindings']=[bind('rows','read','documents')]
    assert any('write_result에는 rows 입력이 없습니다. 받을 수 있는 입력: table' in s for s in workflow_errors(w,['read_document','write_result'],[]))


def test_node_port_edge_references_resolve_only_to_a_real_node_port():
    """Real Deep Agent draft: edges 'read_table_1:table' → 'filter_sort_1:table'."""
    from workflow_engine.workflow import normalize_connections
    nodes=[node('read_table_1','read_table',file_ids=['f']),node('filter_sort_1','filter_sort',[bind('table','read_table_1','table')]),
           node('write_result_1','write_result',[bind('table','filter_sort_1','table')],format='json')]
    wf={'entry':'read_table_1','nodes':copy.deepcopy(nodes),
        'edges':[edge('read_table_1:table','filter_sort_1:table'),edge('filter_sort_1:table','write_result_1:table')]}
    assert workflow_errors(wf,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge('read_table_1','filter_sort_1'),edge('filter_sort_1','write_result_1')]
    assert not workflow_errors(fixed,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    # Unknown node, a port the node does not own, or no port: left for validation to reject.
    # (write_result has no binding here, so no single binding chain can replace the edges.)
    unchained=copy.deepcopy(nodes);unchained[2]['bindings']=[]
    for source in ('missing_1:table','read_table_1:artifact','read_table_1:','start'):
        kept=normalize_connections({'entry':'read_table_1','nodes':copy.deepcopy(unchained),'edges':[edge(source,'filter_sort_1')]})
        assert kept['edges'][0]['source']==source


def test_dotted_node_port_references_resolve_like_colon_ones():
    """Real Deep Agent draft (repro run 6): edges 'read_table_1.table' → 'filter_sort_1.table'."""
    from workflow_engine.workflow import normalize_connections
    nodes=[node('read_table_1','read_table',file_ids=['f']),node('filter_sort_1','filter_sort',[bind('table','read_table_1','table')]),
           node('write_result_1','write_result',[bind('table','filter_sort_1','table')],format='json')]
    wf={'entry':'read_table_1','nodes':copy.deepcopy(nodes),
        'edges':[edge('read_table_1.table','filter_sort_1.table'),edge('filter_sort_1.table','write_result_1.table')]}
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge('read_table_1','filter_sort_1'),edge('filter_sort_1','write_result_1')]
    assert not workflow_errors(fixed,['read_table','filter_sort','write_result'],[{'id':'f','kind':'table','columns':['거래ID','비용']}])
    unchained=copy.deepcopy(nodes);unchained[2]['bindings']=[]
    for source in ('missing_1.table','read_table_1.artifact','read_table_1.','read_table_1.table.x'):
        kept=normalize_connections({'entry':'read_table_1','nodes':copy.deepcopy(unchained),'edges':[edge(source,'filter_sort_1')]})
        assert kept['edges'][0]['source']==source


def _drafted(nodes,edges,entry):
    """Real post-fix drafts (live repro after G1-G4): correct bindings, broken edges."""
    names=[n for n,_ in nodes];chain=[]
    for i,(id,tool) in enumerate(nodes):
        extra={'file_ids':['f']} if tool=='read_table' else {'format':'json'} if tool=='write_result' else {}
        port={'filter_sort':'table','write_result':'table','finish':'artifact'}.get(tool)
        src_out={'read_table':'table','filter_sort':'table','write_result':'artifact'}
        bindings=[bind(port,names[i-1],src_out[nodes[i-1][1]])] if i else []
        chain.append(node(id,tool,bindings,**extra))
    return {'entry':entry,'nodes':chain,'edges':[edge(a,b) for a,b in edges]}


@pytest.mark.parametrize('ids,edges,entry,duplicate',[
    (['read_table_1','filter_sort_1','write_result_1','finish_1'],[('start','read_table_1'),('read_table_1','filter_sort_1'),('filter_sort_1','write_result_')],'read_table_1',False),
    (['read_table_node','filter_sort_node','write_result_node','finish_node'],[('read_table_node','filter_sort_node'),('filter_sort_node','write_result_node')],'read_table_node',False),
    (['read_table_node_1','filter_sort_node_2','write_result_node_3','finish_node_4'],[('read_table_node_1','filter_sort_node_2'),('filter_sort_node_0','write_result_node_3')],'read_table_node_1',True),
    (['read_table_1','filter_sort_1','write_result_1','finish_1'],[('read_table_1','filter_sort_1'),('filter_sort_1','write_result_1'),('write_result_1','finish_1')],'read_table_1',True)])
def test_unusable_edges_follow_an_unbranched_binding_chain(ids,edges,entry,duplicate):
    from workflow_engine.workflow import normalize_connections
    tools=['read_table','filter_sort','write_result','finish']
    wf=_drafted(list(zip(ids,tools)),edges,entry)
    if duplicate:wf['nodes'][3]['bindings']*=2  # exact repeat seen in the draft
    tool_ids=tools
    assert workflow_errors(wf,tool_ids,[{'id':'f','kind':'table','columns':['비용']}])
    fixed=normalize_connections(copy.deepcopy(wf))
    assert fixed['edges']==[edge(a,b) for a,b in zip(ids,ids[1:])] and fixed['entry']==ids[0]
    assert len(fixed['nodes'][3]['bindings'])==1
    assert not workflow_errors(fixed,tool_ids,[{'id':'f','kind':'table','columns':['비용']}])


def test_usable_branched_or_ambiguous_workflows_keep_their_edges():
    from workflow_engine.workflow import normalize_connections
    ok=_drafted([('r','read_table'),('s','filter_sort'),('w','write_result')],[('r','s'),('s','w')],'r')
    assert normalize_connections(copy.deepcopy(ok))==ok
    branched=copy.deepcopy(ok);branched['nodes'].append(node('c','condition',[bind('table','s','table')],condition_operator='empty'))
    branched['edges']=[edge('r','s'),edge('s','zzz')]
    assert normalize_connections(copy.deepcopy(branched))['edges']==branched['edges']
    fanout=copy.deepcopy(ok);fanout['nodes'].append(node('w2','write_result',[bind('table','s','table')],format='csv'))
    fanout['edges']=[edge('r','s'),edge('s','missing')]
    assert normalize_connections(copy.deepcopy(fanout))['edges']==fanout['edges']
