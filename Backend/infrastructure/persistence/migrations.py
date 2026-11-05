"""Explicit PostgreSQL schema migrations for the business data (never run at app start)."""
from pathlib import Path
from infrastructure.persistence.db import REQUIRED_SCHEMA_VERSION, SCHEMA

# The repository root in development; /app in the Docker image (see Dockerfile).
DIRECTORY=Path(__file__).resolve().parents[3]/'infrastructure'/'db'/'migrations'
# version, file, step that must run first (LangGraph creates its own checkpoint tables).
MIGRATIONS=[(1,'001_builder_storage.sql',None),(2,'002_langgraph_checkpoints.sql','langgraph_setup'),
            (3,'003_sqlite_imports.sql',None),(4,'004_generation_requests.sql',None)]
assert MIGRATIONS[-1][0]==REQUIRED_SCHEMA_VERSION


def applied_versions(connection):
    if connection.execute(f"SELECT to_regclass('{SCHEMA}.schema_migrations')").fetchone()[0] is None:return set()
    return {row[0] for row in connection.execute(f'SELECT version FROM {SCHEMA}.schema_migrations')}


def pending(conninfo):
    import psycopg
    with psycopg.connect(conninfo,autocommit=True) as connection:
        done=applied_versions(connection)
    return [(version,name) for version,name,_ in MIGRATIONS if version not in done]


def _langgraph_setup(conninfo):
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver
    # setup() needs autocommit (CREATE INDEX CONCURRENTLY) and creates unqualified tables,
    # so search_path must point at the business schema.
    with psycopg.connect(conninfo,autocommit=True,row_factory=dict_row,options='-c search_path='+SCHEMA) as connection:
        PostgresSaver(connection).setup()


def apply(conninfo,log=print):
    """Applies each pending migration in its own transaction. Safe to rerun."""
    import psycopg
    result=[]
    for version,name,before in MIGRATIONS:
        with psycopg.connect(conninfo,autocommit=True) as connection:
            if version in applied_versions(connection):continue
        if before=='langgraph_setup':_langgraph_setup(conninfo)
        sql=(DIRECTORY/name).read_text(encoding='utf-8')
        with psycopg.connect(conninfo) as connection:
            with connection.transaction():
                connection.execute(sql)
                connection.execute(f'INSERT INTO {SCHEMA}.schema_migrations(version,name) VALUES(%s,%s)',(version,name))
        log(f'적용: {name}');result.append(name)
    return result
