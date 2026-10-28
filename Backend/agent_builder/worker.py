"""JSON over stdin/stdout bridge. Secrets are supplied only in process memory."""
import json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_builder.storage import Storage,uid,now
from agent_builder.graph import build_graph
from agent_builder.runtime import test_agent,public_error
from agent_builder.registry import TOOLS
from agent_builder.scheduling import Schedules
from agent_builder.workflow_runtime import run_workflow
from agent_builder.input_requirements import design_requirements, execution_requirements, preflight_execution
from agent_builder.tool_gap import describe_gap, export_gap
from agent_builder.tool_gap_choice import present_choice, choose, redesign_context
from agent_builder import tool_gap_jobs, tool_gap_redesign
from langgraph.types import Command

def public_state(storage,s,graph):
    snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
    state=snapshot.values or {}
    if not state and s.get('agent_id'):
        base=storage.agent(s['agent_id']);old=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {})
        state={'draft':base['config'],'messages':old.get('messages',[]),'status':'editing'}
    from agent_builder.capability_gate import blocking_issue,capability_enforced
    issue=blocking_issue(state) if state.get('status')=='ready' else None
    return {**s,'messages':state.get('messages',[]),'draft':state.get('draft'),'status':'validation_failed' if issue else state.get('status','empty'),'issues':list(dict.fromkeys(state.get('issues',[])+([issue] if issue else []))),'trace':state.get('trace',[])[-40:],'verification':state.get('verification'),'saved_agent_id':state.get('saved_agent_id'),'resources':[storage.asset(id) for id in s['file_ids']],
            'condition_ledger':state.get('condition_ledger'),'pending_questions':state.get('pending_questions',[]),
            'input_requirements':design_requirements(state) if state.get('condition_enforced') else [],
            'tool_gap':(export_gap(state,s) if describe_gap(state,s['id']) else None),
            'tool_gap_choice':present_choice(state,s), 'redesign_context':redesign_context(state,s),
            'tool_gap_job':tool_gap_jobs.current_job(storage,state,s),
            'capability_assessment':state.get('capability_assessment'),
            'capability_enforced':capability_enforced(state),
            'capability_deferred':state.get('capability_deferred',False),
            'redesign_proposal':tool_gap_redesign.view(state,s),
            'redesign_generation':tool_gap_redesign.generation_handoff(state,s)}

def handle(data,conversation=None):
    storage=Storage(data['directory']);action=data['action'];api_key=data.get('api_key','');connection=None
    try:
        from agent_builder.run_lifecycle import RunLifecycle
        lifecycle=RunLifecycle(storage)
        if action=='recover_runs':return lifecycle.recover()
        if action=='interrupt_run':return lifecycle.interrupt(data['id'],data.get('execution_token'))
        if action=='heartbeat_run':
            result=lifecycle.heartbeat(data['id'],data['execution_token'])
            if data.get('lease_token') and result['status']=='renewed':Schedules(storage).heartbeat(data['id'],data['lease_token'])
            return result
        if action=='nodes':
            from agent_builder.node_library import list_nodes,default_nodes
            return {'nodes':list_nodes(storage),'defaults':default_nodes()}
        if action=='save_node':
            from agent_builder.node_library import save
            return {'node':save(storage,data['spec'],data.get('id'),data.get('base_version',0))}
        if action=='catalog':return {'tools':TOOLS,'engine':{'conversation':'deepagents','routing':'langgraph','storage':'sqlite'}}
        if action=='review_decision':
            from agent_builder.review_decisions import decide
            return decide(storage,data['id'],data['review_id'],data['decision'],data.get('edited_reply',''))
        if action=='approved_replies':
            from agent_builder.review_decisions import approved_csv
            return approved_csv(storage,data['id'])
        if action=='retry_run':
            run=storage.test(data['id'])
            return run_workflow(storage,run['agent_id'],run['request'],api_key,run_id=run['id'],retry=True,execution_token=data.get('execution_token'))
        if action=='asset':return {'asset':storage.asset(data['id'])}
        if action=='document_review':
            from agent_builder.document_review import view,save
            return save(storage,data['id'],data['command']) if 'command' in data else view(storage,data['id'])
        if action=='agents':return {'agents':storage.agents()}
        if action=='agent':
            agent=storage.agent(data['id']);schedules=Schedules(storage)
            source=schedules.source(data['id'])
            return {'agent':agent,'tests':storage.tests(data['id']),'resources':[storage.asset(id) for id in agent['config']['document_ids']],'schedule':schedules.get(data['id']),'source':source,'jobs':schedules.jobs(data['id']),
                    'input_requirements':execution_requirements(agent['config'],storage,[source['asset_id']] if source else [],agent.get('workflow_dependencies'))}
        if action=='claim_due':return {'jobs':Schedules(storage).claim_due()}
        if action=='schedule':return {'schedule':Schedules(storage).activate(data['id'],data['active'])}
        if action=='schedule_test':return {'job':Schedules(storage).enqueue_test(data['id'])}
        if action=='schedule_cancel':return {'job':Schedules(storage).cancel_test(data['id'],data['job_id'])}
        if action=='fail_job':Schedules(storage).finish(data['id'],data['lease_token'],'failed',error='실행 환경·연결 계정을 확인하세요.');return {'status':'failed'}
        if action=='scheduled_run':
            schedules=Schedules(storage);job=schedules.job(data['id'],data['lease_token']);source=schedules.source(job['agent_id'])
            if not source:
                schedules.finish(job['id'],data['lease_token'],'failed',error='관리 데이터 소스가 없습니다.');return {'status':'failed'}
            run=run_workflow(storage,job['agent_id'],'예약 조건에 따라 관리 데이터 소스를 처리하고 보고서를 저장하세요.',api_key,file_ids=[source['asset_id']],agent_snapshot=job['agent_snapshot'],forced_run_id=job['id'],execution_token=data.get('execution_token'),run_tags={'trigger':job['kind'],'scheduled_for':job['due_at'],'source_id':source['id'],'source_version':source['version'],'source_asset_id':source['asset_id']})
            schedules.finish(job['id'],data['lease_token'],run['status'],run_id=run['id'],error=run.get('error'));return run
        if action=='source_upload':
            storage.agent(data['id']);a={**data['asset'],'id':uid(),'created_at':now()}
            if a['kind']!='table':raise ValueError('관리 데이터 소스는 표 파일을 연결하세요.')
            storage.add_asset(a,data['rows']);return {'source':Schedules(storage).replace_source(data['id'],a['id'])}

        if action=='new_session':return storage.new_session(data['model'],data['credential_id'],data.get('agent_id'),data.get('node_refs'))
        if action=='run_upload':
            storage.agent(data['id'])
            a={**data['asset'],'id':uid(),'created_at':now()};storage.add_asset(a,data['rows']);return {'asset':a}
        if action=='resume_run':
            run=storage.test(data['id'])
            return run_workflow(storage,run['agent_id'],run['request'],api_key,run_id=run['id'],answer=data['content'],execution_token=data.get('execution_token'))
        if action=='run':return {'run':storage.test(data['id'])}
        if action=='test':
            agent=storage.agent(data['id'])
            if agent['config'].get('workflow'):
                source=Schedules(storage).source(data['id']);files=data.get('file_ids') or ([source['asset_id']] if source else [])
                preflight_execution(agent['config'],storage,files,agent.get('workflow_dependencies'))
                return run_workflow(storage,data['id'],data['content'],api_key,files,reply_draft=data.get('reply_draft'),forced_run_id=data.get('run_id'),execution_token=data.get('execution_token'),run_tags={'trigger':'manual','source_version':source['version'],'source_id':source['id']} if source and not data.get('file_ids') else {'trigger':'manual'})
            return test_agent(storage,data['id'],data['content'],api_key)
        s=storage.session(data['id']);graph,connection=build_graph(storage,api_key,s['model'],conversation=conversation if conversation else (lambda state:{}) if action in ['session','upload','tool_gap','tool_gap_choice','tool_gap_job_claim','tool_gap_job_finish','tool_gap_job_recover','tool_gap_replan_propose','tool_gap_replan_decide'] else None)
        if action in {'message','confirm'}:
            snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
            fresh=[storage.asset(id) for id in s['file_ids']]
            versions=lambda resources:[(r['id'],str(r.get('version') or r.get('hash') or '1')) for r in resources]
            if snapshot.values and versions(snapshot.values.get('resources',[]))!=versions(fresh):
                if action=='confirm':raise ValueError('자료 검토 결과가 변경됐습니다. 업무 설명을 다시 보내 설정과 근거를 확인하세요.')
                graph.update_state({'configurable':{'thread_id':s['id']}},{'resources':fresh,'confirmed':False,'verification':None,'verification_last_candidate':None})
        if action=='session':return public_state(storage,s,graph)
        if action in ('tool_gap_replan_propose','tool_gap_replan_decide'):
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            s=(tool_gap_redesign.propose if action=='tool_gap_replan_propose' else tool_gap_redesign.decide)(storage,state,s,data)
            return public_state(storage,s,graph)
        if action=='tool_gap_job_recover':return tool_gap_jobs.recover(storage,s)
        if action in ('tool_gap_job_claim','tool_gap_job_finish'):
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            if action=='tool_gap_job_claim':
                if type(data.get('revision')) is not int or data['revision']!=s['revision']:
                    raise ValueError('대화 버전이 변경됐습니다. 제작 선택을 다시 확인하세요.')
                return tool_gap_jobs.claim(storage,state,s,data.get('input_sha256'))
            return {'job':tool_gap_jobs.finish(storage,state,s,data.get('job_id'),data.get('result'),data.get('lease_token'))}
        if action=='tool_gap_choice':
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            s=choose(storage,state,s,data)
            return public_state(storage,s,graph)
        if action=='tool_gap':
            if isinstance(data.get('revision'),bool) or not isinstance(data.get('revision'),int) or s['revision']!=data['revision']:
                raise ValueError('대화 버전이 변경됐습니다. 현재 미지원 요청을 다시 확인하세요.')
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            return {'tool_gap':export_gap(state,s)}
        if action=='upload':
            if s['revision']!=data['revision']:raise ValueError('대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
            if len(s['file_ids'])>=10:raise ValueError('자료는 최대 10개 연결할 수 있습니다.')
            if graph.get_state({'configurable':{'thread_id':s['id']}}).values.get('status')=='saved':raise ValueError('저장된 에이전트의 자료를 바꾸려면 설정 수정 대화를 시작하세요.')
            a={**data['asset'],'id':uid(),'created_at':now()};storage.add_asset(a,data['rows']);s['file_ids'].append(a['id']);s=storage.update_session(s,data['revision'])
            snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
            if snapshot.values:graph.update_state({'configurable':{'thread_id':s['id']}},{'resources':[storage.asset(id) for id in s['file_ids']],'status':'needs_clarification','issues':['자료 연결이 변경됐습니다. 업무 설명을 보내 새 자료와 설정을 확인하세요.']})
            return public_state(storage,s,graph)
        if action not in ['message','confirm']:raise ValueError('지원하지 않는 요청입니다.')
        if s['revision']!=data['revision']:raise ValueError('다른 요청에서 대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
        config={'configurable':{'thread_id':s['id']},'recursion_limit':30}
        before=graph.get_state(config);state=before.values or {};has_interrupt=any(task.interrupts for task in before.tasks)
        if action=='confirm':
            if state.get('status')=='saved':return public_state(storage,s,graph)
            from agent_builder.capability_gate import require_ready
            require_ready(state)
            if not has_interrupt or state.get('status')!='ready':raise ValueError('필수 조건과 근거 검증을 완료한 뒤 저장할 수 있습니다.')
            graph.invoke(Command(resume={'type':'confirm'}),config)
        else:
            content=data['content'].strip()
            if not content or len(content)>4000:raise ValueError('업무 설명은 1~4,000자로 입력하세요.')
            if state.get('status')=='saved':raise ValueError('저장한 설정을 바꾸려면 설정 수정 대화를 시작하세요.')
            if has_interrupt:
                if data.get('question_id') and data['question_id'] not in [q['id'] for q in state.get('pending_questions',[])]:
                    raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
                graph.update_state(config,{'repair_count':0})
                graph.invoke(Command(resume={'type':'message','content':content,**({'question_id':data['question_id']} if data.get('question_id') else {})}),config)
            elif before.next:
                # A provider failure can leave conversation pending. Retry its exact input.
                from agent_builder.capability_gate import blocking_issue
                same=bool(state.get('messages',[]) and state['messages'][-1]['content']==content)
                if same and not blocking_issue(state):graph.invoke(None,config)
                else:
                    graph.update_state(config,{'messages':state.get('messages',[]) if same else state.get('messages',[])+[{'role':'user','content':content}],
                                               'confirmed':False},as_node='resources')
                    graph.invoke(None,config)
            else:
                base=storage.agent(s['agent_id']) if s.get('agent_id') else None
                base_messages=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {}).get('messages',[]) if base else []
                initial={**state,'session_id':s['id'],'messages':(state.get('messages',[]) or base_messages)+[{'role':'user','content':content}],'resources':[storage.asset(id) for id in s['file_ids']],'draft':state.get('draft') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'trace':state.get('trace',[]),'repair_count':0,'confirmed':False,'saved_agent_id':None,'verification_last_candidate':state.get('verification_last_candidate') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'base_agent_id':s.get('agent_id'),'base_version':s['base_version'],'model':s['model'],'credential_id':s['credential_id'],'node_library':s.get('node_library',[]),'required_node_refs':s.get('required_node_refs',[])}
                graph.invoke(initial,config)
        after=graph.get_state(config).values
        if after.get('saved_agent_id'):
            s['agent_id']=after['saved_agent_id'];s['base_version']=storage.agent(s['agent_id'])['version']
        s=storage.update_session(s,data['revision']);return public_state(storage,s,graph)
    finally:
        if connection:connection.close()
        storage.close()

if __name__=='__main__':
    data={}
    # Node exchanges UTF-8 JSON. Windows pipes otherwise use the locale code page (e.g. cp949).
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    try:
        data=json.load(sys.stdin)
        if not isinstance(data,dict):raise ValueError('요청 형식을 확인하세요.')
        # Never enable remote tracing for API credentials or private documents.
        import os
        os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
        result=handle(data)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False))
    except Exception as error:
        import traceback,os
        if os.environ.get('A4B_DIAGNOSTICS'):
            Path(__import__('tempfile').gettempdir(),'a4b-worker-diagnostic.json').write_text(json.dumps({'type':type(error).__name__,'code':getattr(error,'code',None),'param':getattr(error,'param',None),'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-6:]]}))
        print(json.dumps({'ok':False,'error':public_error(error,data.get('api_key',''))},ensure_ascii=False))
