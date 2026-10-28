from ..tools.filter_sort import filter_sort_node


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    ctx = {**inputs['table'], 'trace': []}
    plan = settings.get('execution_plan') or config['execution_plan']
    filter_sort_node(ctx, plan['filters'], plan['sort'], plan['match'], plan['missing_policy'], plan['duplicate_policy'], plan['duplicate_field'])
    result = {k: ctx[k] for k in ['rows', 'columns', 'file_id']}
    if inputs['table'].get('review_report') is True:
        result['review_report'] = True
    return {'table': result}
