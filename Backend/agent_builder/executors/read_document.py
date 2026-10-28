"""Registered source-reader entrypoint; registry path is unchanged."""
from ._read_source import read_source


def execute(node,state,storage,config,llm):
    return read_source(node,state,storage,config,'documents')
