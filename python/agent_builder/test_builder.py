import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.storage import Storage,uid
from agent_builder.graph import build_graph
from agent_builder.nodes.validation import validate_node
from agent_builder.nodes.save import make_save_node
from agent_builder.tools.filter_sort import filter_sort_node
from agent_builder.tools.write_result import write_result_node
from agent_builder.tools.read_table import read_table_node
from agent_builder.worker import handle
from langgraph.types import Command

PROMPT='stock이 reorder_level보다 작은 행을 숫자 오름차순으로 CSV 저장'
def draft(questions=None):
    return {'name':'재고 정리','purpose':'재고 부족 행 정리','instructions':PROMPT,'tool_ids':['read_table','filter_sort','write_result'],'document_ids':[],'requirements':[{'description':'재고 부족과 정렬','source_id':'user','quote':PROMPT}],'questions':questions or [],'unsupported':[],'explanation':'설정을 정리했습니다.','execution_plan':{'filters':[],'sort':[],'match':'all','missing_policy':'ask','duplicate_policy':'keep','duplicate_field':None,'output_format':'csv'}}
def initial():return {'session_id':uid(),'messages':[{'role':'user','content':PROMPT}],'resources':[],'draft':None,'trace':[],'confirmed':False,'saved_agent_id':None,'base_agent_id':None,'base_version':0,'model':'gpt-4.1-mini','credential_id':uid()}
def fake_conversation(state):return {'draft':draft(['중복 상품을 어떻게 처리할까요?'] if len([m for m in state['messages'] if m['role']=='user'])==1 else []),'confirmed':False,'saved_agent_id':None}

def test_question_resume_restart_approval_and_snapshot(tmp_path):
    store=Storage(tmp_path);state=initial();cfg={'configurable':{'thread_id':state['session_id']}}
    graph,conn=build_graph(store,conversation=fake_conversation)
    first=graph.invoke(state,cfg)
    assert first['status']=='needs_clarification';assert first['__interrupt__'][0].value['kind']=='clarification';assert not store.agents()
    conn.close();store.close()
    store=Storage(tmp_path);graph,conn=build_graph(store,conversation=fake_conversation)
    second=graph.invoke(Command(resume={'type':'message','content':'중복 상품은 유지'}),cfg)
    assert second['status']=='ready';assert second['__interrupt__'][0].value['kind']=='review'
    assert second['messages'][0]['content']==PROMPT;assert second['messages'][1]['content']=='중복 상품은 유지';assert not store.agents()
    saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    assert saved['status']=='saved';assert len(store.agents())==1
    assert store.agent(saved['saved_agent_id'])['config']['instructions']==PROMPT
    assert saved['model']=='gpt-4.1-mini'
    conn.close();store.close()

def test_save_requires_human_confirmation(tmp_path):
    store=Storage(tmp_path);state={**initial(),'draft':draft(),'status':'ready'}
    with pytest.raises(ValueError):make_save_node(store)(state)
    assert not store.agents();store.close()

def test_unsupported_route_does_not_save(tmp_path):
    store=Storage(tmp_path);state=initial()
    def conversation(s):return {'draft':{**draft(),'unsupported':['이메일 전송 도구가 없습니다.']}}
    graph,conn=build_graph(store,conversation=conversation);result=graph.invoke(state,{'configurable':{'thread_id':state['session_id']}})
    assert result['status']=='unsupported';assert not result.get('__interrupt__');assert not store.agents();conn.close();store.close()

@pytest.mark.parametrize('change',[{'document_ids':[uid()]},{'requirements':[{'description':'가짜 근거','source_id':'user','quote':'말하지 않은 문장'}]},{'tool_ids':[]},{'instructions':''},{'tool_ids':['read_table','read_table']}])
def test_invalid_settings_fail_validation(change):
    result=validate_node({**initial(),'draft':{**draft(),**change}})
    assert result['status']=='needs_clarification';assert result['issues']

def test_exact_document_citation_required():
    id=uid();state={**initial(),'resources':[{'id':id,'text':'중복 상품은 유지합니다.','columns':[]}],'draft':{**draft(),'document_ids':[id],'requirements':[{'description':'중복 유지','source_id':id,'quote':'중복 상품은 유지합니다.'}]}}
    assert validate_node(state)['status']=='ready'
    state['draft']['requirements'][0]['quote']='중복 상품은 삭제합니다.'
    assert validate_node(state)['issues']

def test_versions_are_immutable_and_stale_update_rejected(tmp_path):
    store=Storage(tmp_path);state={**initial(),'draft':draft(),'confirmed':True};id=store.save_agent(state)
    assert store.save_agent(state)==id
    modified={**state,'session_id':uid(),'base_agent_id':id,'base_version':1,'draft':{**draft(),'name':'변경한 이름'}}
    assert store.save_agent(modified)==id;assert store.agent(id)['version']==2
    old=json.loads(store.db.execute('SELECT json FROM agent_versions WHERE agent_id=? AND version=1',(id,)).fetchone()[0]);assert old['config']['name']=='재고 정리'
    with pytest.raises(ValueError):store.save_agent({**modified,'session_id':uid(),'draft':{**draft(),'name':'충돌'}})
    store.close()

def test_worker_revision_prevents_duplicate_message(tmp_path):
    session=handle({'directory':str(tmp_path),'action':'new_session','model':'gpt-4.1-mini','credential_id':uid()})
    data={'directory':str(tmp_path),'id':session['id'],'revision':0,'action':'message','content':PROMPT}
    first=handle(data,conversation=fake_conversation)
    assert first['revision']==1
    with pytest.raises(ValueError):handle(data,conversation=fake_conversation)
    reopened=handle({'directory':str(tmp_path),'action':'session','id':session['id']})
    assert reopened['status']=='needs_clarification';assert reopened['messages']==first['messages']

def test_tools_numeric_sort_column_comparison_missing_duplicates():
    rows=[{'product':'A','stock':'20','level':30},{'product':'B','stock':'3','level':10},{'product':'C','stock':5,'level':10},{'product':'D','stock':None,'level':10},{'product':'B','stock':1,'level':10}]
    context={'rows':rows,'columns':['product','stock','level'],'file_id':'source','trace':[]}
    filters=[{'field':'stock','operator':'lt','compare_field':'level','value_type':'number'}]
    sort=[{'field':'stock','direction':'ascending','value_type':'number'}]
    with pytest.raises(ValueError):filter_sort_node(context,filters,sort)
    result=filter_sort_node(context,filters,sort,missing_policy='exclude',duplicate_policy='last',duplicate_field='product')
    assert [r['stock'] for r in context['rows']]==[1,5,'20'];assert result['row_count']==3
    write_result_node(context,'csv');assert 'product,stock,level' in context['artifact']['content'];assert context['trace'][0]['parameters']['missing_policy']=='exclude'

@pytest.mark.parametrize('bad',[{'field':'unknown','operator':'eq','value':1},{'field':'stock','operator':'eval','value':'x'}])
def test_tools_reject_invalid_filters(bad):
    with pytest.raises(ValueError):filter_sort_node({'rows':[{'stock':1}],'columns':['stock'],'trace':[]},[bad],[])

def test_unconnected_data_cannot_be_read(tmp_path):
    store=Storage(tmp_path)
    with pytest.raises(ValueError):read_table_node({'file_ids':[],'storage':store},uid())
    store.close()

def test_empty_result_still_writes_header_and_no_fake_rows():
    context={'rows':[],'columns':['a','b'],'trace':[]};write_result_node(context,'csv')
    assert context['artifact']['content']=='\ufeffa,b\r\n'

def test_spreadsheet_formula_neutralized_without_modifying_original():
    context={'rows':[{'name':'=HYPERLINK("bad")'}],'columns':['name'],'trace':[]};write_result_node(context,'csv')
    assert "'=HYPERLINK" in context['artifact']['content'];assert context['rows'][0]['name'].startswith('=')


def test_tool_sqlite_reads_from_langgraph_worker_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Storage(tmp_path);id=uid();store.add_asset({'id':id,'name':'a.csv','kind':'table','columns':['n'],'profile':{}},[{'n':2}])
    ctx={'storage':store,'file_ids':[id],'trace':[]}
    with ThreadPoolExecutor(max_workers=1) as pool:result=pool.submit(read_table_node,ctx,id).result()
    assert result['row_count']==1;assert ctx['rows']==[{'n':2}];store.close()

def test_edit_session_preserves_prior_user_conditions_and_citations(tmp_path):
    directory=str(tmp_path);s=handle({'directory':directory,'action':'new_session','model':'gpt-4.1-mini','credential_id':uid()})
    s=handle({'directory':directory,'action':'message','id':s['id'],'revision':0,'content':PROMPT},conversation=fake_conversation)
    s=handle({'directory':directory,'action':'message','id':s['id'],'revision':s['revision'],'content':'중복은 유지'},conversation=fake_conversation)
    s=handle({'directory':directory,'action':'confirm','id':s['id'],'revision':s['revision']},conversation=fake_conversation)
    edit=handle({'directory':directory,'action':'new_session','model':'gpt-4.1-mini','credential_id':s['credential_id'],'agent_id':s['saved_agent_id']})
    result=handle({'directory':directory,'action':'message','id':edit['id'],'revision':0,'content':'이름만 수정'},conversation=fake_conversation)
    assert result['status']=='ready';assert result['messages'][0]['content']==PROMPT;assert result['draft']['requirements'][0]['quote']==PROMPT


def test_new_resource_invalidates_previously_ready_draft(tmp_path):
    directory=str(tmp_path);s=handle({'directory':directory,'action':'new_session','model':'gpt-4.1-mini','credential_id':uid()})
    def complete(state):return {'draft':draft(),'confirmed':False}
    s=handle({'directory':directory,'action':'message','id':s['id'],'revision':0,'content':PROMPT},conversation=complete)
    assert s['status']=='ready'
    s=handle({'directory':directory,'action':'upload','id':s['id'],'revision':s['revision'],'asset':{'name':'policy.txt','kind':'document','text':'原본 유지','columns':[],'profile':{}},'rows':[]})
    assert s['status']=='needs_clarification'
    with pytest.raises(ValueError):handle({'directory':directory,'action':'confirm','id':s['id'],'revision':s['revision']},conversation=complete)
