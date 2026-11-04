"""Native executor settings shared by both workflow generation paths."""
SCOPED_SETTINGS = {'read_table':('file_ids',), 'filter_sort':('execution_plan',),
                   'write_result':('format',), 'finish':('instructions',)}
NATIVE_SETTINGS = {
    **SCOPED_SETTINGS, 'read_document':('file_ids',), 'search_document':('query',),
    'extract_information':('fields','extraction_mode','instructions'), 'compose_answer':('instructions',),
    'aggregate_table':('fields','aggregation','sum_field'),
    'calculate_columns':('table_operation','calculation_formula','output_column','rename_from','rename_to','fields','date_input_format','date_timezone'),
    'text_transform':('text_find','text_replacement'), 'text_statistics':('statistics_unit','include_whitespace'),
    'date_transform':('date_operation','date_seconds','date_output_format'), 'base64_codec':('codec_mode',),
    'text_hash':(), 'export_result':('format',), 'reply_review':('fields','instructions'),
    'review_summary':(), 'agent_node':('instructions',), 'condition':('condition_field','condition_operator','instructions'),
    'ask_user':('question','instructions'),
    'saved_workflow':('agent_id','agent_version','output_node','output_port'),
}


def decoded_resource_tools(resources):
    """Prefer stored table rows when the session already contains parsed tables."""
    return {'read_table'} if any(r.get('kind') == 'table' for r in resources) else set()


def expand_node_settings(raw):
    """Restore compatibility defaults before strict application validation."""
    from copy import deepcopy
    from contracts.models import NodeSettings
    result = deepcopy(raw)
    workflow = result.get('workflow')
    if isinstance(workflow, dict) and isinstance(workflow.get('nodes'), list):
        defaults = {name: ([] if getattr(field.annotation, '__origin__', None) is list else None)
                    for name, field in NodeSettings.model_fields.items()}
        for node in workflow['nodes']:
            if isinstance(node, dict) and isinstance(node.get('settings'), dict):
                node['settings'] = {**deepcopy(defaults), **node['settings']}
    return result
