"""Dispatch a workflow node to the executor registered for its tool (catalog module_path)."""

def execute_node(node,state,storage,config,llm):
    from importlib import import_module
    from workflow_engine.catalog.registry import TOOLS, uses_operation
    if uses_operation(node):
        from workflow_engine.executors.canonical_operation import execute
        return execute(node,state,storage,config,llm)
    item=next((t for t in TOOLS if t['id']==node['tool']),None)
    if not item:raise ValueError('등록되지 않은 노드입니다.')
    module,name=item['module_path'].split(':')
    return getattr(import_module(module),name)(node,state,storage,config,llm)
