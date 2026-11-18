"""Execute a pinned saved graph as a checkpointed subgraph, not a new source-code string."""
from documents.resources.document_resources import TABLE_SCOPE_INCOMPLETE, document_scope, input_document_scope
from workflow_engine.execution_path import (
    ROWS_SCOPE_VERIFIED, TABLE_SCOPE_ERROR, document_table_scope_incomplete, output_records_table_scope_incomplete,
)


def child_configuration(agent,parent,file_ids):
    return {**agent['config'],'_saved_workflows':agent.get('workflow_dependencies',{}),
            '_runtime_file_ids':file_ids,'_execution_time':parent.get('_execution_time')}


def execute(node,state,storage,config,llm):
    from workflow_engine.runner import compile_workflow
    from workflow_engine.executors.canonical_operation import file_aliases
    from verification.reply_review.state import collect_reviews
    settings=node['settings'];agent=config.get('_saved_workflows',{}).get(node['id'])
    if not agent:raise ValueError('저장된 워크플로우의 고정 버전이 연결되지 않았습니다. 설정을 다시 저장하세요.')
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    inputs.update(input_document_scope(node, state['outputs']))
    if 'documents' in inputs and document_table_scope_incomplete(node, state['outputs'], config):
        inputs[TABLE_SCOPE_INCOMPLETE] = True
    child=child_configuration(agent,config,state.get('file_ids',[]))
    graph,connection=compile_workflow(storage,child,llm,ownership=config.get('_ownership'),inherit_checkpoint=True,external_inputs=inputs)
    try:
        aliases = file_aliases(state['outputs'], state.get('inherited_file_aliases'))
        inherited = {logical.as_posix():physical for logical, physical in aliases.items()}
        result=graph.invoke({'outputs':{},'steps':[],'request':inputs.get('text') or state['request'],'customer_message':state.get('customer_message',state['request']),'reply_draft':state.get('reply_draft'),'file_ids':state.get('file_ids',[]),'messages':state.get('messages',[]),'external_inputs':inputs,'inherited_file_aliases':inherited})
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
        scope = document_scope(selected) if settings['output_port'] == 'documents' else {}
        # Publish only completed child writes, including writes from nested calls.
        # The aliases inherited from the caller are already in the caller's state.
        if produced := file_aliases(result['outputs']):
            scope['_file_aliases'] = {logical.as_posix():physical for logical, physical in produced.items()}
        if settings['output_port'] == 'records':
            if output_records_table_scope_incomplete(settings['output_node'], result['outputs'], child):
                raise ValueError(TABLE_SCOPE_ERROR)
            scope[ROWS_SCOPE_VERIFIED] = True
        return {**scope, **({'evidence':selected['evidence']} if selected.get('evidence') else {}),settings['output_port']:value,'_reviews':list(reviews.values()),'_selected_port':settings['output_port'],'_child_steps':result.get('steps',[]),'_dependency':{'agent_id':agent['id'],'version':agent['version']}}
    finally:
        if connection:connection.close()
