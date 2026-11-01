"""Translate a sum source-column reference into its executor output column."""
def normalize_aggregate_plan_columns(workflow):
    nodes={n['id']:n for n in (workflow or {}).get('nodes',[])}
    for node in nodes.values():
        if node['tool']!='filter_sort' or node['settings'].get('node_ref'):
            continue
        binding=next((b for b in node['bindings'] if b['input']=='table'),None)
        source=nodes.get(binding['node_id']) if binding else None
        if not source or source['tool']!='aggregate_table':
            continue
        settings=source['settings']
        original=settings.get('sum_field')
        if settings.get('aggregation')!='sum' or not original or original in settings['fields']:
            continue
        plan=node['settings'].get('execution_plan')
        if not plan:
            continue
        for item in [*plan['filters'],*plan['sort']]:
            if item['field']==original:item['field']='sum'
            if item.get('compare_field')==original:item['compare_field']='sum'
