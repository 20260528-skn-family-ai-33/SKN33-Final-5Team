from workflow_engine.executors.legacy_tools.write_result import write_result_node


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    table=inputs['table']
    format=settings.get('format') or config['execution_plan']['output_format']
    ctx={'rows':table['rows'],'columns':table['columns'],'trace':[]}
    write_result_node(ctx,format)
    artifact=ctx['artifact']
    # An unapproved diagnostic table keeps its provenance so it is never published as an approved reply.
    if table.get('review_report') is True:
        artifact={**artifact,'name':'검수_보고서.'+format,'review_report':True}
    return {'artifact':artifact}
