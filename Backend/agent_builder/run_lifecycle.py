"""Persisted execution ownership, crash recovery and attempt history (no credentials)."""
import json,os
from datetime import datetime,timedelta,timezone
from .storage import now

LEASE_SECONDS=90

def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

class RunLifecycle:
    def __init__(self,storage):self.storage=storage;self.db=storage.db
    def start(self,run,token):
        stamp=datetime.now(timezone.utc)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT token,expires_at,pid,active FROM builder_run_leases WHERE run_id=?',(run['id'],)).fetchone()
            if old and old[3] and old[0]!=token and old[1]>stamp.isoformat() and alive(old[2]):raise ValueError('이 실행을 다른 작업자가 처리 중입니다.')
            if self.db.execute("SELECT 1 FROM builder_schedule_jobs WHERE id=? AND state='running'",(run['id'],)).fetchone() and run.get('_retry'):
                raise ValueError('예약 작업이 실행 중이므로 재시도할 수 없습니다.')
            run.pop('_retry',None)
            run.update(status='running',updated_at=stamp.isoformat(),error=None)
            attempts=run.setdefault('attempts',[])
            if not attempts and run.get('_previous_status'):
                attempts.append({'number':1,'status':run['_previous_status'],'error':run.get('_previous_error'),'started_at':run['created_at'],'finished_at':run.get('_previous_updated_at')})
            for key in ['_previous_status','_previous_error','_previous_updated_at']:run.pop(key,None)
            attempts.append({'number':len(attempts)+1,'status':'running','started_at':stamp.isoformat(),'error':None})
            run.pop('error_type',None);run.pop('diagnostic_frames',None)
            self.db.execute('INSERT INTO builder_run_leases VALUES(?,?,?,?,1) ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,expires_at=excluded.expires_at,pid=excluded.pid,active=1',(run['id'],token,(stamp+timedelta(seconds=LEASE_SECONDS)).isoformat(),os.getpid()))
            self._save(run);self.db.commit()
        except Exception:self.db.rollback();raise
    def _save(self,run):
        self.db.execute('INSERT INTO agent_tests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json',(run['id'],run['agent_id'],json.dumps(run,ensure_ascii=False)))
    def assert_owner(self,id,token):
        row=self.db.execute('SELECT token,expires_at,active FROM builder_run_leases WHERE run_id=?',(id,)).fetchone()
        if not row or row[0]!=token or not row[2] or row[1]<=now():raise ValueError('실행 소유권이 만료되었습니다. 실행 기록에서 재시도하세요.')
    def heartbeat(self,id,token):
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=LEASE_SECONDS)).isoformat()
        changed=self.db.execute('UPDATE builder_run_leases SET expires_at=? WHERE run_id=? AND token=? AND active=1 AND expires_at>?',(expiry,id,token,now()));self.db.commit()
        if not changed.rowcount:
            row=self.db.execute('SELECT token,active FROM builder_run_leases WHERE run_id=?',(id,)).fetchone()
            run=self.db.execute('SELECT json FROM agent_tests WHERE id=?',(id,)).fetchone()
            if row and row[0]==token and not row[1] and run and json.loads(run[0])['status']!='running':return {'status':'finished'}
            raise ValueError('실행 소유권이 만료되었습니다.')
        return {'status':'renewed'}
    def _sync_job(self,run,interrupted=False):
        row=self.db.execute('SELECT json,state FROM builder_schedule_jobs WHERE id=?',(run['id'],)).fetchone()
        if not row or row[1]=='running' and not interrupted:return
        job=json.loads(row[0]);history=job.setdefault('attempt_history',[])
        if row[1]!=run['status']:
            history.append({'status':row[1],'error':job.get('error'),'finished_at':job.get('finished_at')})
        job.update(run_id=run['id'],error=run.get('error'),finished_at=now(),retry_count=max(0,len(run.get('attempts',[]))-1))
        self.db.execute('UPDATE builder_schedule_jobs SET state=?,json=?,lease_until=NULL WHERE id=?',(run['status'],json.dumps(job,ensure_ascii=False),run['id']))
    def publish(self,id,token,target,content):
        # Hold the SQLite ownership lock through the small atomic file publication.
        # A reclaimed worker cannot overwrite the new owner's report.
        self.db.execute('BEGIN IMMEDIATE')
        temp=target.with_name(target.name+'.'+token+'.tmp')
        try:
            self.assert_owner(id,token)
            target.parent.mkdir(exist_ok=True)
            temp.write_text(content,encoding='utf-8');os.replace(temp,target)
            self.db.commit()
        except Exception:
            self.db.rollback()
            try:temp.unlink(missing_ok=True)
            except OSError:pass
            raise
    def finish(self,run,token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.assert_owner(run['id'],token)
            run['updated_at']=now()
            run['attempts'][-1].update(status=run['status'],error=run.get('error'),finished_at=run['updated_at'])
            self._save(run);self._sync_job(run)
            self.db.execute('UPDATE builder_run_leases SET active=0 WHERE run_id=? AND token=?',(run['id'],token));self.db.commit()
        except Exception:self.db.rollback();raise
    def interrupt(self,id,token=None,reason='실행 프로세스가 종료되었습니다. 실패 단계부터 재시도하세요.'):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            lease=self.db.execute('SELECT token,active FROM builder_run_leases WHERE run_id=?',(id,)).fetchone()
            if token and (not lease or lease[0]!=token or not lease[1]):self.db.commit();return {'status':'unchanged'}
            row=self.db.execute('SELECT json FROM agent_tests WHERE id=?',(id,)).fetchone()
            if not row:self.db.commit();return {'status':'not_started'}
            run=json.loads(row[0])
            if run['status']!='running':self.db.commit();return {'status':'unchanged'}
            run.update(status='failed',error=reason,error_type='InterruptedExecution',updated_at=now())
            run.setdefault('steps',[]).append({'node':'execution','label':'실행 중단','status':'failed','detail':reason})
            attempts=run.setdefault('attempts',[{'number':1,'started_at':run['created_at']}])
            if not attempts:attempts.append({'number':1,'started_at':run['created_at']})
            attempts[-1].update(status='failed',error=reason,finished_at=run['updated_at'])
            self._save(run);self._sync_job(run,interrupted=True)
            self.db.execute('UPDATE builder_run_leases SET active=0 WHERE run_id=?',(id,));self.db.commit();return {'status':'failed'}
        except Exception:self.db.rollback();raise
    def recover(self):
        rows=self.db.execute("SELECT t.id,l.token,l.expires_at,l.pid,l.active FROM agent_tests t LEFT JOIN builder_run_leases l ON l.run_id=t.id WHERE json_extract(t.json,'$.status')='running'").fetchall();recovered=[]
        for id,token,expiry,pid,active in rows:
            if not active or expiry<=now() or not alive(pid):
                # interrupt compares ownership again, so a new execution cannot be overwritten.
                if self.interrupt(id,token)['status']=='failed':recovered.append(id)
        return {'recovered':recovered}
