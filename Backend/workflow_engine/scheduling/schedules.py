"""Durable local-server schedules, managed sources and leased jobs."""
import json
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from infrastructure.persistence.ids import uid, now
from documents.resources.document_resources import available_inputs
from workflow_engine.scheduling.recurrence import next_occurrence,schedule_errors
from workflow_engine.scheduling.library_sources import LibrarySources


def required_connections(config,dependencies=None):
    providers=set()
    for node in (config.get('workflow') or {}).get('nodes',[]):
        if node['tool'].startswith('email.'):
            providers.add('gmail')
        elif node['tool'].startswith('calendar.'):
            providers.add('google_calendar')
        elif node['tool']=='slack.post_message':
            providers.add('slack')
        elif node['tool']=='saved_workflow':
            child=(dependencies or {}).get(node['id'])
            if child:providers.update(required_connections(child['config'],child.get('workflow_dependencies')))
    return sorted(providers)


def source_required(config,storage,file_ids=None,dependencies=None):
    """Whether execution is waiting for a file, using the runner's input contracts."""
    from agent_builder.conditions.input_requirements import execution_requirements
    return any(r['required_before_start'] and not r['ready']
               for r in execution_requirements(config,storage,file_ids,dependencies))


def preflight_schedule(storage,agent,file_ids=None):
    from agent_builder.conditions.input_requirements import preflight_execution,execution_request_required
    def inspect(config,dependencies):
        if any(n['tool']=='ask_user' for n in (config.get('workflow') or {}).get('nodes',[])):
            raise ValueError('예약 실행에는 사용자 응답 대기 노드를 연결할 수 없습니다.')
        for child in (dependencies or {}).values():
            inspect(child['config'],child.get('workflow_dependencies'))
    config=agent['config'];dependencies=agent.get('workflow_dependencies')
    inspect(config,dependencies)
    if execution_request_required(config,dependencies):
        raise ValueError('예약 실행에 필요한 사용자 입력을 고정 설정이나 앞 단계 출력으로 연결하세요.')
    binding=LibrarySources(storage).get(agent['id'])
    if binding and not getattr(storage,'library_target_id',None):raise ValueError('자료함 권한과 실행 파일을 서버에서 다시 확인해야 합니다.')
    if binding and not getattr(storage,'snapshot',{}).get('assets') and binding['mode']=='new':
        from agent_builder.conditions.input_requirements import execution_requirements
        requirements=execution_requirements(config,storage,file_ids,dependencies)
        issues=[issue for item in requirements if item['node_id']!=binding['target_node_id'] and item['required_before_start'] for issue in item['issues']]
        if issues:raise ValueError('실행 전 자료 확인: '+' / '.join(issues))
        return requirements
    return preflight_execution(config,storage,file_ids,dependencies)


def scheduled_agent(agent,spec):
    """Bind execution-only reservation settings without editing the saved workflow."""
    result=deepcopy(agent)
    from workflow_engine.scheduling.policy import fixed_schedule
    result['config'].update(schedule=fixed_schedule(deepcopy(spec)),report_destination='reports')
    return result

class Schedules:
    def __init__(self,storage):
        self.storage=storage;self.db=storage.db
        storage.ensure_schema('''
        CREATE TABLE IF NOT EXISTS builder_data_sources(agent_id TEXT PRIMARY KEY,id TEXT NOT NULL,asset_id TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS builder_schedules(agent_id TEXT PRIMARY KEY,json TEXT NOT NULL,next_at TEXT,active INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS builder_schedule_jobs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,slot_key TEXT NOT NULL,state TEXT NOT NULL,lease_until TEXT,lease_token TEXT,json TEXT NOT NULL,UNIQUE(agent_id,slot_key));
        ''');self.db.commit()
        # Adapt legacy reservations once, before entering any execution/save transaction.
        from workflow_engine.scheduling.policy import WORKFLOW_TIMEZONE, fixed_schedule
        rows=self.db.execute('SELECT agent_id,json FROM builder_schedules WHERE {ws}').fetchall()
        if any(not json.loads(body).get('removed') and json.loads(body).get('spec',{}).get('timezone')!=WORKFLOW_TIMEZONE for _,body in rows):
            self.storage.begin_write()
            try:
                for agent_id,body in self.db.execute('SELECT agent_id,json FROM builder_schedules WHERE {ws}').fetchall():
                    reservation=json.loads(body)
                    if reservation.get('removed'):continue
                    if reservation.get('spec',{}).get('timezone')==WORKFLOW_TIMEZONE:continue
                    reservation['spec']=fixed_schedule(reservation['spec'])
                    next_at=next_occurrence(reservation['spec'])
                    self.db.execute('UPDATE builder_schedules SET json=?,next_at=? WHERE {ws} AND agent_id=?',
                                    (json.dumps(reservation,ensure_ascii=False),next_at,agent_id))
                self.db.commit()
            except Exception:self.db.rollback();raise
    def source(self,agent_id):
        row=self.db.execute('SELECT id,asset_id,version,updated_at FROM builder_data_sources WHERE {ws} AND agent_id=?',(agent_id,)).fetchone()
        if not row:return None
        return {'id':row[0],'asset_id':row[1],'version':row[2],'updated_at':row[3],'asset':self.storage.asset(row[1])}
    def execution_source(self,agent):
        if hasattr(self.storage,'bound') or LibrarySources(self.storage).get(agent['id']):return None
        from agent_builder.conditions.input_requirements import execution_requirements
        requirements=execution_requirements(agent['config'],self.storage,None,agent.get('workflow_dependencies'))
        if not requirements:return None
        source=self.source(agent['id'])
        if not source:return None
        slots=[r for r in requirements if r['kind']==source['asset']['kind']]
        # One managed file may replace one reader slot, never a set of saved
        # documents or several independent inputs of the same type.
        return source if len(slots)==1 and len(slots[0].get('file_ids',[]))<=1 else None
    def replace_source(self,agent_id,asset_id,commit=True):
        agent=self.storage.agent(agent_id);asset=self.storage.asset(asset_id)
        if asset['kind'] not in ('table','document'):raise ValueError('관리 데이터 소스는 표 파일이나 문서를 연결하세요.')
        from contracts.models import AgentDraft
        from agent_builder.design.nodes.validation import validate_node
        config=AgentDraft.model_validate(agent['config']).model_dump()
        # Check the incoming source against the saved conditions, not the old sample.
        retained=[a['id'] for a in available_inputs(self.storage,config['document_ids']) if a['kind']!=asset['kind']]
        same_kind=[a['id'] for a in available_inputs(self.storage,config['document_ids']) if a['kind']==asset['kind']]
        if asset['kind']=='document' and len(same_kind)>1:
            raise ValueError('여러 문서는 워크플로우의 자료 설정에서 각각 연결하세요. 저장한 문서들로 예약 실행할 수 있습니다.')
        config['document_ids']=[asset_id]+retained
        if config.get('workflow'):
            for n in config['workflow']['nodes']:
                if n['tool']==('read_table' if asset['kind']=='table' else 'read_document'):n['settings']['file_ids']=[asset_id]
        result=validate_node({'draft':config,'resources':[asset]+[self.storage.asset(id) for id in retained],'messages':[{'role':'user','content':r['quote']} for r in config['requirements'] if r['source_id']=='user']})
        # Source replacement doesn't revalidate old document citations against a new file.
        relevant=[issue for issue in result['issues'] if not issue.startswith('업무 조건의 인용 원문')]
        if relevant:raise ValueError('새 데이터 소스의 구조를 확인하세요: '+' / '.join(relevant))
        old=self.source(agent_id);id=old['id'] if old else uid();version=(old['version'] if old else 0)+1
        self.db.execute('INSERT INTO builder_data_sources({wsc}agent_id,id,asset_id,version,updated_at) VALUES({wsv}?,?,?,?,?) ON CONFLICT({wsc}agent_id) DO UPDATE SET asset_id=excluded.asset_id,version=excluded.version,updated_at=excluded.updated_at',(agent_id,id,asset_id,version,now()));
        if commit:self.db.commit()
        return self.source(agent_id)
    def configure(self,agent_id,commit=True,previous_config=None,spec=None):
        agent=self.storage.agent(agent_id);spec=spec if spec is not None else agent['config'].get('schedule')
        if not spec:return None
        from contracts.models import ScheduleSpec
        spec=ScheduleSpec.model_validate(spec,strict=True).model_dump()
        issues=schedule_errors(spec)
        if issues:raise ValueError(' / '.join(issues))
        from agent_builder.conditions.input_requirements import execution_requirements
        requirements=execution_requirements(agent['config'],self.storage,None,agent.get('workflow_dependencies'))
        old_source=self.source(agent_id)
        source=self.execution_source(agent)
        changed_slot=False
        if source and previous_config is not None:
            kind=source['asset']['kind']
            previous=execution_requirements(previous_config,self.storage,None,agent.get('workflow_dependencies'))
            slots=lambda items:[(r['node_id'],r['kind'],r.get('file_ids',[])) for r in items if r['kind']==kind]
            changed_slot=slots(previous)!=slots(requirements)
        if old_source and (not source or changed_slot):
            # Configuration changes detach obsolete mappings only. An uploaded
            # replacement's ID may differ from its original slot and is valid
            # while that saved slot remains unchanged.
            self.db.execute('DELETE FROM builder_data_sources WHERE {ws} AND agent_id=?',(agent_id,))
        data={'agent_id':agent_id,'agent_version':agent['version'],'spec':spec,'updated_at':now()}
        self.db.execute('INSERT INTO builder_schedules({wsc}agent_id,json,next_at,active) VALUES({wsv}?,?,?,0) ON CONFLICT({wsc}agent_id) DO UPDATE SET json=excluded.json,next_at=excluded.next_at,active=0',(agent_id,json.dumps(data,ensure_ascii=False),next_occurrence(spec)))
        if requirements and not self.source(agent_id):
            tables=[id for id in agent['config']['document_ids'] if self.storage.asset(id)['kind']=='table']
            if len(tables)==1:self.replace_source(agent_id,tables[0],commit=False)
            elif not tables:
                documents=[id for id in agent['config']['document_ids'] if self.storage.asset(id)['kind']=='document']
                if len(documents)==1:self.replace_source(agent_id,documents[0],commit=False)
        if commit:self.db.commit()
        return self.get(agent_id)
    def workflow_changed(self,agent_id,previous_config):
        """Pause until the user applies the new version; discard obsolete file mappings."""
        agent=self.storage.agent(agent_id);old_source=self.source(agent_id)
        source=self.execution_source(agent)
        changed_slot=False
        if source:
            from agent_builder.conditions.input_requirements import execution_requirements
            kind=source['asset']['kind']
            slots=lambda config:[(r['node_id'],r['kind'],r.get('file_ids',[])) for r in execution_requirements(config,self.storage,None,agent.get('workflow_dependencies')) if r['kind']==kind]
            changed_slot=slots(previous_config)!=slots(agent['config'])
        if old_source and (not source or changed_slot):
            self.db.execute('DELETE FROM builder_data_sources WHERE {ws} AND agent_id=?',(agent_id,))
        self.db.execute('UPDATE builder_schedules SET active=0 WHERE {ws} AND agent_id=?',(agent_id,))
    def remove(self,agent_id):
        """Remove the reservation and queued automatic jobs, preserving workflow versions."""
        self.storage.agent(agent_id)
        self.storage.begin_write()
        try:
            marker={'agent_id':agent_id,'removed':True,'removed_at':now()}
            self.db.execute('INSERT INTO builder_schedules({wsc}agent_id,json,next_at,active) VALUES({wsv}?,?,NULL,0) ON CONFLICT({wsc}agent_id) DO UPDATE SET json=excluded.json,next_at=NULL,active=0',
                            (agent_id,json.dumps(marker,ensure_ascii=False)))
            self.db.execute('DELETE FROM builder_data_sources WHERE {ws} AND agent_id=?',(agent_id,))
            for job_id,body in self.db.execute("SELECT id,json FROM builder_schedule_jobs WHERE {ws} AND agent_id=? AND state='queued'",(agent_id,)).fetchall():
                job=json.loads(body)
                if job.get('kind') not in ('scheduled','schedule_test'):continue
                job.update(error='예약이 해제되어 대기 중인 자동 실행을 취소했습니다.',finished_at=now())
                self.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',json=? WHERE {ws} AND id=?",(json.dumps(job,ensure_ascii=False),job_id))
                self.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND job_id=? AND state='pending'",(now(),job_id))
            self.db.commit()
            return None
        except Exception:self.db.rollback();raise
    def get(self,agent_id):
        row=self.db.execute('SELECT json,next_at,active FROM builder_schedules WHERE {ws} AND agent_id=?',(agent_id,)).fetchone()
        if not row:return None
        data=json.loads(row[0])
        if data.get('removed'):return None
        return {**data,'next_run_at':row[1],'active':bool(row[2])}
    def was_removed(self,agent_id):
        row=self.db.execute('SELECT json FROM builder_schedules WHERE {ws} AND agent_id=?',(agent_id,)).fetchone()
        return bool(row and json.loads(row[0]).get('removed'))
    def advance_version(self,agent_id,previous_version,version):
        """Move a schedule to a name-only revision without changing its next slot."""
        row=self.db.execute('SELECT json FROM builder_schedules WHERE {ws} AND agent_id=?',(agent_id,)).fetchone()
        if row:
            data=json.loads(row[0])
            if data.get('removed'):return
            if data['agent_version']==previous_version:
                data['agent_version']=version
                self.db.execute('UPDATE builder_schedules SET json=? WHERE {ws} AND agent_id=?',
                                (json.dumps(data,ensure_ascii=False),agent_id))
    def activate(self,agent_id,active):
        self.storage.begin_write()
        try:return self._activate(agent_id,active)
        except Exception:self.db.rollback();raise
    def _activate(self,agent_id,active):
        schedule=self.get(agent_id)
        if not schedule:raise ValueError('예약 설정이 없습니다. 대화로 시간을 정하고 저장하세요.')
        if active:
            agent=self.storage.agent(agent_id)
            if agent['version']!=schedule['agent_version']:raise ValueError('워크플로우가 변경됐습니다. 예약 설정을 저장해 적용할 버전을 확인하세요.')
            agent=scheduled_agent(agent,schedule['spec']);source=self.execution_source(agent)
            preflight_schedule(self.storage,agent,[source['asset_id']] if source else [])
        self.db.execute('UPDATE builder_schedules SET active=?,next_at=? WHERE {ws} AND agent_id=?',(int(active),next_occurrence(schedule['spec']),agent_id));self.db.commit();return self.get(agent_id)
    def enqueue_test(self,agent_id,delay_seconds=15):
        self.storage.begin_write()
        try:return self._enqueue_test(agent_id,delay_seconds)
        except Exception:self.db.rollback();raise
    def _enqueue_test(self,agent_id,delay_seconds=15):
        agent=self.storage.agent(agent_id);schedule=self.get(agent_id)
        if not schedule:raise ValueError('예약 설정이 필요합니다.')
        if agent['version']!=schedule['agent_version']:raise ValueError('워크플로우가 변경됐습니다. 예약 설정을 저장해 적용할 버전을 확인하세요.')
        agent=scheduled_agent(agent,schedule['spec'])
        source=self.execution_source(agent)
        preflight_schedule(self.storage,agent,[source['asset_id']] if source else [])
        if hasattr(self.storage,'bound'):agent=scheduled_agent(self.storage.storage.agent(agent_id),schedule['spec'])
        id=uid();due=(datetime.now(timezone.utc)+timedelta(seconds=delay_seconds)).isoformat()
        job={'id':id,'agent_id':agent_id,'agent_snapshot':agent,'kind':'schedule_test','due_at':due,'created_at':now()}
        binding=LibrarySources(self.storage).get(agent_id)
        if binding:job['library_source']={'selection':binding}
        self.db.execute('INSERT INTO builder_schedule_jobs({wsc}id,agent_id,slot_key,state,lease_until,lease_token,json) VALUES({wsv}?,?,?,\'queued\',NULL,NULL,?)',(id,agent_id,'test:'+id,json.dumps(job,ensure_ascii=False)));self.db.commit();return job
    def cancel_test(self,agent_id,job_id):
        # Same write lock as claim_due: a claim that already read this job finishes first.
        self.storage.begin_write()
        try:return self._cancel_test(agent_id,job_id)
        except Exception:self.db.rollback();raise
    def _cancel_test(self,agent_id,job_id):
        row=self.db.execute("SELECT json,state FROM builder_schedule_jobs WHERE {ws} AND id=? AND agent_id=?",(job_id,agent_id)).fetchone()
        if not row:raise ValueError("해당 자동화의 시험 예약을 찾을 수 없습니다.")
        job=json.loads(row[0])
        if job["kind"]!="schedule_test":raise ValueError("정규 예약은 예약 중지 버튼을 사용하세요.")
        if row[1]=="cancelled":self.db.commit();return {**job,"status":"cancelled"}
        job["finished_at"]=now()
        changed=self.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',json=? WHERE {ws} AND id=? AND agent_id=? AND state='queued'",(json.dumps(job,ensure_ascii=False),job_id,agent_id))
        if not changed.rowcount:raise ValueError("이미 실행 중이거나 종료된 예약은 취소할 수 없습니다.")
        self.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND job_id=? AND state='pending'",(now(),job_id))
        self.db.commit();return {**job,"status":"cancelled"}
    def claim_due(self,current=None):
        current=current or datetime.now(timezone.utc);stamp=current.isoformat();jobs=[]
        self.storage.begin_write()
        try:
            for agent_id,body,due in self.db.execute('SELECT agent_id,json,next_at FROM builder_schedules WHERE {ws} AND active=1 AND next_at<=?',(stamp,)).fetchall():
                schedule=json.loads(body);agent=self.storage.agent(agent_id)
                if agent['version']!=schedule['agent_version']:continue
                agent=scheduled_agent(agent,schedule['spec'])
                id=uid();job={'id':id,'agent_id':agent_id,'agent_snapshot':agent,'kind':'scheduled','due_at':due,'created_at':stamp}
                binding=LibrarySources(self.storage).get(agent_id)
                if binding:job['library_source']={'selection':binding}
                self.db.execute('INSERT INTO builder_schedule_jobs({wsc}id,agent_id,slot_key,state,lease_until,lease_token,json) VALUES({wsv}?,?,?,\'queued\',NULL,NULL,?) ON CONFLICT DO NOTHING',(id,agent_id,due,json.dumps(job,ensure_ascii=False)))
                self.db.execute('UPDATE builder_schedules SET next_at=? WHERE {ws} AND agent_id=?',(next_occurrence(schedule['spec'],current),agent_id))
            rows=self.db.execute("SELECT id,json FROM builder_schedule_jobs WHERE {ws} AND (state='queued' OR (state='running' AND lease_until<=?)) AND "+self.storage.json_text('json','due_at')+"<=? ORDER BY {seq} LIMIT 1",(stamp,stamp)).fetchall()
            for id,body in rows:
                job=json.loads(body)
                if job['due_at']>stamp:continue
                token=uid();lease=(current+timedelta(minutes=5)).isoformat()
                # Re-check eligibility in the UPDATE itself and hand out only rows it changed.
                changed=self.db.execute("UPDATE builder_schedule_jobs SET state='running',lease_token=?,lease_until=? WHERE {ws} AND id=? AND (state='queued' OR (state='running' AND lease_until<=?))",(token,lease,id,stamp))
                if changed.rowcount==1:jobs.append({**job,'lease_token':token})
            self.db.commit();return jobs
        except Exception:self.db.rollback();raise
    def heartbeat(self,id,token,current=None):
        current=current or datetime.now(timezone.utc)
        changed=self.db.execute("UPDATE builder_schedule_jobs SET lease_until=? WHERE {ws} AND id=? AND state='running' AND lease_token=?",((current+timedelta(minutes=5)).isoformat(),id,token))
        self.db.commit()
        if not changed.rowcount:raise ValueError('예약 작업의 실행 소유권이 만료되었습니다.')
        return {'status':'renewed'}
    def job(self,id,token):
        row=self.db.execute('SELECT json,state,lease_token FROM builder_schedule_jobs WHERE {ws} AND id=?',(id,)).fetchone()
        if not row or row[1]!='running' or row[2]!=token:raise ValueError('실행 작업의 소유권을 확인하지 못했습니다.')
        return json.loads(row[0])
    def finish(self,id,token,status,run_id=None,error=None,source_run=None):
        # Claim/reclaim and completion share the workspace write lock. Source
        # cursors must not commit before this owner's lease has been checked.
        self.storage.begin_write()
        try:
            job=self.job(id,token)
            if source_run is not None and (source_run['id']!=id or source_run['status']!=status):
                raise ValueError('예약 작업과 자료 처리 결과가 일치하지 않습니다.')
            job.update(run_id=run_id,error=error,finished_at=now())
            changed=self.db.execute("UPDATE builder_schedule_jobs SET state=?,json=?,lease_until=NULL WHERE {ws} AND id=? AND state='running' AND lease_token=?",(status,json.dumps(job,ensure_ascii=False),id,token))
            if not changed.rowcount:raise ValueError('예약 작업의 실행 소유권이 만료되었습니다.')
            manager=LibrarySources(self.storage)
            if source_run is not None:manager.complete(source_run,commit=False)
            if status=='failed':manager.failed_job(id,commit=False)
            self.db.commit()
        except Exception:self.db.rollback();raise
    def jobs(self,agent_id):
        return [{**json.loads(body),'status':state} for body,state in self.db.execute('SELECT json,state FROM builder_schedule_jobs WHERE {ws} AND agent_id=? ORDER BY {seq} DESC LIMIT 20',(agent_id,))]
