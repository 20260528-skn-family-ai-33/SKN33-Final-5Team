"""Compile stored node contracts to a real, checkpointed LangGraph execution."""
import json
import base64
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from infrastructure.persistence.checkpoints import checkpoint_connection, checkpointer
from langgraph.types import Command
from infrastructure.llm.model_factory import create_chat_model


from infrastructure.persistence.ids import uid, now
from workflow_engine.workflow import workflow_errors
from verification.reply_review.state import collect_reviews, is_review_report_artifact
from workflow_engine.executors.dispatch import execute_node
from workflow_engine.catalog.registry import uses_operation
from workflow_engine.run_answer import public_run_answer
from documents.resources.document_resources import saved_input


class RunState(TypedDict,total=False):
    outputs:dict
    steps:list
    request:str
    file_ids:list
    messages:list
    customer_message:str
    reply_draft:str
    external_inputs:dict

def validate_node_result(tool,result,node=None):
    """Reject broken public outputs before a downstream node can consume them."""
    if not isinstance(result,dict):raise ValueError('노드 결과는 포트별 객체여야 합니다.')
    from workflow_engine.catalog.registry import uses_operation, node_ports
    operation = bool(node and uses_operation(node))
    mandatory='result' if operation else {'agent_node':'text','saved_workflow':result.get('_selected_port'),'review_summary':'table','reply_review':'reviews','read_table':'table','filter_sort':'table','aggregate_table':'table','calculate_columns':'table','text_transform':'text','base64_codec':'text','text_hash':'text','date_transform':'text','text_statistics':'records','write_result':'artifact','read_document':'documents','search_document':'documents','extract_information':'records','compose_answer':'text','export_result':'artifact','condition':'decision','ask_user':'answer','finish':'text'}[tool]
    if operation:
        import json
        json.dumps(result, allow_nan=False)
        missing=set(node_ports(node)[1])-set(result)
        if missing: raise ValueError('노드 출력이 없습니다: '+', '.join(sorted(missing)))
    if mandatory not in result:raise ValueError('노드 필수 출력이 없습니다: '+mandatory)
    for port,value in result.items():
        if port=='table' and (not isinstance(value,dict) or not isinstance(value.get('rows'),list) or not isinstance(value.get('columns'),list) or any(not isinstance(row,dict) for row in value['rows'])):raise ValueError('표 출력 형식이 올바르지 않습니다.')
        if port in ['reviews','_reviews','_review_updates']:
            from verification.reply_review.contracts import ReviewReport
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
        elif port=='artifact':summaries[port]={'name':value['name'],'bytes':len(base64.b64decode(value['content'])) if value.get('encoding')=='base64' else len(value['content'].encode())}
        elif isinstance(value,bool):summaries[port]=value
    return summaries

def execution_parameters(node,config):
    if not node:return {}
    if not uses_operation(node):
        if node['tool']=='filter_sort':return node['settings'].get('execution_plan') or config['execution_plan']
        if node['tool']=='write_result':return {'format':node['settings'].get('format') or config['execution_plan']['output_format']}
    return node['settings']


def compile_workflow(storage,config,llm,executor=execute_node,ownership=None,inherit_checkpoint=False):
    from workflow_engine.library.reusable_workflows import reject_review_export
    reject_review_export(config,config.get('_saved_workflows',{}))
    workflow=config['workflow'];issues=workflow_errors(workflow,config['tool_ids'],[saved_input(storage,id) for id in config['document_ids']],config['execution_plan'],runtime_resources=[storage.asset(id) for id in config.get('_runtime_file_ids',[])])
    if issues:raise ValueError(' / '.join(issues))
    connection=None if inherit_checkpoint else checkpoint_connection(storage)
    config={**config,'_ownership':ownership}
    graph=StateGraph(RunState)
    for node in workflow['nodes']:
        def run(state,n=node):
            from time import perf_counter
            if ownership:ownership()
            started=perf_counter();started_at=now()
            result=executor(n,state,storage,config,llm)
            if ownership:ownership()
            validate_node_result(n['tool'],result,n)
            step={'node':n['id'],'label':n['label'],'status':'completed','detail':'등록된 '+n['tool']+' 코드를 실행했습니다.','parameters':execution_parameters(n,config),'bindings':n['bindings']}
            step['elapsed_ms']=round((perf_counter()-started)*1000,2)
            step.update(started_at=started_at,finished_at=now())
            step['input_summary']=port_summary({b['input']:state['outputs'][b['node_id']][b['output']] for b in n['bindings']})
            step['output_summary']=port_summary(result)
            if n['tool']=='read_table' and not n['settings'].get('operation'):
                file_id=result['table'].get('file_id')
                asset=storage.asset(file_id) if file_id else None
                step['parameters']={'source_file_id':asset['id'],'file_name':asset['name'],'hash':asset.get('hash'),'row_count':len(result['table']['rows'])} if asset else {'input_source':'앞 노드 출력','row_count':len(result['table']['rows'])}
            if n['tool']=='search_document' and not n['settings'].get('operation'):step['parameters']={**n['settings'],'effective_query':result['query'],'keywords':result['keywords']};step['detail']=f"원문 {result['matched_count']}개 구간을 검색했습니다."
            if n['tool']=='condition':step['detail']=('조건 충족 → yes' if result['decision'] else '조건 미충족 → no')+(' · '+result.get('reason','') if result.get('reason') else '')
            if n['tool']=='saved_workflow':step['child_steps']=result.get('_child_steps',[]);step['dependency']=result.get('_dependency')
            update={'outputs':{**state.get('outputs',{}),n['id']:result},'steps':state.get('steps',[])+[step]}
            if n['tool']=='ask_user' and not result.get('_already_answered'):
                update['request']=state['request']+'\n사용자 확인 답변: '+result['answer']
                update['messages']=state.get('messages',[])+[{'role':'user','content':result['answer']}]
            if n['tool'] in ['compose_answer','agent_node'] and 'text' in result:
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
    return graph.compile(checkpointer=True if inherit_checkpoint else checkpointer(connection)),connection

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
    config={**config,'_saved_workflows':run.get('workflow_dependencies',{}),'_runtime_file_ids':run.get('file_ids',[])}
    from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
    lifecycle=RunLifecycle(storage);execution_token=execution_token or uid()
    lifecycle.start(run,execution_token)
    graph=None;connection=None
    cfg={'configurable':{'thread_id':'run:'+run['id']},'recursion_limit':40}
    try:
        if any(v!=1 for v in run.get('tool_versions',{}).values()):raise ValueError('저장된 도구 버전을 지원하지 않습니다.')
        from workflow_engine.library.reusable_workflows import requires_model
        needs_model=requires_model(config)
        runtime_model=llm or (create_chat_model(model=model,api_key=api_key,timeout=65,max_retries=1,max_tokens=5000) if needs_model else None)
        graph,connection=compile_workflow(storage,config,runtime_model,executor,ownership=lambda:lifecycle.assert_owner(run['id'],execution_token))
        resume_pending=bool(forced_run_id and graph.get_state(cfg).values)
        # Persist each checkpoint before the next node starts so a killed worker
        # can resume its input and completed steps without losing or replaying them.
        request=run['request']  # A reclaimed start uses the input persisted by its original owner.
        # Run Now can use the saved schedule instructions, separately from real customer input.
        task_prompt=request or (config.get('instructions','') if config.get('schedule') else '')
        result=graph.invoke(None if retry else Command(resume=answer) if run_id else None if resume_pending else {'outputs':{},'steps':[],'request':task_prompt,'file_ids':run['file_ids'],'messages':[{'role':'user','content':request}] if request else [],'customer_message':request,'reply_draft':run.get('reply_draft')},cfg,durability='sync')
        snapshot=graph.get_state(cfg);state=snapshot.values;outputs=state.get('outputs',{})
        questions=[i.value for t in snapshot.tasks for i in t.interrupts]
        from workflow_engine.execution_path import artifact_output_ids
        artifacts=[outputs[id]['artifact'] for id in artifact_output_ids(config['workflow'],outputs)]
        tools_by_id={n['id']:n['tool'] for n in config['workflow']['nodes']}
        transformed_sources={b['node_id'] for n in config['workflow']['nodes'] if n['tool'] in ('text_transform','base64_codec','text_hash','date_transform') and n['id'] in outputs for b in n['bindings'] if b['input']=='text' and b['output']=='text'}
        gmail_collections={n['id'] for n in config['workflow']['nodes'] if n['tool']=='email.list' and n['settings'].get('operation')=='read_messages'}
        summarized_sources={b['node_id'] for n in config['workflow']['nodes'] if n['tool']=='compose_answer' and n['id'] in outputs
                            for b in n['bindings'] if b['input']=='documents' and b['output']=='documents' and b['node_id'] in gmail_collections}
        operation_nodes={n['id'] for n in config['workflow']['nodes'] if uses_operation(n)}
        texts=list(dict.fromkeys(v['text'] for id,v in outputs.items() if isinstance(v.get('text'),str) and id not in transformed_sources|summarized_sources and (tools_by_id.get(id) in ['compose_answer','agent_node','finish','saved_workflow','text_transform','base64_codec','text_hash','date_transform'] or id in operation_nodes)))
        records=[v['records'] for v in outputs.values() if 'records' in v]
        tables=[v['table'] for v in outputs.values() if 'table' in v]
        result_rows=next((v['records'] if 'records' in v else v['table']['rows'] for v in reversed(list(outputs.values())) if 'records' in v or 'table' in v),[])
        evidence=[e for v in outputs.values() for e in v.get('evidence',[])]+[r['evidence'] for r in (records[-1] if records else []) if r.get('evidence')]
        run.update(status='needs_input' if questions else 'succeeded',steps=state.get('steps',[]),rows=result_rows,artifact=artifacts[-1] if artifacts else None,answer=((texts[-1]+'\n\n') if texts else '')+questions[0]['question'] if questions else '\n\n'.join(texts) if texts else '저장한 워크플로우의 실행이 완료되었습니다.',question=questions[0] if questions else None,messages=state.get('messages',[]),evidence=evidence,outputs=outputs,error=None)
        run['answer'] = public_run_answer(run['answer'], outputs)
    except Exception as error:
        from infrastructure.llm.errors import public_error
        state=(graph.get_state(cfg).values or {}) if graph else {}
        pending=(graph.get_state(cfg).next or ['workflow'])[0] if graph else 'workflow'
        import traceback
        frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-6:]]
        failed_node=next((n for n in config['workflow']['nodes'] if n['id']==pending),None)
        run.update(outputs=state.get('outputs',{}),artifact=None,report_path=None,status='failed',error_type=type(error).__name__,diagnostic_frames=frames,error=public_error(error,api_key),steps=state.get('steps',[])+[{'node':pending,'label':failed_node['label'] if failed_node else pending,'status':'failed','detail':public_error(error,api_key),'parameters':execution_parameters(failed_node,config),'bindings':failed_node['bindings'] if failed_node else []}])
    finally:
        if connection:connection.close()
    lifecycle.assert_owner(run['id'],execution_token)
    reviews=collect_reviews(run.get('outputs',{}))
    from workflow_engine.library.reusable_workflows import contains_review
    if (reviews or contains_review(config)) and run.get('artifact') and run['status']=='succeeded' and not is_review_report_artifact(config,run.get('outputs',{}),run['artifact']):
        run.update(status='failed',artifact=None,error='검수 후보 응대는 승인 전에 자동 내보낼 수 없습니다. 검수 보고서 저장 또는 사람 승인을 사용하세요.')
    if run.get('artifact') and run['status']=='succeeded':

        reports=Path(storage.path).parent/'reports'
        suffix=run['artifact']['name'].rsplit('.',1)[-1]
        if suffix not in ['csv','json','md','txt','docx','xlsx','pdf','png','zip','html','eml']:suffix='bin'
        target=reports/(run['id']+'.'+suffix);temp=reports/(run['id']+'.tmp')
        try:
            lifecycle.publish(run['id'],execution_token,target,
                base64.b64decode(run['artifact']['content'],validate=True) if run['artifact'].get('encoding')=='base64' else run['artifact']['content']);run['report_path']='reports/'+target.name
        except OSError:run.update(status='failed',error='보고서를 서버에 저장하지 못했습니다. 저장 공간과 권한을 확인하세요.')
    if run['status']=='succeeded' and reviews:
        run['answer']='응대 검수가 완료되었습니다. 보고서를 확인하고 수정·승인 또는 거절하세요.'

        reports=Path(storage.path).parent/'reports'
        target=reports/(run['id']+'.review.json');temp=reports/(run['id']+'.review.tmp')
        try:
            lifecycle.publish(run['id'],execution_token,target,json.dumps({'id':run['id'],'agent_version':run['agent_version'],'source_version':run.get('source_version'),'reviews':reviews,'outputs':run['outputs']},ensure_ascii=False,indent=2))
            run['review_report_path']='reports/'+target.name
        except OSError:run.update(status='failed',error='검수 보고서를 저장하지 못했습니다. 저장 공간과 권한을 확인하세요.')
    lifecycle.finish(run,execution_token);return storage.test(run['id'])
