"""Workflow execution jobs: run, resume, retry, run uploads and run lease control."""
from infrastructure.persistence.ids import uid, now
from workflow_engine.legacy_runtime import test_agent
from workflow_engine.runner import run_workflow
from workflow_engine.scheduling.schedules import Schedules
from agent_builder.conditions.input_requirements import preflight_execution, execution_request_required
from workflow_engine.scheduling.library_sources import LibrarySources,source_tags


def recover_runs(c):return c.lifecycle.recover()

def interrupt_run(c):return c.lifecycle.interrupt(c.data['id'],c.data.get('execution_token'))

def heartbeat_run(c):
    data=c.data
    result=c.lifecycle.heartbeat(data['id'],data['execution_token'])
    if data.get('lease_token') and result['status']=='renewed':Schedules(c.storage).heartbeat(data['id'],data['lease_token'])
    return result

def retry_run(c):
    data=c.data;run=c.storage.test(data['id'])
    result=run_workflow(c.storage,run['agent_id'],run['request'],c.api_key,run_id=run['id'],retry=True,execution_token=data.get('execution_token'))
    LibrarySources(c.storage).complete(result)
    return result

def run_upload(c):
    data=c.data;storage=c.storage
    storage.agent(data['id'])
    a={**data['asset'],'id':uid(),'created_at':now()};storage.add_asset(a,data['rows']);return {'asset':a}

def resume_run(c):
    data=c.data
    if not isinstance(data.get('content'),str) or not data['content'].strip():
        raise ValueError('확인 답변을 입력하세요.')
    run=c.storage.test(data['id'])
    result=run_workflow(c.storage,run['agent_id'],run['request'],c.api_key,run_id=run['id'],answer=data['content'],execution_token=data.get('execution_token'))
    LibrarySources(c.storage).complete(result)
    return result

def run(c):return {'run':c.storage.test(c.data['id'])}

def test(c):
    data=c.data;storage=c.storage;api_key=c.api_key
    agent=storage.agent(data['id'])
    if LibrarySources(storage).get(data['id']) and not hasattr(storage,'bound'):raise ValueError('자료함 파일 권한과 실행 스냅샷을 먼저 확인하세요.')
    if hasattr(storage,'bound') and data.get('file_ids'):raise ValueError('자료함 파일 선택과 이번 실행 파일 첨부를 동시에 사용할 수 없습니다.')
    request=data.get('content','')
    if not isinstance(request,str):
        raise ValueError('실행 요청은 텍스트로 입력하세요.')
    if not request.strip():
        if execution_request_required(agent['config'],agent.get('workflow_dependencies')):
            raise ValueError('이 워크플로우에 필요한 실행 요청을 입력하세요.')
        request=''
    if agent['config'].get('workflow'):
        source=Schedules(storage).execution_source(agent)
        files=data.get('file_ids') or ([source['asset_id']] if source else [])
        preflight_execution(agent['config'],storage,files,agent.get('workflow_dependencies'))
        tags={'trigger':'manual',**source_tags(storage)}
        if source and not data.get('file_ids'):tags.update(source_version=source['version'],source_id=source['id'])
        result=run_workflow(storage,data['id'],request,api_key,files,agent_snapshot=agent,reply_draft=data.get('reply_draft'),forced_run_id=data.get('run_id'),execution_token=data.get('execution_token'),run_tags=tags)
        LibrarySources(storage).complete(result)
        return result
    return test_agent(storage,data['id'],request,api_key,file_ids=data.get('file_ids'),agent_snapshot=agent)
