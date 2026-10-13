"""Execute a pinned saved graph as a checkpointed subgraph, not a new source-code string."""
def execute(node,state,storage,config,llm):
    from ..workflow_runtime import compile_workflow
    from ..review_state import collect_reviews
    settings=node['settings'];agent=config.get('_saved_workflows',{}).get(node['id'])
    if not agent:raise ValueError('저장된 워크플로우의 고정 버전이 연결되지 않았습니다. 설정을 다시 저장하세요.')
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    child={**agent['config'],'_saved_workflows':agent.get('workflow_dependencies',{})}
    graph,connection=compile_workflow(storage,child,llm,ownership=config.get('_ownership'),inherit_checkpoint=True)
    try:
        result=graph.invoke({'outputs':{},'steps':[],'request':inputs.get('text') or state['request'],'customer_message':state.get('customer_message',state['request']),'reply_draft':state.get('reply_draft'),'file_ids':state.get('file_ids',[]),'messages':state.get('messages',[]),'external_inputs':inputs})
        selected=result['outputs'].get(settings['output_node'],{})
        if settings['output_port'] not in selected:raise ValueError('저장된 워크플로우가 선택한 출력을 반환하지 않았습니다.')
        value=selected[settings['output_port']]
        # Approval is attached to the run, regardless of the selected public port.
        # Preserve the public instance:ordinal ID contract used by approval APIs.
        child_reviews=collect_reviews(result['outputs'])
        selected_ids=list(dict.fromkeys(r['id'] for r in value)) if settings['output_port']=='reviews' else []
        by_id={r['id']:r for r in child_reviews}
        # A selected subset retains its historical ordinal IDs; other reports
        # follow it so they cannot disappear when a different port is selected.
        ordered=selected_ids+[r['id'] for r in child_reviews if r['id'] not in selected_ids]
        reviews={id:{**by_id[id],'source_review_id':by_id[id].get('source_review_id',id),'id':node['id']+':'+str(i)} for i,id in enumerate(ordered)}
        if settings['output_port']=='reviews':value=[reviews[id] for id in selected_ids]
        return {**({'evidence':selected['evidence']} if selected.get('evidence') else {}),settings['output_port']:value,'_reviews':list(reviews.values()),'_selected_port':settings['output_port'],'_child_steps':result.get('steps',[]),'_dependency':{'agent_id':agent['id'],'version':agent['version']}}
    finally:
        if connection:connection.close()
