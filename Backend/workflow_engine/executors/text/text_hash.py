"""UTF-8 text hashing using the existing local SHA-256 operation."""
def execute(node, state, storage, config, llm):
    from workflow_engine.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    return {'text':execute_unified('text_hash','sha256',{'text':inputs['text']})}
