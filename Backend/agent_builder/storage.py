import json, sqlite3, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from .registry import TOOLS

def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())

def enable_wal(connection, timeout=10):
    # SQLite can return BUSY immediately while concurrent first connections
    # switch journal mode, even when connect(timeout=...) has a busy timeout.
    deadline=time.monotonic()+timeout
    while True:
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            return
        except sqlite3.OperationalError as error:
            code=getattr(error,'sqlite_errorcode',0)&0xff
            if code not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or time.monotonic()>=deadline:
                raise
            time.sleep(min(.05,max(0,deadline-time.monotonic())))

class Storage:
    def __init__(self,directory):
        Path(directory).mkdir(parents=True,exist_ok=True)
        self.path=str(Path(directory)/'agent-builder.sqlite')
        self.db=sqlite3.connect(self.path,timeout=10,check_same_thread=False)
        try:enable_wal(self.db)
        except Exception:
            self.db.close()
            raise
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS registered_tools(id TEXT NOT NULL,version INTEGER NOT NULL,module_path TEXT NOT NULL,schema_json TEXT NOT NULL,PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS builder_sessions(id TEXT PRIMARY KEY,json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS builder_assets(id TEXT PRIMARY KEY,json TEXT NOT NULL,rows_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_versions(agent_id TEXT NOT NULL,version INTEGER NOT NULL,json TEXT NOT NULL,PRIMARY KEY(agent_id,version));
        CREATE TABLE IF NOT EXISTS agent_tests(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS builder_run_leases(run_id TEXT PRIMARY KEY,token TEXT NOT NULL,expires_at TEXT NOT NULL,pid INTEGER NOT NULL,active INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS builder_schedule_jobs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,slot_key TEXT NOT NULL,state TEXT NOT NULL,lease_until TEXT,lease_token TEXT,json TEXT NOT NULL,UNIQUE(agent_id,slot_key));
        CREATE TABLE IF NOT EXISTS reply_review_decisions(run_id TEXT NOT NULL REFERENCES agent_tests(id),review_id TEXT NOT NULL,json TEXT NOT NULL,PRIMARY KEY(run_id,review_id));
        ''')
        for tool in TOOLS:self.db.execute('INSERT INTO registered_tools VALUES(?,?,?,?) ON CONFLICT(id,version) DO UPDATE SET schema_json=excluded.schema_json',(tool['id'],tool['version'],tool['module_path'],json.dumps(tool,ensure_ascii=False)))
        self.db.commit()
    def close(self): self.db.close()
    def session(self,id):
        row=self.db.execute('SELECT json,revision FROM builder_sessions WHERE id=?',(id,)).fetchone()
        if not row: raise ValueError('대화를 찾을 수 없습니다.')
        return {**json.loads(row[0]),'revision':row[1]}
    def new_session(self,model,credential_id,agent_id=None,node_refs=None):
        base=self.agent(agent_id) if agent_id else None
        if node_refs is not None and (not isinstance(node_refs,list) or len(node_refs)>10 or any(not isinstance(ref,str) for ref in node_refs) or len(set(node_refs))!=len(node_refs)):
            raise ValueError('선택한 노드 버전을 확인하세요. 중복 없이 최대 10개 선택할 수 있습니다.')
        files=base['config']['document_ids'] if base else []
        if base and base['config'].get('schedule'):
            from .scheduling import Schedules
            source=Schedules(self).source(agent_id)
            if source:files=[source['asset_id']]+[id for id in files if self.asset(id)['kind']=='document']
        s={'id':uid(),'model':model,'credential_id':credential_id,'agent_id':agent_id,'base_version':base['version'] if base else 0,'file_ids':files,'created_at':now()}
        from .node_library import get,default_nodes,list_nodes
        s['required_node_refs']=node_refs or []
        s['node_library']=([get(self,ref) for ref in node_refs] if node_refs else default_nodes()+list_nodes(self))
        if base:
            refs={n['ref'] for n in s['node_library']}
            s['node_library'] += [n for n in base['config'].get('node_snapshots',[]) if n['ref'] not in refs]
        self.db.execute('INSERT INTO builder_sessions(id,json) VALUES(?,?)',(s['id'],json.dumps(s,ensure_ascii=False)));self.db.commit()
        return self.session(s['id'])
    def update_session(self,s,expected):
        result=self.db.execute('UPDATE builder_sessions SET json=?,revision=revision+1 WHERE id=? AND revision=?',(json.dumps({k:v for k,v in s.items() if k!='revision'},ensure_ascii=False),s['id'],expected))
        if not result.rowcount: raise ValueError('다른 요청에서 대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
        self.db.commit(); return self.session(s['id'])
    def add_asset(self,asset,rows):
        self.db.execute('INSERT INTO builder_assets VALUES(?,?,?)',(asset['id'],json.dumps(asset,ensure_ascii=False),json.dumps(rows,ensure_ascii=False)));self.db.commit();return asset
    def asset(self,id):
        row=self.db.execute('SELECT json FROM builder_assets WHERE id=?',(id,)).fetchone()
        if not row: raise ValueError('연결한 자료를 찾을 수 없습니다.')
        return json.loads(row[0])
    def rows(self,id):
        row=self.db.execute('SELECT rows_json FROM builder_assets WHERE id=?',(id,)).fetchone()
        if not row: raise ValueError('표 파일을 찾을 수 없습니다.')
        return json.loads(row[0])
    def agents(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT json FROM agent_versions v WHERE version=(SELECT MAX(version) FROM agent_versions WHERE agent_id=v.agent_id) ORDER BY rowid DESC')]
    def agent(self,id):
        row=self.db.execute('SELECT json FROM agent_versions WHERE agent_id=? ORDER BY version DESC LIMIT 1',(id,)).fetchone()
        if not row: raise ValueError('저장한 에이전트를 찾을 수 없습니다.')
        return json.loads(row[0])
    def agent_version(self,id,version):
        row=self.db.execute('SELECT json FROM agent_versions WHERE agent_id=? AND version=?',(id,version)).fetchone()
        if not row:raise ValueError('저장된 워크플로우 버전을 찾을 수 없습니다.')
        return json.loads(row[0])
    def save_agent(self,state):
        from .capability_gate import require_save_ready
        require_save_ready(state)
        from .scheduling import Schedules
        from .node_library import init,apply_refs,used_snapshots,persist_proposals
        init(self)
        draft=json.loads(json.dumps({k:v for k,v in state['draft'].items() if k!='node_snapshots'}))
        pool=state.get('node_library',[])
        draft['workflow']=apply_refs(draft.get('workflow'),pool,state.get('required_node_refs',[]))
        # Legacy table-only configurations need not acquire a new workflow key.
        if draft['workflow'] is None and 'workflow' not in state['draft']:draft.pop('workflow')
        schedules=Schedules(self)  # Create/migrate schema BEFORE entering the atomic save.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            session_id=state['session_id']
            for old in self.agents():
                if old['session_id']==session_id and {k:v for k,v in old['config'].items() if k!='node_snapshots'}==draft:
                    # Repair old partial saves on idempotent replays without pausing a healthy schedule.
                    if draft.get('schedule') and (not schedules.get(old['id']) or not schedules.source(old['id'])):
                        schedules.configure(old['id'],commit=False)
                    self.db.commit();return old['id']
            id=state.get('base_agent_id') or uid()
            row=self.db.execute('SELECT MAX(version) FROM agent_versions WHERE agent_id=?',(id,)).fetchone()
            version=row[0] or 0
            if version!=state.get('base_version',0):raise ValueError('저장된 에이전트가 다른 대화에서 수정됐습니다. 최신 설정을 다시 열어주세요.')
            old=self.agent(id) if version else None
            snapshots=used_snapshots(draft,pool)
            if snapshots:draft['node_snapshots']=snapshots
            persist_proposals(self,snapshots)
            a={'id':id,'version':version+1,'config':draft,'model':state['model'],'credential_id':state['credential_id'],'tool_versions':{id:1 for id in draft['tool_ids']},'session_id':session_id,'created_at':old['created_at'] if old else now(),'updated_at':now()}
            from .reusable_workflows import pin_dependencies
            a['workflow_dependencies']=pin_dependencies(self,draft,state['model'],state['credential_id'],id)
            self.db.execute('INSERT INTO agent_versions VALUES(?,?,?)',(id,a['version'],json.dumps(a,ensure_ascii=False)))
            if draft.get('schedule'):schedules.configure(id,commit=False)
            else:self.db.execute('UPDATE builder_schedules SET active=0 WHERE agent_id=?',(id,))
            self.db.commit();return id
        except Exception:self.db.rollback();raise
    def save_test(self,run):
        self.db.execute('INSERT INTO agent_tests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json',(run['id'],run['agent_id'],json.dumps(run,ensure_ascii=False)));self.db.commit()
    def tests(self,agent_id):
        from .review_decisions import decorate_run
        return [decorate_run(self,json.loads(r[0])) for r in self.db.execute('SELECT json FROM agent_tests WHERE agent_id=? ORDER BY rowid DESC LIMIT 20',(agent_id,))]

    def test(self,id):
        row=self.db.execute('SELECT json FROM agent_tests WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('실행을 찾을 수 없습니다.')
        from .review_decisions import decorate_run
        return decorate_run(self,json.loads(row[0]))
