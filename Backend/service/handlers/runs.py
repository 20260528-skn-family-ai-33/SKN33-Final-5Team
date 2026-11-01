"""Workflow execution jobs: run, resume, retry, run uploads and run lease control."""
from infrastructure.persistence.ids import uid, now
from workflow_engine.legacy_runtime import test_agent
from workflow_engine.runner import run_workflow
from workflow_engine.scheduling.schedules import Schedules
from agent_builder.conditions.input_requirements import preflight_execution


def recover_runs(c):return c.lifecycle.recover()

def interrupt_run(c):return c.lifecycle.interrupt(c.data['id'],c.data.get('execution_token'))

def heartbeat_run(c):
    data=c.data
    result=c.lifecycle.heartbeat(data['id'],data['execution_token'])
    if data.get('lease_token') and result['status']=='renewed':Schedules(c.storage).heartbeat(data['id'],data['lease_token'])
    return result

def retry_run(c):
    data=c.data;run=c.storage.test(data['id'])
    return run_workflow(c.storage,run['agent_id'],run['request'],c.api_key,run_id=run['id'],retry=True,execution_token=data.get('execution_token'))

def run_upload(c):
    data=c.data;storage=c.storage
    storage.agent(data['id'])
    a={**data['asset'],'id':uid(),'created_at':now()};storage.add_asset(a,data['rows']);return {'asset':a}

def resume_run(c):
    data=c.data;run=c.storage.test(data['id'])
    return run_workflow(c.storage,run['agent_id'],run['request'],c.api_key,run_id=run['id'],answer=data['content'],execution_token=data.get('execution_token'))

def run(c):return {'run':c.storage.test(c.data['id'])}

def test(c):
    data=c.data;storage=c.storage;api_key=c.api_key
    agent=storage.agent(data['id'])
    if agent['config'].get('workflow'):
        source=Schedules(storage).source(data['id']);files=data.get('file_ids') or ([source['asset_id']] if source else [])
        preflight_execution(agent['config'],storage,files,agent.get('workflow_dependencies'))
        return run_workflow(storage,data['id'],data['content'],api_key,files,reply_draft=data.get('reply_draft'),forced_run_id=data.get('run_id'),execution_token=data.get('execution_token'),run_tags={'trigger':'manual','source_version':source['version'],'source_id':source['id']} if source and not data.get('file_ids') else {'trigger':'manual'})
    return test_agent(storage,data['id'],data['content'],api_key)
