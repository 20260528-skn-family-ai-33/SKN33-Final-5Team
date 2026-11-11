"""Exercise the PostgreSQL locking protocol without accessing an operational database."""
from types import SimpleNamespace
import pytest
from infrastructure.persistence.generation_lock import generation_session_lock


class Database:
    def __init__(self, held):
        self.held=held
        self.calls=[]
        self.closed=False
        self.connection=SimpleNamespace(close=self.close)
    def close(self):self.closed=True
    def rollback(self):self.calls.append('rollback')
    def execute(self, sql, params):
        self.calls.append((sql,params))
        key,=params
        if sql=='SELECT pg_try_advisory_lock(?::bigint)':
            result=key not in self.held
            if result:self.held.add(key)
            return SimpleNamespace(fetchone=lambda:(result,))
        assert sql=='SELECT pg_advisory_unlock(?::bigint)'
        self.held.remove(key)
        return SimpleNamespace(fetchone=lambda:(True,))


def test_postgres_ownership_is_session_and_workspace_scoped_and_released_on_error():
    held=set()
    first=SimpleNamespace(dialect='postgres',workspace_id='alice',db=Database(held))
    second=SimpleNamespace(dialect='postgres',workspace_id='alice',db=Database(held))
    other=SimpleNamespace(dialect='postgres',workspace_id='bob',db=Database(held))
    with pytest.raises(RuntimeError, match='model failed'):
        with generation_session_lock(first,'session') as acquired:
            assert acquired
            with generation_session_lock(second,'session') as busy:assert not busy
            with generation_session_lock(other,'session') as independent:assert independent
            raise RuntimeError('model failed')
    assert not held
    with generation_session_lock(second,'session') as acquired:assert acquired
    assert not held


def test_failed_unlock_discards_the_connection_instead_of_returning_it_locked():
    db=Database(set())
    original=db.execute
    def execute(sql,params):
        if 'pg_advisory_unlock' in sql:raise OSError('connection interrupted')
        return original(sql,params)
    db.execute=execute
    store=SimpleNamespace(dialect='postgres',workspace_id='alice',db=db)
    with pytest.raises(OSError):
        with generation_session_lock(store,'session') as acquired:assert acquired
    assert db.closed
