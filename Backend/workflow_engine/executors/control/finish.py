def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    return {**inputs, 'text': inputs.get('text') or settings['instructions'] or '처리가 완료되었습니다.'}
