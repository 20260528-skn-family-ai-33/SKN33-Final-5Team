from ..node_library import run_agent_node

def execute(node,state,storage,config,llm):
    return run_agent_node(node,state,storage,config,llm)
