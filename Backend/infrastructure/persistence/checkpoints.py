"""LangGraph checkpoints live in the same SQLite file as Storage (agent-builder.sqlite)."""
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver


def checkpoint_connection(storage):
    return sqlite3.connect(storage.path,check_same_thread=False,timeout=10)


def sqlite_checkpointer(connection):
    return SqliteSaver(connection)
