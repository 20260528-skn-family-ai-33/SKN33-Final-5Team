"""Scoped library bindings, pinned inputs and successful-file processing cursors."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json
from uuid import uuid5,NAMESPACE_URL
from infrastructure.persistence.ids import uid,now
from infrastructure.persistence.asset_library import AssetLibrary,identifier
from documents.resources.document_resources import supports,chunks_for_asset

SOURCE_SCHEMA='''
CREATE TABLE IF NOT EXISTS builder_library_sources(agent_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS builder_source_snapshots(run_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,source_key TEXT NOT NULL,json TEXT NOT NULL,state TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS builder_source_processed(agent_id TEXT NOT NULL,revision INTEGER NOT NULL,source_key TEXT NOT NULL,job_id TEXT NOT NULL,state TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(agent_id,revision,source_key));
CREATE INDEX IF NOT EXISTS builder_source_processed_job ON builder_source_processed(job_id);
'''


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def target_kind(agent,selection):
    node=next((n for n in (agent['config'].get('workflow') or {}).get('nodes',[]) if n['id']==selection['target_node_id']),None)
    if not node or node['tool'] not in ('read_table','read_document') or node['settings'].get('operation'):
        raise ValueError('자료함 파일을 연결할 표/문서 읽기 작업을 선택하세요.')
    return 'table' if node['tool']=='read_table' else 'document'


def normalized(raw,agent):
    if not isinstance(raw,dict) or set(raw)-{'mode','space','team_id','folder_id','asset_ids','target_node_id'}:
        raise ValueError('자료함 읽기 설정을 확인하세요.')
    mode=raw.get('mode');space=raw.get('space')
    if mode not in ('fixed','latest','new','all') or space not in ('personal','team'):raise ValueError('자료함 읽기 모드와 공간을 확인하세요.')
    team=identifier(raw.get('team_id')) if space=='team' else None
    if space=='personal' and raw.get('team_id') is not None:raise ValueError('개인 자료함에는 팀 ID를 지정할 수 없습니다.')
    folder=identifier(raw.get('folder_id'),nullable=True)
    ids=raw.get('asset_ids',[])
    if not isinstance(ids,list) or len(ids)>100 or len(ids)!=len(set(ids)):raise ValueError('고정 파일은 중복 없이 최대 100개를 선택하세요.')
    ids=[identifier(value) for value in ids]
    if mode=='fixed' and not ids or mode!='fixed' and ids:raise ValueError('고정 모드에서만 사용할 파일 ID를 선택하세요.')
    result=dict(mode=mode,space=space,team_id=team,folder_id=folder,asset_ids=ids,target_node_id=raw.get('target_node_id'))
    kind=target_kind(agent,result)
    if kind=='table' and mode=='fixed' and len(ids)!=1:raise ValueError('표 읽기 작업에는 고정 파일 하나를 선택하세요.')
    return result


def reference(asset,space,team_id,folder_id):
    # Effective parsed/reviewed content participates in the version. Renaming or
    # moving a file does not silently turn old content into a different version.
    content={k:v for k,v in asset.items() if k not in ('name','created_at','updated_at','folder_id','original_path','original_bucket')}
    version=hashlib.sha256(encoded(content).encode()).hexdigest()
    return dict(id=asset['id'],version=version,name=asset.get('name',''),kind=asset.get('kind'),
                created_at=asset.get('created_at',''),updated_at=asset.get('updated_at') or asset.get('created_at',''),
                space=space,team_id=team_id,folder_id=folder_id)


def source_key(selection,refs):
    return hashlib.sha256(encoded({'space':selection['space'],'team_id':selection['team_id'],
                                  'refs':[(r['id'],r['version']) for r in refs]}).encode()).hexdigest()


def library_export(storage,selection,kind,refs=None,verify_only=False):
    library=AssetLibrary(storage)
    if selection['space']!=library.kind or selection.get('team_id')!=(library.owner_id if library.kind=='team' else None):
        raise ValueError('자료함 공간에 접근할 수 없습니다.')
    fixed=selection['mode']=='fixed'
    pinned=fixed or verify_only
    folder=selection.get('folder_id') if pinned else library.destination(selection.get('folder_id'))
    if refs is not None and (not isinstance(refs,list) or len(refs)>100):raise ValueError('선택한 파일 목록을 확인하세요.')
    requested=[identifier(ref.get('id')) for ref in refs] if refs is not None else list(selection.get('asset_ids',[])) if fixed else None
    if requested is not None and len(set(requested))!=len(requested):raise ValueError('같은 자료를 중복 선택할 수 없습니다.')
    params=[library.space_id]
    condition='' if pinned else ' AND '+('p.folder_id IS NULL' if folder is None else 'p.folder_id=?')
    if not pinned and folder is not None:params.append(folder)
    if requested is not None:
        condition+=' AND a.id IN ('+','.join('?' for _ in requested)+')' if requested else ' AND 1=0'
        params.extend(requested)
    rows=storage.db.execute('SELECT a.id,p.folder_id FROM builder_assets a LEFT JOIN library_asset_placements p ON {ws:p} AND p.asset_id=a.id '
        'WHERE {ws:a} AND (p.asset_id IS NULL OR p.space_id=?)'+condition,tuple(params)).fetchall()
    eligible={}
    for asset_id,parent in rows:
        try:asset=storage.asset(asset_id)
        except ValueError as error:
            if selection['mode']=='all':
                raise ValueError(f'폴더의 자료 {asset_id}를 읽을 수 없습니다. 삭제 상태와 접근 권한을 확인한 뒤 다시 실행하세요.') from error
            continue
        if not verify_only:
            from agent_builder.conditions.condition_readiness import input_issues
            compatible=supports(asset,kind);issues=input_issues({'resources':[asset]})
            if not compatible or issues:
                if selection['mode']=='all':
                    name=asset.get('name') or asset_id
                    reason='선택한 읽기 작업과 파일 종류가 다릅니다.' if not compatible else '파일 분석이 완료되지 않았거나 누락 범위 확인이 필요합니다.'
                    raise ValueError(f'폴더 전체 읽기에서 “{name}”을 처리할 수 없습니다. {reason} 파일을 확인하거나 다른 폴더로 옮긴 뒤 다시 실행하세요.')
                continue
            if selection['mode']=='all' and kind=='document' and next(chunks_for_asset(asset),None) is None:
                name=asset.get('name') or asset_id
                raise ValueError(f'폴더 전체 읽기에서 “{name}”에 읽을 본문이 없습니다. 파일의 분석 결과와 선택한 본문을 확인하거나 다른 폴더로 옮긴 뒤 다시 실행하세요.')
        ref=reference(asset,selection['space'],selection.get('team_id'),parent)
        # Metadata planning never retains every file's parsed body in memory.
        eligible[asset_id]=(asset if refs is not None else None,ref)
    if refs is None:
        candidates=[ref for _,ref in eligible.values()]
        if selection['mode']=='fixed':
            if any(id not in eligible for id in selection['asset_ids']):raise ValueError('선택한 고정 파일이 없거나 폴더·종류·처리 상태를 확인할 수 없습니다.')
            candidates=[eligible[id][1] for id in selection['asset_ids']]
        return {'candidates':candidates}
    assets=[];originals=[]
    for ref in refs:
        pair=eligible.get(ref.get('id'))
        if not pair:raise ValueError('고정된 원본 파일이 삭제되었거나 선택 폴더에서 접근할 수 없습니다.')
        asset,current=pair
        if not verify_only and current['version']!=ref.get('version'):raise ValueError('선택 후 파일 버전이 변경됐습니다. 다시 실행해 파일을 선택하세요.')
        if not verify_only:
            assets.append({'asset':asset,'rows':storage.rows(asset['id']),
                           'identity':{'asset_id':asset['id'],'version':current['version'],'space':selection['space'],
                                       'team_id':selection.get('team_id'),'folder_id':folder}})
        else:originals.append({'asset':{key:asset[key] for key in ('id','original_path','hash') if key in asset}})
    result={'assets':assets,'originals':originals,'refs':refs,'verified_only':bool(verify_only)}
    if assets and len(encoded(result).encode())>10*1024*1024:raise ValueError('선택한 자료의 파싱 결과가 큽니다. 파일을 나누거나 처리 범위를 줄여주세요.')
    return result


class SnapshotStorage:
    """A run-only parsed-input view. It never inserts into the personal library."""
    def __init__(self,storage,snapshot,agent):
        self.storage=storage;self.snapshot=snapshot
        self.items={input_id(snapshot['selection'],item):item for item in snapshot['assets']}
        if is_table_batch(snapshot,agent):
            from workflow_engine.executors.table_batch import merge_table_assets
            merged=merge_table_assets(snapshot['assets']);id=table_batch_id(snapshot)
            self.items[id]={'asset':{'id':id,'name':'폴더_통합표.csv','kind':'table','format':'csv',
                'columns':merged['columns'],'row_count':merged['row_count'],'profile':{},'text':'',
                'processing_status':'ready','hash':snapshot['source_key'],
                'source_asset_ids':merged['source_asset_ids']},'rows':merged['rows']}
        self.bound=bind_agent(storage,agent,snapshot)
        self.library_target_id=snapshot['selection']['target_node_id']
    def __getattr__(self,key):return getattr(self.storage,key)
    def agent(self,id):return deepcopy(self.bound) if id==self.bound['id'] else self.storage.agent(id)
    def asset(self,id):return {**deepcopy(self.items[id]['asset']),'id':id} if id in self.items else self.storage.asset(id)
    def raw_asset(self,id):return self.asset(id)
    def rows(self,id):return deepcopy(self.items[id]['rows']) if id in self.items else self.storage.rows(id)
    def require_assets(self,ids):return self.storage.require_assets([id for id in ids if id not in self.items])
    def bind_agent(self,agent):return bind_agent(self.storage,agent,self.snapshot)


def input_id(selection,item):
    identity=item['identity']
    return str(uuid5(NAMESPACE_URL,'a4b.source.input:'+encoded([selection['space'],selection.get('team_id'),identity['asset_id'],identity['version']])))


def is_table_batch(snapshot,agent):
    return snapshot['selection']['mode']=='all' and target_kind(agent,snapshot['selection'])=='table'


def table_batch_id(snapshot):
    return str(uuid5(NAMESPACE_URL,'a4b.source.table-batch:'+snapshot['source_key']))


def bind_agent(storage,agent,snapshot):
    result=deepcopy(agent);config=result['config'];selection=snapshot['selection']
    ids=[table_batch_id(snapshot)] if is_table_batch(snapshot,agent) else [input_id(selection,item) for item in snapshot['assets']]
    from workflow_engine.executors.sources import source_file_ids
    replaced=set();retained=set()
    for node in (config.get('workflow') or {}).get('nodes',[]):
        if node['tool'] not in ('read_table','read_document') or node['settings'].get('operation'):continue
        if node['id']==selection['target_node_id']:
            replaced.update(node['settings'].get('file_ids') or config.get('document_ids',[]))
            node['settings']['file_ids']=ids
        else:
            if not node['settings'].get('file_ids'):node['settings']['file_ids']=source_file_ids(node,[],storage,agent['config'])
            retained.update(node['settings']['file_ids'])
    # Every reader now has an explicit slot; original static references remain.
    config['document_ids']=list(dict.fromkeys([id for id in config.get('document_ids',[]) if id not in replaced or id in retained]+ids))
    return result


class LibrarySources:
    def __init__(self,storage):self.storage=storage;self.db=storage.db
    def get(self,agent_id):
        row=self.db.execute('SELECT json FROM builder_library_sources WHERE {ws} AND agent_id=?',(agent_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,agent_id,raw,base_revision):
        agent=self.storage.agent(agent_id);spec=normalized(raw,agent) if raw is not None else None
        if type(base_revision) is not int or base_revision<0:raise ValueError('자료함 설정의 기준 버전을 확인하세요.')
        self.storage.begin_write()
        try:
            row=self.db.execute('SELECT revision,json FROM builder_library_sources WHERE {ws} AND agent_id=?'+(' FOR UPDATE' if self.storage.dialect=='postgres' else ''),(agent_id,)).fetchone()
            old=json.loads(row[1]) if row else None;revision=row[0] if row else 0
            if (old['revision'] if old else 0)!=base_revision:raise ValueError('자료함 설정이 변경됐습니다. 새로고침 후 다시 저장하세요.')
            if old and spec=={k:v for k,v in old.items() if k!='revision'} or old is None and spec is None:
                self.db.commit();return old
            if spec is None:self.db.execute("UPDATE builder_library_sources SET revision=?,json='null',updated_at=? WHERE {ws} AND agent_id=? AND revision=?",(revision+1,now(),agent_id,revision))
            else:
                spec={**spec,'revision':revision+1}
                if row:
                    changed=self.db.execute('UPDATE builder_library_sources SET revision=?,json=?,updated_at=? WHERE {ws} AND agent_id=? AND revision=?',
                                            (revision+1,encoded(spec),now(),agent_id,revision))
                else:
                    changed=self.db.execute('INSERT INTO builder_library_sources({wsc}agent_id,revision,json,updated_at) VALUES({wsv}?,?,?,?) ON CONFLICT DO NOTHING',
                                            (agent_id,revision+1,encoded(spec),now()))
                if not changed.rowcount:raise ValueError('자료함 설정이 변경됐습니다. 새로고침 후 다시 저장하세요.')
            self.db.execute('UPDATE builder_schedules SET active=0 WHERE {ws} AND agent_id=?',(agent_id,))
            if spec is not None:self.db.execute('DELETE FROM builder_data_sources WHERE {ws} AND agent_id=?',(agent_id,))
            for job_id,body in self.db.execute("SELECT id,json FROM builder_schedule_jobs WHERE {ws} AND agent_id=? AND state='queued'",(agent_id,)).fetchall():
                job=json.loads(body)
                job.update(error='자료함 설정이 변경되어 대기 작업을 취소했습니다.',finished_at=now())
                self.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',json=? WHERE {ws} AND id=?",(encoded(job),job_id))
                self.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND job_id=? AND state='pending'",(now(),job_id))
            self.db.commit();return spec
        except Exception:self.db.rollback();raise
    def snapshot(self,run_id):
        row=self.db.execute('SELECT json FROM builder_source_snapshots WHERE {ws} AND run_id=?',(run_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def original_plan(self,run_id,asset_id):
        identifier(run_id);identifier(asset_id)
        run=self.storage.test(run_id);snapshot=self.snapshot(run_id)
        if not snapshot or not run.get('library_source') or run['library_source'].get('source_key')!=snapshot['source_key']:
            raise ValueError('이 실행에서 사용한 자료 원본을 찾을 수 없습니다.')
        for item,ref in zip(snapshot['assets'],snapshot['refs']):
            if input_id(snapshot['selection'],item)==asset_id:
                asset={key:item['asset'][key] for key in ('id','name','format','original_path','original_bucket','hash') if key in item['asset']}
                return {'selection':snapshot['selection'],'ref':ref,'asset':asset}
        raise ValueError('이 실행에서 사용한 자료 원본을 찾을 수 없습니다.')
    def plan(self,action,id):
        refs=None;run_id=None
        if action=='scheduled_run':
            row=self.db.execute('SELECT json FROM builder_schedule_jobs WHERE {ws} AND id=?',(id,)).fetchone()
            if not row:raise ValueError('예약 실행 작업을 찾을 수 없습니다.')
            job=json.loads(row[0]);agent=job['agent_snapshot'];bound=job.get('library_source')
            selection=bound['selection'] if bound else None;refs=bound.get('refs') if bound else None;run_id=id
        elif action in ('retry_run','resume_run'):
            run=self.storage.test(id);agent={'id':run['agent_id'],'config':run['config_snapshot']};meta=run.get('library_source')
            selection=meta['selection'] if meta else None;refs=meta.get('refs') if meta else None;run_id=id
        else:
            agent=self.storage.agent(id);selection=self.get(id)
        return {'selection':selection,'refs':refs,'run_id':run_id,'pinned_snapshot':bool(run_id and self.snapshot(run_id)),
                'needs_resolve':selection is not None and refs is None,
                'target_kind':target_kind(agent,selection) if selection else None}
    def choose(self,selection,candidates):
        if not isinstance(candidates,list) or len(candidates)>5000:raise ValueError('자료함 파일 목록이 너무 큽니다. 폴더를 나눠주세요.')
        unique={}
        for item in candidates:
            if not isinstance(item,dict) or not isinstance(item.get('version'),str) or not item['version']:raise ValueError('자료 버전을 확인할 수 없습니다.')
            identifier(item.get('id'))
            if item.get('space')!=selection['space'] or item.get('team_id')!=selection.get('team_id'):
                raise ValueError('선택한 자료함과 파일 목록의 공간이 다릅니다.')
            if selection['mode']!='fixed' and item.get('folder_id')!=selection.get('folder_id'):
                raise ValueError('선택한 폴더와 파일 목록이 다릅니다.')
            if item['id'] in unique:raise ValueError('자료 목록에 중복 파일이 있습니다.')
            unique[item['id']]=item
        if selection['mode']=='fixed':
            if any(id not in unique for id in selection['asset_ids']):raise ValueError('선택한 고정 파일을 찾을 수 없습니다.')
            return [unique[id] for id in selection['asset_ids']]
        def registered(item):
            stamp=item.get('created_at') or ''
            if not stamp:return (0,item['id'])
            try:return (datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp(),item['id'])
            except (TypeError,ValueError):raise ValueError('자료 등록 시각을 확인할 수 없습니다.')
        ordered=sorted(unique.values(),key=registered,reverse=True)
        if selection['mode']=='latest' and not ordered:raise ValueError('선택한 폴더에 실행할 수 있는 최신 파일이 없습니다.')
        if selection['mode']=='all':
            if not ordered:raise ValueError('선택한 폴더에 함께 읽을 파일이 없습니다. 파일을 올린 뒤 다시 실행하세요.')
            if len(ordered)>100:raise ValueError('폴더 전체 함께 읽기는 최대 100개 파일까지 지원합니다. 폴더를 나눈 뒤 다시 실행하세요.')
        return ordered[:1] if selection['mode']=='latest' else ordered
    def resolve(self,action,id,candidates,lease_token=None,run_id=None,expected_selection=None,use_schedule=False,request=''):
        plan=self.plan(action,id);selection=plan['selection']
        if expected_selection is not None and selection!=expected_selection:raise ValueError('자료함 설정이 변경됐습니다. 다시 실행하세요.')
        if not selection:return {'status':'resolved','selection':None,'refs':[]}
        if action=='test' and selection['mode']=='new':
            if not isinstance(request,str):raise ValueError('실행 요청은 텍스트로 입력하세요.')
            if request.strip():raise ValueError('새 파일마다 실행에는 추가 실행 조건을 적용할 수 없습니다. 조건을 워크플로우에 저장하거나 파일을 직접 선택해 실행하세요.')
        chosen=plan['refs'] if plan['refs'] is not None else self.choose(selection,candidates)
        if selection['mode']!='new' or action in ('schedule','schedule_test'):
            return {'status':'resolved','selection':selection,'refs':chosen[:1] if selection['mode']=='new' else chosen,'run_id':run_id or plan['run_id'] or uid()}
        if use_schedule:return self.expand(action,id,selection,chosen,lease_token,use_schedule=True)
        return self.expand(action,id,selection,chosen,lease_token)
    def expand(self,action,id,selection,refs,lease_token,use_schedule=False):
        self.storage.begin_write()
        try:
            if action=='scheduled_run':
                row=self.db.execute('SELECT json,state,lease_token FROM builder_schedule_jobs WHERE {ws} AND id=?',(id,)).fetchone()
                if not row or row[1]!='running' or row[2]!=lease_token:raise ValueError('예약 작업의 실행 소유권이 만료되었습니다.')
                parent=json.loads(row[0]);agent=parent['agent_snapshot'];due=parent['due_at'];parent_id=id
                if self.get(agent['id'])!=selection:
                    parent.update(message='자료함 설정이 변경되어 이전 대기 작업을 취소했습니다.',finished_at=now())
                    self.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',json=?,lease_until=NULL WHERE {ws} AND id=?",(encoded(parent),id))
                    self.db.commit();return {'status':'skipped','enqueued_count':0,'message':parent['message']}
            else:
                # The plan/candidate export precedes this write transaction. A
                # concurrent settings save may have replaced or removed it.
                # Check again while holding the same workspace lock as save().
                if self.get(id)!=selection:raise ValueError('자료함 설정이 변경됐습니다. 다시 실행하세요.')
                agent=self.storage.agent(id)
                if use_schedule:
                    from workflow_engine.scheduling.schedules import scheduled_agent
                    row=self.db.execute('SELECT json FROM builder_schedules WHERE {ws} AND agent_id=?',(id,)).fetchone()
                    reservation=json.loads(row[0]) if row else None
                    if not reservation or reservation.get('removed'):raise ValueError('예약 설정을 먼저 저장하세요.')
                    if reservation['agent_version']!=agent['version']:raise ValueError('워크플로우가 변경됐습니다. 예약 설정을 저장해 적용할 버전을 확인하세요.')
                    agent=scheduled_agent(agent,reservation['spec'])
                due=now();parent_id=uid()
                parent={'id':parent_id,'agent_id':id,'agent_snapshot':agent,'kind':'manual','due_at':due,'created_at':due}
            jobs=[]
            for ref in refs:
                key=source_key(selection,[ref])
                old=self.db.execute('SELECT job_id,state FROM builder_source_processed WHERE {ws} AND agent_id=? AND revision=? AND source_key=?',
                                    (agent['id'],selection['revision'],key)).fetchone()
                if old:
                    if old[1]=='failed':
                        changed=self.db.execute("UPDATE builder_source_processed SET state='pending',updated_at=? WHERE {ws} AND agent_id=? AND revision=? AND source_key=? AND state='failed'",(now(),agent['id'],selection['revision'],key))
                        if changed.rowcount:
                            requeued=self.db.execute("UPDATE builder_schedule_jobs SET state='queued',lease_token=NULL,lease_until=NULL WHERE {ws} AND id=? AND state IN ('failed','cancelled')",(old[0],))
                            if requeued.rowcount:jobs.append(old[0])
                            else:self.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND agent_id=? AND revision=? AND source_key=?",(now(),agent['id'],selection['revision'],key))
                    continue
                child_id=uid()
                inserted=self.db.execute("INSERT INTO builder_source_processed({wsc}agent_id,revision,source_key,job_id,state,updated_at) VALUES({wsv}?,?,?,?,'pending',?) ON CONFLICT DO NOTHING",
                                         (agent['id'],selection['revision'],key,child_id,now()))
                if not inserted.rowcount:continue
                child={'id':child_id,'agent_id':agent['id'],'agent_snapshot':agent,'kind':parent['kind'],'due_at':due,'created_at':now(),
                       'parent_job_id':parent_id,'source_name':ref.get('name',''),'source_version':ref['version'],
                       'library_source':{'selection':selection,'refs':[ref],'source_key':key}}
                self.db.execute("INSERT INTO builder_schedule_jobs({wsc}id,agent_id,slot_key,state,lease_until,lease_token,json) VALUES({wsv}?,?,?,'queued',NULL,NULL,?)",(child_id,agent['id'],'source:'+child_id,encoded(child)))
                jobs.append(child_id)
            status='expanded' if jobs else 'skipped';message=f'{len(jobs)}개 파일을 실행 대기열에 넣었습니다.' if jobs else '성공 처리하지 않은 새 파일이 없습니다.'
            parent.update(enqueued_count=len(jobs),child_job_ids=jobs,message=message,finished_at=now())
            if action=='scheduled_run':self.db.execute('UPDATE builder_schedule_jobs SET state=?,json=?,lease_until=NULL WHERE {ws} AND id=?',(status,encoded(parent),parent_id))
            else:self.db.execute('INSERT INTO builder_schedule_jobs({wsc}id,agent_id,slot_key,state,lease_until,lease_token,json) VALUES({wsv}?,?,?,?,NULL,NULL,?)',(parent_id,agent['id'],'manual:'+parent_id,status,encoded(parent)))
            self.db.commit();return {'status':status,'enqueued_count':len(jobs),'job_ids':jobs,'message':message}
        except Exception:self.db.rollback();raise
    def stage(self,run_id,agent,payload):
        old=self.snapshot(run_id) if run_id else None
        if old:
            if payload.get('selection')!=old['selection'] or source_key(old['selection'],payload.get('refs',[]))!=old['source_key']:
                raise ValueError('재시도할 자료 스냅샷이 원래 실행과 다릅니다.')
            return SnapshotStorage(self.storage,old,agent)
        selection=payload['selection'];refs=payload.get('refs',[]);assets=payload.get('assets',[])
        kind=target_kind(agent,selection)
        if selection['mode']=='fixed' and [ref['id'] for ref in refs]!=selection['asset_ids']:raise ValueError('고정 파일 선택이 실행 설정과 다릅니다.')
        if selection['mode']=='latest' and len(refs)!=1 or selection['mode']=='new' and len(refs)>1:raise ValueError('최신·새 파일은 실행마다 파일 하나를 고정합니다.')
        if selection['mode']=='all' and not 1<=len(refs)<=100:raise ValueError('폴더 전체 함께 읽기에는 1~100개 파일이 필요합니다.')
        if len(assets)!=len(refs):raise ValueError('선택한 자료의 고정 스냅샷이 필요합니다.')
        for item,ref in zip(assets,refs):
            identity=item.get('identity',{})
            if (identity.get('asset_id')!=ref['id'] or identity.get('version')!=ref['version'] or item['asset']['id']!=ref['id']
                    or identity.get('space')!=selection['space'] or identity.get('team_id')!=selection.get('team_id')
                    or identity.get('folder_id')!=selection.get('folder_id')):
                raise ValueError('자료 스냅샷의 공간·파일·버전이 일치하지 않습니다.')
            if not isinstance(item.get('rows'),list):raise ValueError('자료 스냅샷 형식을 확인하세요.')
            if not supports(item['asset'],kind):raise ValueError('선택한 자료와 읽기 작업의 종류가 다릅니다.')
        snapshot={'selection':selection,'refs':refs,'assets':assets,'source_key':source_key(selection,refs)}
        # Validate a combined table before persisting a run snapshot, so an
        # incompatible file cannot leave a pending report behind.
        view=SnapshotStorage(self.storage,snapshot,agent)
        if run_id:
            self.db.execute("INSERT INTO builder_source_snapshots({wsc}run_id,agent_id,source_key,json,state,updated_at) VALUES({wsv}?,?,?,?,'pending',?) ON CONFLICT DO NOTHING",(run_id,agent['id'],snapshot['source_key'],encoded(snapshot),now()));self.db.commit()
            persisted=self.snapshot(run_id)
            if persisted['selection']!=selection or persisted['source_key']!=source_key(selection,refs):raise ValueError('실행의 고정 자료가 다른 요청에서 변경됐습니다.')
            if persisted!=snapshot:return SnapshotStorage(self.storage,persisted,agent)
        return view
    def execution_storage(self,data):
        action=data['action'];id=data['id'];plan=self.plan(action,id);payload=data['library_source_snapshot']
        if not plan['selection'] or payload.get('selection')!=plan['selection']:raise ValueError('자료함 설정이 실행 전에 변경됐습니다. 다시 실행하세요.')
        if plan['refs'] is not None and source_key(plan['selection'],plan['refs'])!=source_key(plan['selection'],payload.get('refs',[])):
            raise ValueError('예약된 파일 선택이 바뀌었습니다.')
        if action=='scheduled_run':
            row=self.db.execute('SELECT json FROM builder_schedule_jobs WHERE {ws} AND id=?',(id,)).fetchone();agent=json.loads(row[0])['agent_snapshot'];run_id=id
        elif action in ('retry_run','resume_run'):
            run=self.storage.test(id);agent={'id':run['agent_id'],'version':run['agent_version'],'model':run['model'],'credential_id':run['credential_id'],
                                           'config':run['config_snapshot'],'workflow_dependencies':run.get('workflow_dependencies',{})};run_id=id
        else:agent=self.storage.agent(id);run_id=data.get('run_id') if action=='test' else None
        if action=='test' and not run_id:raise ValueError('자료함 실행의 고정 실행 ID가 필요합니다.')
        return self.stage(run_id,agent,payload)
    def complete(self,run,commit=True):
        meta=run.get('library_source')
        if not meta:return
        state='succeeded' if run['status']=='succeeded' else 'failed' if run['status']=='failed' else 'pending'
        terminal_guard=" AND state<>'succeeded'" if state!='succeeded' else ''
        self.db.execute('UPDATE builder_source_snapshots SET state=?,updated_at=? WHERE {ws} AND run_id=?'+terminal_guard,(state,now(),run['id']))
        selection=meta['selection']
        self.db.execute('UPDATE builder_source_processed SET state=?,updated_at=? WHERE {ws} AND agent_id=? AND revision=? AND source_key=? AND job_id=?'+terminal_guard,
                        (state,now(),run['agent_id'],selection['revision'],meta['source_key'],run['id']))
        if commit:self.db.commit()
    def failed_job(self,id,commit=True):
        self.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND job_id=? AND state='pending'",(now(),id))
        self.db.execute("UPDATE builder_source_snapshots SET state='failed',updated_at=? WHERE {ws} AND run_id=? AND state='pending'",(now(),id))
        if commit:self.db.commit()


def source_metadata(storage):
    snapshot=getattr(storage,'snapshot',None)
    if not isinstance(snapshot,dict):return None
    return {key:snapshot[key] for key in ('selection','refs','source_key')}


def source_tags(storage):
    meta=source_metadata(storage)
    if not meta:return {}
    selection=meta['selection']
    snapshot=storage.snapshot
    return {'library_source':meta,'library_source_files':[{'space':selection['space'],'team_id':selection.get('team_id'),
            'asset_id':ref['id'],'execution_asset_id':input_id(selection,item),'name':ref.get('name',''),'version':ref['version']}
            for ref,item in zip(meta['refs'],snapshot['assets'])]}
