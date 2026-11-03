"""Business-data connections for one workspace.

A4B_STORAGE=sqlite (default): one agent-builder.sqlite per workspace folder, as before.
A4B_STORAGE=postgres: shared a4b_builder schema. Every row carries workspace_id, every
statement filters on it, and FORCE ROW LEVEL SECURITY rejects any statement that does not.

SQL is written once, in SQLite form, with these markers:
  {ws}    workspace filter: `workspace_id=%s` (PostgreSQL) or `1=1` (SQLite)
  {ws:t}  the same filter on table alias t
  {wsc}   workspace column at the start of an INSERT column list or ON CONFLICT target
  {wsv}   its value at the start of the matching VALUES list
  {seq}   insertion order column: seq (PostgreSQL) or rowid (SQLite)
"""
import functools, ipaddress, os, re, socket, sqlite3, threading, time
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

SCHEMA='a4b_builder'
# Latest infrastructure/db/migrations version the code needs. Raise with each new migration.
REQUIRED_SCHEMA_VERSION=2
LOOPBACK={'localhost','127.0.0.1','::1'}
WORKSPACE=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',re.I)
_WRITE=re.compile(r'\s*(INSERT|UPDATE|DELETE)\b',re.I)
_TOKEN=re.compile(r"'(?:[^']|'')*'|\?|%|\{ws(?::(\w+))?\}|\{wsc\}|\{wsv\}|\{seq\}")


def storage_kind(env=None):
    env=os.environ if env is None else env
    kind=(env.get('A4B_STORAGE') or 'sqlite').strip()
    if kind not in ('sqlite','postgres'):raise ValueError('A4B_STORAGE는 sqlite 또는 postgres로 설정하세요.')
    public=(env.get('EASYFLOW_PUBLIC_URL') or '').strip()
    # Same rule as the SQLite member store: a public server must keep data in RDS.
    if kind=='sqlite' and public and (urlsplit(public).hostname or '') not in LOOPBACK:
        raise ValueError('SQLite 업무 데이터 저장소는 로컬 개발에서만 사용할 수 있습니다. 운영에서는 A4B_STORAGE=postgres를 설정하세요.')
    return kind


def workspace_id(directory):
    """data/users/<member ID>/ is that member's workspace; any other folder is 'local'."""
    path=Path(directory).resolve()
    return path.name.lower() if path.parent.name=='users' and WORKSPACE.fullmatch(path.name) else 'local'


def enable_wal(connection, timeout=10, io_timeout=2):
    # SQLite can return BUSY immediately while concurrent first connections
    # switch journal mode, even when connect(timeout=...) has a busy timeout.
    # On Windows a just-killed worker can still hold the -shm lock for a moment,
    # which surfaces as IOERR; retry that briefly so a real disk fault still raises.
    started=time.monotonic();deadline=started+timeout
    while True:
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            return
        except sqlite3.OperationalError as error:
            code=getattr(error,'sqlite_errorcode',0)&0xff
            busy=code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)
            io=code==sqlite3.SQLITE_IOERR and time.monotonic()<started+io_timeout
            if not (busy or io) or time.monotonic()>=deadline:
                raise
            time.sleep(min(.05,max(0,deadline-time.monotonic())))


@functools.lru_cache(maxsize=512)
def sqlite_sql(sql):
    return re.sub(r'\{ws(?::\w+)?\}','1=1',sql).replace('{wsc}','').replace('{wsv}','').replace('{seq}','rowid')


@functools.lru_cache(maxsize=512)
def postgres_sql(sql):
    """Returns (query, slots): each slot is 'p' for a caller parameter or 'w' for workspace_id."""
    slots=[]
    def replace(match):
        token=match.group(0)
        if token.startswith("'"):return token.replace('%','%%')
        if token=='%':return '%%'
        if token=='?':slots.append('p');return '%s'
        if token=='{wsc}':return 'workspace_id,'
        if token=='{wsv}':slots.append('w');return '%s,'
        if token=='{seq}':return 'seq'
        slots.append('w');return (match.group(1)+'.' if match.group(1) else '')+'workspace_id=%s'
    return _TOKEN.sub(replace,sql),tuple(slots)


def bind(slots,params,workspace):
    params=list(params);values=[];index=0
    for slot in slots:
        if slot=='w':values.append(workspace)
        else:values.append(params[index]);index+=1
    if index!=len(params):raise ValueError('SQL 자리표시자와 값의 개수가 다릅니다.')
    return values


class SqliteDatabase:
    dialect='sqlite'
    def __init__(self,path):
        self.path=path
        self.connection=sqlite3.connect(path,timeout=10,check_same_thread=False)
        try:enable_wal(self.connection)
        except Exception:
            self.connection.close()
            raise
        self.connection.execute('PRAGMA foreign_keys=ON')
    def execute(self,sql,params=()):return self.connection.execute(sqlite_sql(sql),params)
    def executescript(self,script):self.connection.executescript(script)
    def begin_write(self):self.connection.execute('BEGIN IMMEDIATE')
    def table_exists(self,name):
        return bool(self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
    def commit(self):self.connection.commit()
    def rollback(self):self.connection.rollback()
    def close(self):self.connection.close()


def postgres_conninfo(env=None):
    """libpq settings with the same TLS rule as auth-users.ts postgresTls: always verify the server."""
    from psycopg.conninfo import conninfo_to_dict
    env=os.environ if env is None else env
    url=(env.get('A4B_DATABASE_URL') or '').strip()
    if not url:raise ValueError('A4B_DATABASE_URL 설정이 필요합니다.')
    parts=urlsplit(url)
    if parts.scheme not in ('postgres','postgresql') or any(key.lower().startswith('ssl') for key,_ in parse_qsl(parts.query)):
        raise ValueError('A4B_DATABASE_URL에는 PostgreSQL 주소를 넣고 SSL 옵션은 제외하세요. TLS 인증서 검증은 서버가 설정합니다.')
    params=conninfo_to_dict(url)
    expected=(env.get('A4B_DATABASE_TLS_SERVER_NAME') or '').strip()
    if expected and (not re.fullmatch(r'[a-z0-9.-]+',expected,re.I) or _is_ip(expected) or '.' not in expected):
        raise ValueError('A4B_DATABASE_TLS_SERVER_NAME에는 인증서에서 확인할 실제 DB 호스트 이름을 넣으세요.')
    ca=(env.get('A4B_DATABASE_CA_FILE') or '').strip()
    params.update(sslmode='verify-full',sslrootcert=ca or 'system')
    if expected:
        # Connect to the tunnel address, but check the certificate (and send SNI) for the RDS name.
        host=params.get('host') or 'localhost'
        params['hostaddr']=host if _is_ip(host) else socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)[0][4][0]
        params['host']=expected
    params.setdefault('connect_timeout','5')
    params.setdefault('application_name','a4b-builder')
    params['options']=(params.get('options','')+' -c search_path='+SCHEMA).strip()
    return params


def _is_ip(value):
    try:ipaddress.ip_address(value.strip('[]'));return True
    except ValueError:return False


def _pool_size(name,default):
    raw=os.environ.get(name,str(default))
    if not raw.isdigit() or not 1<=int(raw)<=20:raise ValueError(name+'는 1~20 사이로 설정하세요.')
    return int(raw)


_pools={};_checked=set();_pool_lock=threading.Lock();_test_conninfo=None


def use_conninfo(conninfo):
    """Tests point the pools at a disposable database without the production TLS rule."""
    global _test_conninfo
    close_pools();_test_conninfo=conninfo


def close_pools():
    with _pool_lock:
        for pool in _pools.values():pool.close()
        _pools.clear();_checked.clear()


def _text_json(data):
    # Keep stored JSON text verbatim; callers json.loads() it exactly as with SQLite.
    return bytes(data).decode() if isinstance(data,(bytes,bytearray,memoryview)) else data


def _check_server(connection):
    row=connection.execute("SELECT r.rolsuper OR r.rolbypassrls,to_regclass('"+SCHEMA+".schema_migrations') IS NOT NULL FROM pg_roles r WHERE r.rolname=current_user").fetchone()
    if not row or row[0]:
        raise RuntimeError('업무 데이터 DB 계정이 행 수준 보안을 우회할 수 있습니다. superuser나 BYPASSRLS가 아닌 앱 전용 계정으로 접속하세요.')
    version=connection.execute('SELECT max(version) FROM '+SCHEMA+'.schema_migrations').fetchone()[0] if row[1] else None
    if version is None or version<REQUIRED_SCHEMA_VERSION:
        raise RuntimeError(f'업무 데이터 DB 스키마가 최신이 아닙니다(필요 {REQUIRED_SCHEMA_VERSION}, 현재 {version}). scripts/apply-builder-migrations.py로 마이그레이션을 먼저 적용하세요.')


def pool(role):
    """One small pool per worker process and role. Node starts a process per job, so the
    process count (A4B_WORKER_MAX_CONCURRENCY) bounds connections, not these pools."""
    with _pool_lock:
        if role in _pools:return _pools[role]
        from psycopg_pool import ConnectionPool
        from psycopg.types.json import set_json_loads
        def configure(connection):
            if role=='storage':set_json_loads(_text_json,connection)
        def reset(connection):connection.execute('RESET a4b.workspace_id')
        size=_pool_size('A4B_DATABASE_POOL_MAX',1)
        conninfo=_test_conninfo or postgres_conninfo()
        if isinstance(conninfo,dict):
            from psycopg.conninfo import make_conninfo
            conninfo=make_conninfo(**conninfo)
        _pools[role]=ConnectionPool(conninfo,min_size=0,max_size=size,open=True,configure=configure,reset=reset,
                                    kwargs={'autocommit':True,'prepare_threshold':0 if role=='checkpoint' else None},
                                    timeout=float(os.environ.get('A4B_DATABASE_POOL_TIMEOUT_S','10')),name='a4b-'+role)
        return _pools[role]


def checkout(role,workspace):
    pool_=pool(role);connection=pool_.getconn()
    try:
        # Checked on a real connection so a wrong account or schema fails with its own message.
        if role not in _checked:_check_server(connection);_checked.add(role)
        connection.execute("SELECT set_config('a4b.workspace_id',%s,false)",(workspace,))
    except Exception:
        pool_.putconn(connection);raise
    return pool_,connection


class PostgresDatabase:
    """Mirrors Python sqlite3's default transactions: DML opens one, commit() ends it."""
    dialect='postgres'
    def __init__(self,workspace):
        self.workspace_id=workspace
        self._pool,self.connection=checkout('storage',workspace)
        self.in_transaction=False
    def _begin(self):
        self.connection.execute('BEGIN');self.in_transaction=True
    def execute(self,sql,params=()):
        query,slots=postgres_sql(sql)
        values=bind(slots,params,self.workspace_id)
        if not self.in_transaction and _WRITE.match(sql):self._begin()
        cursor=self.connection.cursor();cursor.execute(query,values)
        return cursor
    def executescript(self,script):
        raise RuntimeError('PostgreSQL 스키마는 infrastructure/db/migrations로만 바꿉니다.')
    def begin_write(self):
        # SQLite BEGIN IMMEDIATE takes the file's single write lock. The workspace advisory
        # lock gives the same serialization, scoped to one member's data.
        if not self.in_transaction:self._begin()
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('a4b_builder:'||%s,0))",(self.workspace_id,))
    def table_exists(self,name):return True
    def commit(self):
        if self.in_transaction:
            self.in_transaction=False;self.connection.execute('COMMIT')
    def rollback(self):
        if self.in_transaction:
            self.in_transaction=False;self.connection.execute('ROLLBACK')
    def close(self):
        if self.connection is None:return
        try:self.rollback()
        finally:
            self._pool.putconn(self.connection);self.connection=None


def open_database(path,workspace):
    return PostgresDatabase(workspace) if storage_kind()=='postgres' else SqliteDatabase(path)
