"""Compile stored node contracts to a real, checkpointed LangGraph execution."""
import json,re,sqlite3
from pathlib import Path
from typing import TypedDict
from pydantic import BaseModel,ConfigDict
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt,Command
from .model_factory import create_chat_model
from deepagents import create_deep_agent
from langchain_core.tools import tool as registered_tool
from langchain.agents.structured_output import ProviderStrategy
from langchain.agents.middleware import wrap_model_call
from .storage import uid,now
from .workflow import workflow_errors
from .review_state import collect_reviews,is_review_report_artifact
from .tools.filter_sort import filter_sort_node
from .tools.write_result import write_result_node

class RunState(TypedDict,total=False):
    outputs:dict
    steps:list
    request:str
    file_ids:list
    messages:list
    customer_message:str
    reply_draft:str
    external_inputs:dict

class Fact(BaseModel):
    model_config=ConfigDict(extra='forbid')
    field:str
    value:str|None
    source_id:str|None
    quote:str|None
class Citation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str
    quote:str
class GroundedAnswer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str
    citations:list[Citation]
class InputAssessment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    valid:bool
    clarification:str

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:bool
    reason:str

class SearchTerms(BaseModel):
    model_config=ConfigDict(extra='forbid')
    keywords:list[str]

class Facts(BaseModel):
    model_config=ConfigDict(extra='forbid')
    facts:list[Fact]

def original_quote(text,quote):
    if not quote or not quote.strip():return None
    if quote in text:return quote
    positions=[i for i,c in enumerate(text) if not c.isspace()]
    compact=''.join(text[i] for i in positions);needle=''.join(c for c in quote if not c.isspace())
    if not needle:return None
    start=compact.find(needle)
    if start<0:return None
    return text[positions[start]:positions[start+len(needle)-1]+1]

def document_chunks(storage,ids):
    chunks=[]
    for id in ids:
        asset=storage.asset(id)
        if asset['kind']!='document':raise ValueError('문서 읽기에는 PDF/TXT/MD 문서를 연결하세요.')
        pages=asset.get('pages') or [asset.get('text','')]
        for page,text in enumerate(pages,1):
            # Overlap retains context; offsets remain exact within the parsed page.
            for start in range(0,len(text),1400):
                chunk=text[start:start+1800]
                if chunk.strip():chunks.append({'source_id':f'{id}:{page}:{start}','file_id':id,'file_name':asset['name'],'page':page if asset['format']=='pdf' else None,'offset':start,'text':chunk})
    return chunks

def execute_node(node,state,storage,config,llm):
    from importlib import import_module
    from .registry import TOOLS
    item=next((t for t in TOOLS if t['id']==node['tool']),None)
    if not item:raise ValueError('등록되지 않은 노드입니다.')
    module,name=item['module_path'].split(':')
    return getattr(import_module(module),name)(node,state,storage,config,llm)


def validate_node_result(tool,result):
    """Reject broken public outputs before a downstream node can consume them."""
    if not isinstance(result,dict):raise ValueError('노드 결과는 포트별 객체여야 합니다.')
    mandatory={'agent_node':'text','saved_workflow':result.get('_selected_port'),'review_summary':'table','reply_review':'reviews','read_table':'table','filter_sort':'table','aggregate_table':'table','write_result':'artifact','read_document':'documents','search_document':'documents','extract_information':'records','compose_answer':'text','export_result':'artifact','condition':'decision','ask_user':'answer','finish':'text'}[tool]
    if mandatory not in result:raise ValueError('노드 필수 출력이 없습니다: '+mandatory)
    for port,value in result.items():
        if port=='table' and (not isinstance(value,dict) or not isinstance(value.get('rows'),list) or not isinstance(value.get('columns'),list) or any(not isinstance(row,dict) for row in value['rows'])):raise ValueError('표 출력 형식이 올바르지 않습니다.')
        if port in ['reviews','_reviews','_review_updates']:
            from .review_contracts import ReviewReport
            if not isinstance(value,list):raise ValueError('검수 출력은 보고서 목록이어야 합니다.')
            for review in value:
                ReviewReport.model_validate(review['report'])
                if 'candidate_reply' in review and (not isinstance(review['candidate_reply'],str) or not review['candidate_reply'].strip()):raise ValueError('승인 후보 응대문이 비어 있습니다.')
                revision=review.get('candidate_revision',0)
                if type(revision) is not int or revision<0:raise ValueError('승인 후보 버전이 올바르지 않습니다.')
        if port in ['documents','records'] and (not isinstance(value,list) or any(not isinstance(row,dict) for row in value)):raise ValueError('목록 출력 형식이 올바르지 않습니다.')
        if port in ['text','answer'] and (not isinstance(value,str) or not value.strip()):raise ValueError('최종 답변 또는 확인 응답이 비어 있습니다.')
        if port=='decision' and not isinstance(value,bool):raise ValueError('분기 결과는 true/false여야 합니다.')
        if port=='artifact' and (not isinstance(value,dict) or any(not isinstance(value.get(k),str) for k in ['name','content','mime'])):raise ValueError('결과 파일 출력 형식이 올바르지 않습니다.')

def port_summary(values):
    summaries={}
    for port,value in values.items():
        if port.startswith('_'):continue
        if port=='table':summaries[port]={'rows':len(value['rows']),'columns':value['columns']}
        elif isinstance(value,list):summaries[port]={'items':len(value)}
        elif isinstance(value,str):summaries[port]={'characters':len(value)}
        elif port=='artifact':summaries[port]={'name':value['name'],'bytes':len(value['content'].encode())}
        elif isinstance(value,bool):summaries[port]=value
    return summaries

def compile_workflow(storage,config,llm,executor=execute_node,ownership=None,inherit_checkpoint=False):
    from .reusable_workflows import reject_review_export
    reject_review_export(config,config.get('_saved_workflows',{}))
    workflow=config['workflow'];issues=workflow_errors(workflow,config['tool_ids'],[storage.asset(id) for id in config['document_ids']],config['execution_plan'])
    if issues:raise ValueError(' / '.join(issues))
    connection=None if inherit_checkpoint else sqlite3.connect(storage.path,check_same_thread=False,timeout=10)
    config={**config,'_ownership':ownership}
    graph=StateGraph(RunState)
    for node in workflow['nodes']:
        def run(state,n=node):
            from time import perf_counter
            if ownership:ownership()
            started=perf_counter()
            result=executor(n,state,storage,config,llm)
            if ownership:ownership()
            validate_node_result(n['tool'],result)
            step={'node':n['id'],'label':n['label'],'status':'completed','detail':'등록된 '+n['tool']+' 코드를 실행했습니다.','parameters':n['settings'],'bindings':n['bindings']}
            step['elapsed_ms']=round((perf_counter()-started)*1000,2)
            step['input_summary']=port_summary({b['input']:state['outputs'][b['node_id']][b['output']] for b in n['bindings']})
            step['output_summary']=port_summary(result)
            if n['tool']=='read_table':
                file_id=result['table'].get('file_id')
                asset=storage.asset(file_id) if file_id else None
                step['parameters']={'source_file_id':asset['id'],'file_name':asset['name'],'hash':asset.get('hash'),'row_count':len(result['table']['rows'])} if asset else {'input_source':'앞 노드 출력','row_count':len(result['table']['rows'])}
            if n['tool']=='filter_sort':step['parameters']=n['settings'].get('execution_plan') or config['execution_plan']
            if n['tool']=='write_result':step['parameters']={'format':n['settings'].get('format') or config['execution_plan']['output_format']}
            if n['tool']=='search_document':step['parameters']={**n['settings'],'effective_query':result['query'],'keywords':result['keywords']};step['detail']=f"원문 {result['matched_count']}개 구간을 검색했습니다."
            if n['tool']=='condition':step['detail']=('조건 충족 → yes' if result['decision'] else '조건 미충족 → no')+(' · '+result.get('reason','') if result.get('reason') else '')
            if n['tool']=='saved_workflow':step['child_steps']=result.get('_child_steps',[]);step['dependency']=result.get('_dependency')
            update={'outputs':{**state.get('outputs',{}),n['id']:result},'steps':state.get('steps',[])+[step]}
            if n['tool']=='ask_user' and not result.get('_already_answered'):
                update['request']=state['request']+'\n사용자 확인 답변: '+result['answer']
                update['messages']=state.get('messages',[])+[{'role':'user','content':result['answer']}]
            if n['tool'] in ['compose_answer','agent_node']:
                update['request']=state['request']+'\n에이전트 답변: '+result['text']
                update['messages']=state.get('messages',[])+[{'role':'assistant','content':result['text']}]
            return update
        graph.add_node(node['id'],run)
    graph.add_edge(START,workflow['entry'])
    for node in workflow['nodes']:
        edges=[e for e in workflow['edges'] if e['source']==node['id']]
        if node['tool']=='condition':
            graph.add_conditional_edges(node['id'],lambda s,id=node['id']:'yes' if s['outputs'][id]['decision'] else 'no',{e['branch']:e['target'] for e in edges})
        else:graph.add_edge(node['id'],edges[0]['target'] if edges else END)
    return graph.compile(checkpointer=True if inherit_checkpoint else SqliteSaver(connection)),connection

def run_workflow(storage,agent_id,request,api_key,file_ids=None,run_id=None,answer=None,executor=execute_node,llm=None,agent_snapshot=None,forced_run_id=None,run_tags=None,reply_draft=None,retry=False,execution_token=None):
    if run_id:
        run=storage.test(run_id)
        if run['agent_id']!=agent_id or run['status']!=('failed' if retry else 'needs_input'):raise ValueError('이어갈 수 있는 실행이 아닙니다.')
        run.update(_retry=retry,_previous_status=run['status'],_previous_error=run.get('error'),_previous_updated_at=run.get('updated_at'))
        config=run['config_snapshot'];model=run['model']
    else:
        agent=agent_snapshot or storage.agent(agent_id);config=agent['config'];model=agent['model']
        run={'workflow_dependencies':agent.get('workflow_dependencies',{}),'id':forced_run_id or uid(),'agent_id':agent_id,'agent_version':agent['version'],'model':model,'tool_versions':agent.get('tool_versions',{}),'credential_id':agent['credential_id'],'config_snapshot':config,'request':request,'reply_draft':reply_draft,'file_ids':file_ids or [],'status':'running','created_at':now(),'steps':[],'rows':[],'artifact':None,'answer':'','error':None}
        if run_tags:run.update(run_tags)
        if forced_run_id:
            try:
                existing=storage.test(forced_run_id)
                if existing['status'] in ['succeeded','failed']:return existing
                run=existing;config=run['config_snapshot'];model=run['model'];file_ids=run['file_ids']
            except ValueError:pass
    config={**config,'_saved_workflows':run.get('workflow_dependencies',{})}
    from .run_lifecycle import RunLifecycle
    lifecycle=RunLifecycle(storage);execution_token=execution_token or uid()
    lifecycle.start(run,execution_token)
    graph=None;connection=None
    cfg={'configurable':{'thread_id':'run:'+run['id']},'recursion_limit':40}
    try:
        if any(v!=1 for v in run.get('tool_versions',{}).values()):raise ValueError('저장된 도구 버전을 지원하지 않습니다.')
        from .reusable_workflows import requires_model
        needs_model=requires_model(config)
        runtime_model=llm or (create_chat_model(model=model,api_key=api_key,timeout=65,max_retries=1,max_tokens=5000) if needs_model else None)
        graph,connection=compile_workflow(storage,config,runtime_model,executor,ownership=lambda:lifecycle.assert_owner(run['id'],execution_token))
        resume_pending=bool(forced_run_id and graph.get_state(cfg).values)
        # Persist each checkpoint before the next node starts so a killed worker
        # can resume its input and completed steps without losing or replaying them.
        result=graph.invoke(None if retry else Command(resume=answer) if run_id else None if resume_pending else {'outputs':{},'steps':[],'request':request,'file_ids':file_ids or [],'messages':[{'role':'user','content':request}],'customer_message':request,'reply_draft':reply_draft},cfg,durability='sync')
        snapshot=graph.get_state(cfg);state=snapshot.values;outputs=state.get('outputs',{})
        questions=[i.value for t in snapshot.tasks for i in t.interrupts]
        artifacts=[v['artifact'] for v in outputs.values() if v.get('artifact')]
        tools_by_id={n['id']:n['tool'] for n in config['workflow']['nodes']}
        texts=list(dict.fromkeys(v['text'] for id,v in outputs.items() if isinstance(v.get('text'),str) and tools_by_id.get(id) in ['compose_answer','agent_node','finish','saved_workflow']))
        records=[v['records'] for v in outputs.values() if 'records' in v]
        tables=[v['table'] for v in outputs.values() if 'table' in v]
        result_rows=next((v['records'] if 'records' in v else v['table']['rows'] for v in reversed(list(outputs.values())) if 'records' in v or 'table' in v),[])
        evidence=[e for v in outputs.values() for e in v.get('evidence',[])]+[r['evidence'] for r in (records[-1] if records else []) if r.get('evidence')]
        run.update(status='needs_input' if questions else 'succeeded',steps=state.get('steps',[]),rows=result_rows,artifact=artifacts[-1] if artifacts else None,answer=((texts[-1]+'\n\n') if texts else '')+questions[0]['question'] if questions else '\n\n'.join(texts) if texts else '저장한 워크플로우의 실행이 완료되었습니다.',question=questions[0] if questions else None,messages=state.get('messages',[]),evidence=evidence,outputs=outputs,error=None)
    except Exception as error:
        from .runtime import public_error
        state=(graph.get_state(cfg).values or {}) if graph else {}
        pending=(graph.get_state(cfg).next or ['workflow'])[0] if graph else 'workflow'
        import traceback
        frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-6:]]
        failed_node=next((n for n in config['workflow']['nodes'] if n['id']==pending),None)
        run.update(outputs=state.get('outputs',{}),artifact=None,report_path=None,status='failed',error_type=type(error).__name__,diagnostic_frames=frames,error=public_error(error,api_key),steps=state.get('steps',[])+[{'node':pending,'label':failed_node['label'] if failed_node else pending,'status':'failed','detail':public_error(error,api_key),'parameters':(failed_node['settings'].get('execution_plan') or config['execution_plan']) if failed_node and failed_node['tool']=='filter_sort' else failed_node['settings'] if failed_node else {},'bindings':failed_node['bindings'] if failed_node else []}])
    finally:
        if connection:connection.close()
    lifecycle.assert_owner(run['id'],execution_token)
    reviews=collect_reviews(run.get('outputs',{}))
    from .reusable_workflows import contains_review
    if (reviews or contains_review(config)) and run.get('artifact') and run['status']=='succeeded' and not is_review_report_artifact(config,run.get('outputs',{}),run['artifact']):
        run.update(status='failed',artifact=None,error='검수 후보 응대는 승인 전에 자동 내보낼 수 없습니다. 검수 보고서 저장 또는 사람 승인을 사용하세요.')
    if run.get('artifact') and run['status']=='succeeded':
        import os
        reports=Path(storage.path).parent/'reports'
        suffix=run['artifact']['name'].rsplit('.',1)[-1]
        if suffix not in ['csv','json','md']:suffix='txt'
        target=reports/(run['id']+'.'+suffix);temp=reports/(run['id']+'.tmp')
        try:
            lifecycle.publish(run['id'],execution_token,target,run['artifact']['content']);run['report_path']='reports/'+target.name
        except OSError:run.update(status='failed',error='보고서를 서버에 저장하지 못했습니다. 저장 공간과 권한을 확인하세요.')
    if run['status']=='succeeded' and reviews:
        run['answer']='응대 검수가 완료되었습니다. 보고서를 확인하고 수정·승인 또는 거절하세요.'
        import os
        reports=Path(storage.path).parent/'reports'
        target=reports/(run['id']+'.review.json');temp=reports/(run['id']+'.review.tmp')
        try:
            lifecycle.publish(run['id'],execution_token,target,json.dumps({'id':run['id'],'agent_version':run['agent_version'],'source_version':run.get('source_version'),'reviews':reviews,'outputs':run['outputs']},ensure_ascii=False,indent=2))
            run['review_report_path']='reports/'+target.name
        except OSError:run.update(status='failed',error='검수 보고서를 저장하지 못했습니다. 저장 공간과 권한을 확인하세요.')
    lifecycle.finish(run,execution_token);return storage.test(run['id'])
