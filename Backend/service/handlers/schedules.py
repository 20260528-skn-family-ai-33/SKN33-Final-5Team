"""Schedule jobs and managed data sources."""
from infrastructure.persistence.ids import uid, now
from workflow_engine.scheduling.schedules import Schedules, preflight_schedule, scheduled_agent
from workflow_engine.scheduling.library_sources import source_tags


def claim_due(c):return {'jobs':Schedules(c.storage).claim_due()}

def schedule(c):return {'schedule':Schedules(c.storage).activate(c.data['id'],c.data['active'])}

def schedule_configure(c):
    manager=Schedules(c.storage)
    c.storage.begin_write()
    try:return {'schedule':manager.configure(c.data['id'],spec=c.data['spec'])}
    except Exception:c.storage.db.rollback();raise

def schedule_remove(c):return {'schedule':Schedules(c.storage).remove(c.data['id'])}

def schedule_test(c):return {'job':Schedules(c.storage).enqueue_test(c.data['id'])}

def schedule_cancel(c):return {'job':Schedules(c.storage).cancel_test(c.data['id'],c.data['job_id'])}

def fail_job(c):
    try:run=c.storage.test(c.data['id'])
    except ValueError:run=None
    if run and run['status']=='succeeded':
        Schedules(c.storage).finish(c.data['id'],c.data['lease_token'],'succeeded',run_id=run['id'],source_run=run);return run
    Schedules(c.storage).finish(c.data['id'],c.data['lease_token'],'failed',error='실행 환경·연결 계정을 확인하세요.');return {'status':'failed'}

def scheduled_run(c):
    from workflow_engine.runner import run_workflow
    data=c.data;storage=c.storage
    schedules=Schedules(storage);job=schedules.job(data['id'],data['lease_token'])
    snapshot=job['agent_snapshot']
    if snapshot['config'].get('schedule'):snapshot=scheduled_agent(snapshot,snapshot['config']['schedule'])
    agent=storage.bind_agent(snapshot) if hasattr(storage,'bound') else snapshot
    if job.get('library_source') and not hasattr(storage,'bound'):raise ValueError('자료함 실행 파일의 권한 확인과 스냅샷이 필요합니다.')
    source=schedules.execution_source(agent)
    files=[source['asset_id']] if source else []
    try:preflight_schedule(storage,agent,files)
    except ValueError as error:
        schedules.finish(job['id'],data['lease_token'],'failed',error=str(error));return {'status':'failed','error':str(error)}
    tags={'trigger':job['kind'],'scheduled_for':job['due_at'],**source_tags(storage)}
    if source:tags.update(source_id=source['id'],source_version=source['version'],source_asset_id=source['asset_id'])
    try:previous=storage.test(job['id'])
    except ValueError:previous=None
    if previous and previous['status']=='failed' and job.get('library_source'):
        run=run_workflow(storage,job['agent_id'],previous['request'],c.api_key,run_id=job['id'],retry=True,execution_token=data.get('execution_token'))
    else:run=run_workflow(storage,job['agent_id'],'',c.api_key,file_ids=files,agent_snapshot=agent,forced_run_id=job['id'],execution_token=data.get('execution_token'),run_tags=tags)
    schedules.finish(job['id'],data['lease_token'],run['status'],run_id=run['id'],error=run.get('error'),source_run=run);return run

def source_upload(c):
    data=c.data;storage=c.storage
    storage.agent(data['id']);a={**data['asset'],'id':uid(),'created_at':now()}
    if a['kind'] not in ('table','document'):raise ValueError('관리 데이터 소스는 표 파일이나 문서를 연결하세요.')
    schedules=Schedules(storage)
    storage.begin_write()
    storage.add_asset(a,data['rows'],commit=False)
    return {'source':schedules.replace_source(data['id'],a['id'])}
