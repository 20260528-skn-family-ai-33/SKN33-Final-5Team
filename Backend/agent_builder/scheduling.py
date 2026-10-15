"""Durable local-server schedules, managed sources and leased jobs."""
import json
from datetime import datetime,timedelta,time,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .storage import uid,now

def next_occurrence(spec,current=None):
    current=current or datetime.now(timezone.utc);zone=ZoneInfo(spec['timezone']);local=current.astimezone(zone)
    date=local.date()
    if spec['frequency']=='weekly':date+=timedelta(days=(spec['weekday']-date.weekday())%7)
    candidate=datetime.combine(date,time(spec['hour'],spec['minute']),zone)
    # Shift nonexistent DST wall time forward; choose first fold on an ambiguous day.
    candidate=candidate.astimezone(timezone.utc).astimezone(zone)
    if candidate<=local:
        candidate=datetime.combine(date+timedelta(days=7 if spec['frequency']=='weekly' else 1),time(spec['hour'],spec['minute']),zone)
        candidate=candidate.astimezone(timezone.utc).astimezone(zone)
    return candidate.astimezone(timezone.utc).isoformat()

def schedule_errors(spec):
    issues=[]
    if spec['timezone'] is None:issues.append('예약 시간대를 알려주세요.')
    else:
        try:ZoneInfo(spec['timezone'])
        except (ZoneInfoNotFoundError,ValueError):issues.append('유효한 IANA 시간대가 필요합니다.')
    if spec['hour'] is None or spec['minute'] is None:issues.append('예약 실행 시간을 알려주세요.')
    if spec['frequency']=='weekly' and spec['weekday'] is None:issues.append('매주 실행할 요일을 알려주세요.')
    return issues

class Schedules:
    def __init__(self,storage):
        self.storage=storage;self.db=storage.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS builder_data_sources(agent_id TEXT PRIMARY KEY,id TEXT NOT NULL,asset_id TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS builder_schedules(agent_id TEXT PRIMARY KEY,json TEXT NOT NULL,next_at TEXT,active INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS builder_schedule_jobs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,slot_key TEXT NOT NULL,state TEXT NOT NULL,lease_until TEXT,lease_token TEXT,json TEXT NOT NULL,UNIQUE(agent_id,slot_key));
        ''');self.db.commit()
    def source(self,agent_id):
        row=self.db.execute('SELECT id,asset_id,version,updated_at FROM builder_data_sources WHERE agent_id=?',(agent_id,)).fetchone()
        if not row:return None
        return {'id':row[0],'asset_id':row[1],'version':row[2],'updated_at':row[3],'asset':self.storage.asset(row[1])}
    def replace_source(self,agent_id,asset_id,commit=True):
        agent=self.storage.agent(agent_id);asset=self.storage.asset(asset_id)
        if asset['kind']!='table':raise ValueError('관리 데이터 소스는 표 파일을 연결하세요.')
        from .models import AgentDraft
        from .nodes.validation import validate_node
        config=AgentDraft.model_validate(agent['config']).model_dump()
        # Check the incoming source against the saved conditions, not the old sample.
        document_ids=[id for id in config['document_ids'] if self.storage.asset(id)['kind']=='document']
        config['document_ids']=[asset_id]+document_ids
        if config.get('workflow'):
            for n in config['workflow']['nodes']:
                if n['tool']=='read_table':n['settings']['file_ids']=[asset_id]
        result=validate_node({'draft':config,'resources':[asset]+[self.storage.asset(id) for id in document_ids],'messages':[{'role':'user','content':r['quote']} for r in config['requirements'] if r['source_id']=='user']})
        # Source replacement doesn't revalidate old document citations against a new file.
        relevant=[issue for issue in result['issues'] if not issue.startswith('업무 조건의 인용 원문')]
        if relevant:raise ValueError('새 데이터 소스의 구조를 확인하세요: '+' / '.join(relevant))
        old=self.source(agent_id);id=old['id'] if old else uid();version=(old['version'] if old else 0)+1
        self.db.execute('INSERT INTO builder_data_sources VALUES(?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET asset_id=excluded.asset_id,version=excluded.version,updated_at=excluded.updated_at',(agent_id,id,asset_id,version,now()));
        if commit:self.db.commit()
        return self.source(agent_id)
    def configure(self,agent_id,commit=True):
        agent=self.storage.agent(agent_id);spec=agent['config'].get('schedule')
        if not spec:return None
        if schedule_errors(spec):raise ValueError('예약 필수 설정이 비어 있습니다.')
        data={'agent_id':agent_id,'agent_version':agent['version'],'spec':spec,'updated_at':now()}
        self.db.execute('INSERT INTO builder_schedules VALUES(?,?,?,0) ON CONFLICT(agent_id) DO UPDATE SET json=excluded.json,next_at=excluded.next_at,active=0',(agent_id,json.dumps(data,ensure_ascii=False),next_occurrence(spec)))
        if not self.source(agent_id):
            tables=[id for id in agent['config']['document_ids'] if self.storage.asset(id)['kind']=='table']
            if len(tables)==1:self.replace_source(agent_id,tables[0],commit=False)
        if commit:self.db.commit()
        return self.get(agent_id)
    def get(self,agent_id):
        row=self.db.execute('SELECT json,next_at,active FROM builder_schedules WHERE agent_id=?',(agent_id,)).fetchone()
        if not row:return None
        return {**json.loads(row[0]),'next_run_at':row[1],'active':bool(row[2])}
    def activate(self,agent_id,active):
        schedule=self.get(agent_id)
        if not schedule:raise ValueError('예약 설정이 없습니다. 대화로 시간을 정하고 저장하세요.')
        if active and not self.source(agent_id):raise ValueError('예약 실행할 관리 데이터 소스를 먼저 연결하세요.')
        self.db.execute('UPDATE builder_schedules SET active=?,next_at=? WHERE agent_id=?',(int(active),next_occurrence(schedule['spec']),agent_id));self.db.commit();return self.get(agent_id)
    def enqueue_test(self,agent_id,delay_seconds=15):
        if not self.get(agent_id) or not self.source(agent_id):raise ValueError('예약 설정과 데이터 소스가 필요합니다.')
        agent=self.storage.agent(agent_id);id=uid();due=(datetime.now(timezone.utc)+timedelta(seconds=delay_seconds)).isoformat()
        job={'id':id,'agent_id':agent_id,'agent_snapshot':agent,'kind':'schedule_test','due_at':due,'created_at':now()}
        self.db.execute('INSERT INTO builder_schedule_jobs VALUES(?,?,?,\'queued\',NULL,NULL,?)',(id,agent_id,'test:'+id,json.dumps(job,ensure_ascii=False)));self.db.commit();return job
    def cancel_test(self,agent_id,job_id):
        row=self.db.execute("SELECT json,state FROM builder_schedule_jobs WHERE id=? AND agent_id=?",(job_id,agent_id)).fetchone()
        if not row:raise ValueError("해당 자동화의 시험 예약을 찾을 수 없습니다.")
        job=json.loads(row[0])
        if job["kind"]!="schedule_test":raise ValueError("정규 예약은 예약 중지 버튼을 사용하세요.")
        if row[1]=="cancelled":return {**job,"status":"cancelled"}
        job["finished_at"]=now()
        changed=self.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',json=? WHERE id=? AND agent_id=? AND state='queued'",(json.dumps(job,ensure_ascii=False),job_id,agent_id))
        if not changed.rowcount:raise ValueError("이미 실행 중이거나 종료된 예약은 취소할 수 없습니다.")
        self.db.commit();return {**job,"status":"cancelled"}
    def claim_due(self,current=None):
        current=current or datetime.now(timezone.utc);stamp=current.isoformat();jobs=[]
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for agent_id,body,due in self.db.execute('SELECT agent_id,json,next_at FROM builder_schedules WHERE active=1 AND next_at<=?',(stamp,)).fetchall():
                schedule=json.loads(body);agent=self.storage.agent(agent_id)
                if agent['version']!=schedule['agent_version']:continue
                id=uid();job={'id':id,'agent_id':agent_id,'agent_snapshot':agent,'kind':'scheduled','due_at':due,'created_at':stamp}
                self.db.execute('INSERT OR IGNORE INTO builder_schedule_jobs VALUES(?,?,?,\'queued\',NULL,NULL,?)',(id,agent_id,due,json.dumps(job,ensure_ascii=False)))
                self.db.execute('UPDATE builder_schedules SET next_at=? WHERE agent_id=?',(next_occurrence(schedule['spec'],current),agent_id))
            rows=self.db.execute("SELECT id,json FROM builder_schedule_jobs WHERE (state='queued' OR (state='running' AND lease_until<=?)) AND json_extract(json,'$.due_at')<=? ORDER BY rowid LIMIT 1",(stamp,stamp)).fetchall()
            for id,body in rows:
                job=json.loads(body)
                if job['due_at']>stamp:continue
                token=uid();lease=(current+timedelta(minutes=5)).isoformat()
                self.db.execute("UPDATE builder_schedule_jobs SET state='running',lease_token=?,lease_until=? WHERE id=?",(token,lease,id))
                jobs.append({**job,'lease_token':token})
            self.db.commit();return jobs
        except Exception:self.db.rollback();raise
    def heartbeat(self,id,token,current=None):
        current=current or datetime.now(timezone.utc)
        changed=self.db.execute("UPDATE builder_schedule_jobs SET lease_until=? WHERE id=? AND state='running' AND lease_token=?",((current+timedelta(minutes=5)).isoformat(),id,token))
        self.db.commit()
        if not changed.rowcount:raise ValueError('예약 작업의 실행 소유권이 만료되었습니다.')
        return {'status':'renewed'}
    def job(self,id,token):
        row=self.db.execute('SELECT json,state,lease_token FROM builder_schedule_jobs WHERE id=?',(id,)).fetchone()
        if not row or row[1]!='running' or row[2]!=token:raise ValueError('실행 작업의 소유권을 확인하지 못했습니다.')
        return json.loads(row[0])
    def finish(self,id,token,status,run_id=None,error=None):
        job=self.job(id,token);job.update(run_id=run_id,error=error,finished_at=now())
        self.db.execute('UPDATE builder_schedule_jobs SET state=?,json=?,lease_until=NULL WHERE id=? AND lease_token=?',(status,json.dumps(job,ensure_ascii=False),id,token));self.db.commit()
    def jobs(self,agent_id):
        return [{**json.loads(body),'status':state} for body,state in self.db.execute('SELECT json,state FROM builder_schedule_jobs WHERE agent_id=? ORDER BY rowid DESC LIMIT 20',(agent_id,))]
