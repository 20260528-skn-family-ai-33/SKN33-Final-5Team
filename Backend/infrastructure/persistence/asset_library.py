"""Personal and team namespaces and logical placement, independent of file paths.

Only the authenticated server chooses the workspace and space. The Node API
checks team membership before selecting a team directory for a worker.
"""
from contextlib import contextmanager
import re,json
import unicodedata
from uuid import NAMESPACE_URL, uuid5
from infrastructure.persistence.ids import uid, now

LIBRARY_SCHEMA='''
CREATE TABLE IF NOT EXISTS library_spaces(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('personal','team')),
 owner_id TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(kind,owner_id));
CREATE TABLE IF NOT EXISTS library_folders(id TEXT PRIMARY KEY,space_id TEXT NOT NULL REFERENCES library_spaces(id),
 parent_id TEXT,parent_key TEXT NOT NULL,name TEXT NOT NULL,name_key TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(space_id,id),UNIQUE(space_id,parent_key,name_key),CHECK(parent_key=COALESCE(parent_id,'')),
 FOREIGN KEY(space_id,parent_id) REFERENCES library_folders(space_id,id));
CREATE INDEX IF NOT EXISTS library_folders_parent ON library_folders(space_id,parent_id);
CREATE TABLE IF NOT EXISTS library_asset_placements(asset_id TEXT PRIMARY KEY REFERENCES builder_assets(id) ON DELETE CASCADE,
 space_id TEXT NOT NULL REFERENCES library_spaces(id),folder_id TEXT,updated_at TEXT NOT NULL,
 FOREIGN KEY(space_id,folder_id) REFERENCES library_folders(space_id,id));
CREATE INDEX IF NOT EXISTS library_placements_folder ON library_asset_placements(space_id,folder_id);
'''
_UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',re.I)

def identifier(value,nullable=False):
    if value is None and nullable:return None
    if not isinstance(value,str) or not _UUID.fullmatch(value):raise ValueError('자료 또는 폴더 ID를 확인해주세요.')
    return value.lower()

def folder_name(value):
    if not isinstance(value,str) or any(unicodedata.category(c).startswith('C') or c in '/\\\u2028\u2029' for c in value):
        raise ValueError('폴더 이름은 경로 기호 없이 한 줄로 입력해주세요.')
    value=unicodedata.normalize('NFC',value.strip())
    if not 1<=len(value)<=80 or value in ('.','..'):raise ValueError('폴더 이름을 1~80자로 입력해주세요.')
    return value

class AssetLibrary:
    def __init__(self,storage):
        self.storage=storage;self.db=storage.db
        self.kind='team' if storage.workspace_id.startswith('team:') else 'personal'
        self.owner_id=storage.workspace_id[5:] if self.kind=='team' else storage.workspace_id
        self.name='팀 자료' if self.kind=='team' else '내 자료'
        self.space_id=str(uuid5(NAMESPACE_URL,'a4b.library.'+self.kind+':'+self.owner_id))

    @property
    def space(self):return dict(id=self.space_id,kind=self.kind,name=self.name)

    @contextmanager
    def write(self):
        self.storage.begin_write()
        try:
            self.db.execute('INSERT INTO library_spaces({wsc}id,kind,owner_id,name,created_at) VALUES({wsv}?,?,?,?,?) '
                            'ON CONFLICT({wsc}id) DO NOTHING',(self.space_id,self.kind,self.owner_id,self.name,now()))
            yield
            self.db.commit()
        except Exception:self.db.rollback();raise

    def folder(self,id):
        id=identifier(id)
        row=self.db.execute('SELECT id,name,parent_id FROM library_folders WHERE {ws} AND space_id=? AND id=?',(self.space_id,id)).fetchone()
        if not row:raise ValueError('접근할 수 있는 폴더를 찾을 수 없습니다.')
        return dict(zip(('id','name','parent_id'),row))

    def destination(self,id):
        id=identifier(id,nullable=True)
        if id is not None:self.folder(id)
        return id

    def folders(self):
        return [dict(zip(('id','name','parent_id'),row)) for row in self.db.execute(
            'SELECT id,name,parent_id FROM library_folders WHERE {ws} AND space_id=? ORDER BY name_key,id',(self.space_id,))]

    def unique_name(self,name,parent_id,exclude=None):
        row=self.db.execute('SELECT id FROM library_folders WHERE {ws} AND space_id=? AND parent_key=? AND name_key=?',
                            (self.space_id,parent_id or '',name.casefold())).fetchone()
        if row and row[0]!=exclude:raise ValueError('같은 위치에 이름이 같은 폴더가 있습니다.')

    def create_folder(self,name,parent_id=None):
        name=folder_name(name)
        with self.write():
            parent_id=self.destination(parent_id);self.unique_name(name,parent_id)
            id=uid();stamp=now()
            self.db.execute('INSERT INTO library_folders({wsc}id,space_id,parent_id,parent_key,name,name_key,created_at,updated_at) '
                            'VALUES({wsv}?,?,?,?,?,?,?,?)',(id,self.space_id,parent_id,parent_id or '',name,name.casefold(),stamp,stamp))
            result=self.folder(id)
        return result

    def update_folder(self,id,*,name=None,parent_id=None,move=False):
        id=identifier(id)
        if not move:name=folder_name(name)
        with self.write():
            existing=self.folder(id)
            destination=self.destination(parent_id) if move else existing['parent_id']
            if move:
                current=destination;seen={id}
                while current is not None:
                    if current in seen:raise ValueError('폴더를 자기 자신이나 하위 폴더로 옮길 수 없습니다.')
                    seen.add(current);current=self.folder(current)['parent_id']
                name=existing['name']
            self.unique_name(name,destination,id)
            self.db.execute('UPDATE library_folders SET name=?,name_key=?,parent_id=?,parent_key=?,updated_at=? '
                            'WHERE {ws} AND space_id=? AND id=?',(name,name.casefold(),destination,destination or '',now(),self.space_id,id))
            result=self.folder(id)
        return result

    def delete_folder(self,id):
        id=identifier(id)
        with self.write():
            self.folder(id)
            if self.db.execute('SELECT 1 FROM library_folders WHERE {ws} AND space_id=? AND parent_id=? LIMIT 1',(self.space_id,id)).fetchone():
                raise ValueError('하위 폴더가 있습니다. 빈 폴더만 삭제할 수 있습니다.')
            active=self.db.execute('SELECT 1 FROM library_asset_placements p JOIN builder_assets a ON {ws:a} AND a.id=p.asset_id '
                'WHERE {ws:p} AND p.space_id=? AND p.folder_id=? AND '+self.storage.json_text('a.json','deleted')+' IS NULL AND '+
                self.storage.json_text('a.json','pending_delete')+' IS NULL LIMIT 1',(self.space_id,id)).fetchone()
            if active:raise ValueError('자료가 있습니다. 빈 폴더만 삭제할 수 있습니다.')
            # Immutable workflow tombstones are not active files and must not hold an empty folder forever.
            self.db.execute('DELETE FROM library_asset_placements WHERE {ws} AND space_id=? AND folder_id=?',(self.space_id,id))
            self.db.execute('DELETE FROM library_folders WHERE {ws} AND space_id=? AND id=?',(self.space_id,id))
        return dict(deleted=True)

    def _place(self,id,folder_id):
        self.db.execute('INSERT INTO library_asset_placements({wsc}asset_id,space_id,folder_id,updated_at) VALUES({wsv}?,?,?,?) '
                        'ON CONFLICT({wsc}asset_id) DO UPDATE SET space_id=excluded.space_id,folder_id=excluded.folder_id,updated_at=excluded.updated_at',
                        (id,self.space_id,folder_id,now()))

    def upload(self,asset,rows,folder_id=None):
        if not isinstance(asset,dict) or not isinstance(rows,list):raise ValueError('업로드한 자료 형식을 확인해주세요.')
        if any(k in asset for k in ('deleted','pending_delete','space_id','user_id','owner_id','team_id','workspace_id')):
            raise ValueError('업로드 자료의 소유 공간이나 삭제 상태를 지정할 수 없습니다.')
        if not isinstance(asset.get('name'),str) or not asset['name'].strip():raise ValueError('자료 이름이 필요합니다.')
        saved={k:v for k,v in asset.items() if k not in ('id','created_at','folder_id')};saved.update(id=uid(),created_at=now())
        with self.write():
            folder_id=self.destination(folder_id)
            self.storage.add_asset(saved,rows,commit=False);self._place(saved['id'],folder_id)
        return {**saved,'folder_id':folder_id}

    def move_asset(self,id,folder_id=None):
        id=identifier(id)
        with self.write():
            asset=self.storage.raw_asset(id);folder_id=self.destination(folder_id)
            placement=self.db.execute('SELECT space_id FROM library_asset_placements WHERE {ws} AND asset_id=?',(id,)).fetchone()
            if placement and placement[0]!=self.space_id:raise ValueError('이 자료를 이동할 수 있는 권한이 없습니다.')
            self._place(id,folder_id)
        return {**asset,'folder_id':folder_id}

    def listing(self,folder_id=None,before=None,q=None):
        folder_id=self.destination(folder_id)
        if before is not None and (type(before) is not int or not 1<=before<=9007199254740991):raise ValueError('자료 목록의 페이지 위치를 확인해주세요.')
        if q is not None and (not isinstance(q,str) or len(q)>200 or '\0' in q):raise ValueError('검색어는 200자 이내로 입력해주세요.')
        fields=('name','format','created_at','size_bytes','original_path','pending_delete','processing_status','kind','input_kinds')
        columns=','.join(self.storage.json_text('a.json',field) for field in fields)
        params=[self.space_id];destination='p.folder_id IS NULL' if folder_id is None else 'p.folder_id=?'
        if folder_id is not None:params.append(folder_id)
        bound=''
        if before is not None:bound+=' AND a.{seq}<?';params.append(before)
        if q and q.strip():
            bound+=' AND LOWER('+self.storage.json_text('a.json','name')+") LIKE ? ESCAPE '!'"
            term=q.strip().lower().replace('!','!!').replace('%','!%').replace('_','!_');params.append('%'+term+'%')
        rows=self.db.execute('SELECT a.{seq},a.id,'+columns+',p.folder_id FROM builder_assets a '
            'LEFT JOIN library_asset_placements p ON {ws:p} AND p.asset_id=a.id '
            'WHERE {ws:a} AND (p.asset_id IS NULL OR p.space_id=?) AND '+destination+' AND ('+
            self.storage.json_text('a.json','deleted')+' IS NULL OR '+self.storage.json_text('a.json','pending_delete')+' IS NOT NULL)'+bound+
            ' ORDER BY a.{seq} DESC LIMIT 51',tuple(params)).fetchall()
        from service.handlers.uploads import asset_usages
        usages=asset_usages(self.storage,[row[1] for row in rows[:50]])
        assets=[dict(id=id,name=name,format=fmt,created_at=created,size_bytes=int(size) if size is not None else None,
                     has_original=bool(original),pending_delete=bool(pending),processing_status=status,usages=usages[id],folder_id=parent,
                     kind=kind,input_kinds=json.loads(kinds) if kinds else [kind] if kind else [])
                for _,id,name,fmt,created,size,original,pending,status,kind,kinds,parent in rows[:50]]
        return dict(space=self.space,folder_id=folder_id,folders=self.folders(),assets=assets,next=str(rows[49][0]) if len(rows)>50 else None)
