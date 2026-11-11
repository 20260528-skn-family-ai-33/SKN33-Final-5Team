import json
import os
from pathlib import Path
from documents.resources.document_resources import RemovedUpload, available_inputs
from infrastructure.persistence.ids import uid, now
from infrastructure.persistence.db import WORKSPACE, enable_wal, open_database, workspace_id
from workflow_engine.catalog.registry import TOOLS
from infrastructure.persistence.asset_library import LIBRARY_SCHEMA
from workflow_engine.scheduling.library_sources import SOURCE_SCHEMA

__all__=['Storage','enable_wal']

NUL_ESCAPE=r'(?<!\\)((?:\\\\)*)\\u0000'

# SQLite only. PostgreSQL tables come from infrastructure/db/migrations, applied explicitly.
SCHEMA='''
CREATE TABLE IF NOT EXISTS registered_tools(id TEXT NOT NULL,version INTEGER NOT NULL,module_path TEXT NOT NULL,schema_json TEXT NOT NULL,PRIMARY KEY(id,version));
CREATE TABLE IF NOT EXISTS builder_sessions(id TEXT PRIMARY KEY,json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS builder_assets(id TEXT PRIMARY KEY,json TEXT NOT NULL,rows_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_versions(agent_id TEXT NOT NULL,version INTEGER NOT NULL,json TEXT NOT NULL,PRIMARY KEY(agent_id,version));
CREATE TABLE IF NOT EXISTS agent_tests(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS builder_generation_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS generation_requests_session ON builder_generation_requests(session_id);
CREATE TABLE IF NOT EXISTS builder_run_leases(run_id TEXT PRIMARY KEY,token TEXT NOT NULL,expires_at TEXT NOT NULL,pid INTEGER NOT NULL,active INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS builder_schedule_jobs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,slot_key TEXT NOT NULL,state TEXT NOT NULL,lease_until TEXT,lease_token TEXT,json TEXT NOT NULL,UNIQUE(agent_id,slot_key));
CREATE TABLE IF NOT EXISTS reply_review_decisions(run_id TEXT NOT NULL REFERENCES agent_tests(id),review_id TEXT NOT NULL,json TEXT NOT NULL,PRIMARY KEY(run_id,review_id));
CREATE TABLE IF NOT EXISTS document_reviews(asset_id TEXT PRIMARY KEY REFERENCES builder_assets(id),revision INTEGER NOT NULL,json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_review_events(asset_id TEXT NOT NULL REFERENCES builder_assets(id),revision INTEGER NOT NULL,request_id TEXT NOT NULL,json TEXT NOT NULL,PRIMARY KEY(asset_id,revision),UNIQUE(asset_id,request_id));
'''+LIBRARY_SCHEMA+SOURCE_SCHEMA

class Storage:
    def __init__(self,directory):
        self.workspace_id=workspace_id(directory)
        Path(directory).mkdir(parents=True,exist_ok=True)
        self.path=str(Path(directory)/'agent-builder.sqlite')
        self.db=open_database(self.path,self.workspace_id)
        self.dialect=self.db.dialect
        self.business=None
        if self.dialect=='postgres' and os.environ.get('A4B_BUSINESS_RECORDS')=='a4b' and WORKSPACE.fullmatch(self.workspace_id):
            from infrastructure.persistence.business_records import BusinessRecords
            self.business=BusinessRecords(self.db.connection,self.workspace_id)
        try:
            self.ensure_schema(SCHEMA)
            # A worker starts for every call (heartbeats too), so write only what actually changed.
            saved={(row[0],row[1]):(row[2],row[3]) for row in self.db.execute('SELECT id,version,module_path,schema_json FROM registered_tools WHERE {ws}').fetchall()}
            for tool in TOOLS:
                schema=json.dumps(tool,ensure_ascii=False)
                if saved.get((tool['id'],tool['version']))!=(tool['module_path'],schema):
                    self.db.execute('INSERT INTO registered_tools({wsc}id,version,module_path,schema_json) VALUES({wsv}?,?,?,?) ON CONFLICT({wsc}id,version) DO UPDATE SET module_path=excluded.module_path,schema_json=excluded.schema_json',(tool['id'],tool['version'],tool['module_path'],schema))
            self.db.commit()
        except Exception:
            self.db.close()
            raise
    def ensure_schema(self,script):
        """Creates SQLite tables on first use. PostgreSQL needs the explicit migrations instead."""
        if self.dialect=='sqlite':self.db.executescript(script)
    def begin_write(self):self.db.begin_write()
    def json_text(self,column,key):
        if self.dialect!='postgres':return f"json_extract({column},'$.{key}')"
        # ->> decodes every string in the document and \u0000 has no text form, so one NUL in
        # a run's output would fail the whole query. Only this read drops it; the row keeps it.
        # An even run of backslashes before it is literal text: \\u0000 is not an escape.
        text=f"{column}::text"
        return f"(CASE WHEN position('\\u0000' in {text})>0 THEN regexp_replace({text},'{NUL_ESCAPE}','\\1','g') ELSE {text} END)::json->>'{key}'"
    def close(self): self.db.close()
    def session(self,id):
        row=self.db.execute('SELECT json,revision FROM builder_sessions WHERE {ws} AND id=?',(id,)).fetchone()
        if not row: raise ValueError('대화를 찾을 수 없습니다.')
        return {**json.loads(row[0]),'revision':row[1]}
    def new_session(self,model,credential_id,agent_id=None,node_refs=None):
        base=self.agent(agent_id) if agent_id else None
        if node_refs is not None and (not isinstance(node_refs,list) or len(node_refs)>10 or any(not isinstance(ref,str) for ref in node_refs) or len(set(node_refs))!=len(node_refs)):
            raise ValueError('선택한 노드 버전을 확인하세요. 중복 없이 최대 10개 선택할 수 있습니다.')
        inputs=available_inputs(self,base['config']['document_ids']) if base else []
        files=[asset['id'] for asset in inputs]
        if base and base['config'].get('schedule'):
            from workflow_engine.scheduling.schedules import Schedules
            source=Schedules(self).source(agent_id)
            if source:files=[source['asset_id']]+[asset['id'] for asset in inputs if asset['kind']=='document']
        s={'id':uid(),'model':model,'credential_id':credential_id,'agent_id':agent_id,'base_version':base['version'] if base else 0,'file_ids':files,'created_at':now()}
        from workflow_engine.library.node_library import get, default_nodes, list_nodes
        s['required_node_refs']=node_refs or []
        s['node_library']=([get(self,ref) for ref in node_refs] if node_refs else default_nodes()+list_nodes(self))
        if base:
            refs={n['ref'] for n in s['node_library']}
            s['node_library'] += [n for n in base['config'].get('node_snapshots',[]) if n['ref'] not in refs]
            self.begin_write()
        try:
            if base:self.require_assets(files)
            self.db.execute('INSERT INTO builder_sessions({wsc}id,json) VALUES({wsv}?,?)',(s['id'],json.dumps(s,ensure_ascii=False)))
            if self.business:self.business.open_session(s)
            self.db.commit()
        except Exception:
            self.db.rollback();raise
        return self.session(s['id'])
    def update_session(self,s,expected,design_state=None):
        if design_state and design_state.get('draft'):
            s={**s,'draft_name':design_state['draft'].get('name')}
        result=self.db.execute('UPDATE builder_sessions SET json=?,revision=revision+1 WHERE {ws} AND id=? AND revision=?',(json.dumps({k:v for k,v in s.items() if k!='revision'},ensure_ascii=False),s['id'],expected))
        if not result.rowcount: raise ValueError('다른 요청에서 대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
        if self.business and design_state is not None:self.business.update_design(s['id'],design_state)
        if design_state is not None and getattr(self,'generation_audit',None):
            self.generation_audit.published_in_transaction(design_state, expected+1)
        self.db.commit(); return self.session(s['id'])
    def add_asset(self,asset,rows,commit=True):
        self.db.execute('INSERT INTO builder_assets({wsc}id,json,rows_json) VALUES({wsv}?,?,?)',(asset['id'],json.dumps(asset,ensure_ascii=False),json.dumps(rows,ensure_ascii=False)))
        if self.business and asset.get('original_path'):self.business.save_document(asset)
        if commit:self.db.commit()
        return asset
    def asset(self,id):
        from documents.review.document_review import effective_asset
        return effective_asset(self.raw_asset(id),self.document_review(id))
    def raw_asset(self,id):
        row=self.db.execute('SELECT json FROM builder_assets WHERE {ws} AND id=?',(id,)).fetchone()
        if not row: raise ValueError('연결한 자료를 찾을 수 없습니다.')
        asset=json.loads(row[0])
        if 'pending_delete' in asset or asset.get('deleted'):raise RemovedUpload(asset)
        return asset
    def require_assets(self,ids):
        if ids:
            params=','.join('?' for _ in ids)
            count=self.db.execute('SELECT count(*) FROM builder_assets WHERE {ws} AND id IN ('+params+') AND '+
                                  self.json_text('json','pending_delete')+' IS NULL AND '+
                                  self.json_text('json','deleted')+' IS NULL',tuple(ids)).fetchone()[0]
            if count!=len(set(ids)):raise ValueError('자료가 삭제됐습니다. 연결 자료를 다시 확인하세요.')
    def document_review(self,id):
        row=self.db.execute('SELECT json FROM document_reviews WHERE {ws} AND asset_id=?',(id,)).fetchone()
        return json.loads(row[0]) if row else None
    def document_review_history(self,id):
        return [json.loads(row[0]) for row in self.db.execute('SELECT json FROM document_review_events WHERE {ws} AND asset_id=? ORDER BY revision DESC LIMIT 50',(id,))]
    def rows(self,id):
        row=self.db.execute('SELECT rows_json FROM builder_assets WHERE {ws} AND id=? AND '+
                            self.json_text('json','pending_delete')+' IS NULL AND '+
                            self.json_text('json','deleted')+' IS NULL',(id,)).fetchone()
        if not row: raise ValueError('표 파일을 찾을 수 없습니다.')
        return json.loads(row[0])
    def agents(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT json FROM agent_versions v WHERE {ws:v} AND version=(SELECT MAX(version) FROM agent_versions WHERE {ws} AND agent_id=v.agent_id) ORDER BY {seq} DESC')]
    def agent(self,id):
        row=self.db.execute('SELECT json FROM agent_versions WHERE {ws} AND agent_id=? ORDER BY version DESC LIMIT 1',(id,)).fetchone()
        if not row: raise ValueError('저장한 에이전트를 찾을 수 없습니다.')
        return json.loads(row[0])
    def agent_version(self,id,version):
        row=self.db.execute('SELECT json FROM agent_versions WHERE {ws} AND agent_id=? AND version=?',(id,version)).fetchone()
        if not row:raise ValueError('저장된 워크플로우 버전을 찾을 수 없습니다.')
        return json.loads(row[0])
    def _insert_agent_version(self,agent):
        self.db.execute('INSERT INTO agent_versions({wsc}agent_id,version,json) VALUES({wsv}?,?,?)',
                        (agent['id'],agent['version'],json.dumps(agent,ensure_ascii=False)))
        if self.business:self.business.save_version(agent)

    def rename_agent(self,id,name,base_version):
        if type(base_version) is not int or not 1<=base_version<=9007199254740991:
            raise ValueError('워크플로우 버전을 확인해주세요.')
        if not isinstance(name,str) or any(ord(char)<32 or char in '\x7f\x85\u2028\u2029' for char in name):
            raise ValueError('워크플로우 이름은 한 줄로 입력해주세요.')
        name=name.strip()
        if not 1<=len(name)<=60:
            raise ValueError('워크플로우 이름을 1~60자로 입력해주세요.')
        from workflow_engine.scheduling.schedules import Schedules
        schedules=Schedules(self)
        self.begin_write()
        try:
            agent=self.agent(id)
            if agent['config']['name']!=name:
                if agent['version']!=base_version:
                    raise ValueError('다른 곳에서 워크플로우가 수정됐어요. 목록을 새로고침하고 다시 시도해주세요.')
                agent={**agent,'version':base_version+1,'updated_at':now(),
                       'config':{**agent['config'],'name':name}}
                self._insert_agent_version(agent)
                schedules.advance_version(id,base_version,agent['version'])
            self.db.commit()
            return agent
        except Exception:
            self.db.rollback()
            raise

    def save_agent(self,state):
        from agent_builder.capabilities.capability_gate import require_save_ready
        require_save_ready(state)
        from workflow_engine.scheduling.schedules import Schedules
        from workflow_engine.library.node_library import init, apply_refs, used_snapshots, persist_proposals
        init(self)
        draft=json.loads(json.dumps({k:v for k,v in state['draft'].items() if k!='node_snapshots'}))
        pool=state.get('node_library',[])
        draft['workflow']=apply_refs(draft.get('workflow'),pool,state.get('required_node_refs',[]))
        # Legacy table-only configurations need not acquire a new workflow key.
        if draft['workflow'] is None and 'workflow' not in state['draft']:draft.pop('workflow')
        schedules=Schedules(self)  # Create/migrate schema BEFORE entering the atomic save.
        self.begin_write()
        try:
            session_id=state['session_id']
            self.require_assets(draft.get('document_ids',[]))
            for old in self.agents():
                if old['session_id']==session_id and {k:v for k,v in old['config'].items() if k!='node_snapshots'}==draft:
                    # Repair old partial saves on idempotent replays without pausing a healthy schedule.
                    if draft.get('schedule') and not schedules.get(old['id']):
                        schedules.configure(old['id'],commit=False)
                    if getattr(self,'generation_audit',None):self.generation_audit.saved_in_transaction(old['id'],old['version'])
                    self.db.commit();return old['id']
            id=state.get('base_agent_id') or (session_id if self.business else uid())
            row=self.db.execute('SELECT MAX(version) FROM agent_versions WHERE {ws} AND agent_id=?',(id,)).fetchone()
            version=row[0] or 0
            if version!=state.get('base_version',0):raise ValueError('저장된 에이전트가 다른 대화에서 수정됐습니다. 최신 설정을 다시 열어주세요.')
            old=self.agent(id) if version else None
            snapshots=used_snapshots(draft,pool)
            if snapshots:draft['node_snapshots']=snapshots
            persist_proposals(self,snapshots)
            a={'id':id,'version':version+1,'config':draft,'model':state['model'],'credential_id':state['credential_id'],'tool_versions':{id:1 for id in draft['tool_ids']},'session_id':session_id,'created_at':old['created_at'] if old else now(),'updated_at':now()}
            from workflow_engine.library.reusable_workflows import pin_dependencies
            a['workflow_dependencies']=pin_dependencies(self,draft,state['model'],state['credential_id'],id)
            self._insert_agent_version(a)
            if draft.get('schedule'):schedules.configure(id,commit=False,previous_config=old['config'] if old else None)
            else:self.db.execute('UPDATE builder_schedules SET active=0 WHERE {ws} AND agent_id=?',(id,))
            if getattr(self,'generation_audit',None):self.generation_audit.saved_in_transaction(id,version+1)
            self.db.commit();return id
        except Exception:self.db.rollback();raise
    def write_test(self,run):
        self.db.execute('INSERT INTO agent_tests({wsc}id,agent_id,json) VALUES({wsv}?,?,?) ON CONFLICT({wsc}id) DO UPDATE SET json=excluded.json',(run['id'],run['agent_id'],json.dumps(run,ensure_ascii=False)))
        if self.business:self.business.save_run(run)
    def save_test(self,run):
        self.write_test(run);self.db.commit()
    def _read_test(self,raw):
        from verification.reply_review.decisions import decorate_run
        from workflow_engine.run_answer import public_run_answer
        run=json.loads(raw)
        if 'answer' in run:
            run['answer']=public_run_answer(run['answer'],run.get('outputs',{}))
        return decorate_run(self,run)
    def tests(self,agent_id):
        return [self._read_test(r[0]) for r in self.db.execute('SELECT json FROM agent_tests WHERE {ws} AND agent_id=? ORDER BY {seq} DESC LIMIT 20',(agent_id,))]

    def test(self,id):
        row=self.db.execute('SELECT json FROM agent_tests WHERE {ws} AND id=?',(id,)).fetchone()
        if not row:raise ValueError('실행을 찾을 수 없습니다.')
        return self._read_test(row[0])
