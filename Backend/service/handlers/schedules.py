"""Schedule jobs and managed data sources."""
from infrastructure.persistence.ids import uid, now
from workflow_engine.runner import run_workflow
from workflow_engine.scheduling.schedules import Schedules


def claim_due(c):return {'jobs':Schedules(c.storage).claim_due()}

def schedule(c):return {'schedule':Schedules(c.storage).activate(c.data['id'],c.data['active'])}

def schedule_test(c):return {'job':Schedules(c.storage).enqueue_test(c.data['id'])}

def schedule_cancel(c):return {'job':Schedules(c.storage).cancel_test(c.data['id'],c.data['job_id'])}

def fail_job(c):
    Schedules(c.storage).finish(c.data['id'],c.data['lease_token'],'failed',error='실행 환경·연결 계정을 확인하세요.');return {'status':'failed'}

def scheduled_run(c):
    data=c.data;storage=c.storage
    schedules=Schedules(storage);job=schedules.job(data['id'],data['lease_token']);source=schedules.source(job['agent_id'])
    if not source:
        schedules.finish(job['id'],data['lease_token'],'failed',error='관리 데이터 소스가 없습니다.');return {'status':'failed'}
    run=run_workflow(storage,job['agent_id'],'예약 조건에 따라 관리 데이터 소스를 처리하고 보고서를 저장하세요.',c.api_key,file_ids=[source['asset_id']],agent_snapshot=job['agent_snapshot'],forced_run_id=job['id'],execution_token=data.get('execution_token'),run_tags={'trigger':job['kind'],'scheduled_for':job['due_at'],'source_id':source['id'],'source_version':source['version'],'source_asset_id':source['asset_id']})
    schedules.finish(job['id'],data['lease_token'],run['status'],run_id=run['id'],error=run.get('error'));return run

def source_upload(c):
    data=c.data;storage=c.storage
    storage.agent(data['id']);a={**data['asset'],'id':uid(),'created_at':now()}
    if a['kind']!='table':raise ValueError('관리 데이터 소스는 표 파일을 연결하세요.')
    storage.add_asset(a,data['rows']);return {'source':Schedules(storage).replace_source(data['id'],a['id'])}
