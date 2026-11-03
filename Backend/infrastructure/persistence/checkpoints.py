"""LangGraph checkpoints live next to the business data.

SQLite: the same agent-builder.sqlite file as Storage. PostgreSQL: the checkpoint tables
in the a4b_builder schema, created by the explicit migration step and filtered by
row-level security on the connection's workspace (session IDs are already unique UUIDs).
"""
import sqlite3


class PostgresCheckpointConnection:
    """A pooled connection for one workspace; close() returns it to the pool."""
    def __init__(self,workspace):
        from infrastructure.persistence.db import checkout
        self._pool,self.connection=checkout('checkpoint',workspace)
    def close(self):
        if self.connection is not None:
            self._pool.putconn(self.connection);self.connection=None


def checkpoint_connection(storage):
    if getattr(storage,'dialect','sqlite')=='postgres':return PostgresCheckpointConnection(storage.workspace_id)
    return sqlite3.connect(storage.path,check_same_thread=False,timeout=10)


def checkpointer(connection):
    if isinstance(connection,PostgresCheckpointConnection):
        from langgraph.checkpoint.postgres import PostgresSaver
        return PostgresSaver(connection.connection)
    from langgraph.checkpoint.sqlite import SqliteSaver
    return SqliteSaver(connection)

