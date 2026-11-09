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


def constrain_draft_settings(schema):
    """Decode valid required settings; never choose a business column for the model."""
    from copy import deepcopy
    result = deepcopy(schema)
    result['properties']['instructions'].update(
        minLength=1,
        description='사용자가 요청한 전체 업무를 실행할 지시사항입니다. 빈 문자열을 쓰지 마세요. 노드별 settings.instructions와 별개인 초안 최상위 필드입니다.')

    def visit(value):
        if isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, dict):
            for child in list(value.values()): visit(child)
            props = value.get('properties', {})
            if props.get('tool', {}).get('enum') != ['aggregate_table']:
                return
            settings = props.get('settings', {})
            fields = settings.get('properties', {})
            if 'aggregation' not in fields or 'sum_field' not in fields:
                return  # Operation variants have a different contract.
            variants = []
            for aggregation in fields['aggregation'].get('enum', ['count', 'sum']):
                if aggregation not in ('count', 'sum'): continue
                variant = deepcopy(settings)
                variant['properties']['aggregation'] = {'type':'string', 'enum':[aggregation]}
                if aggregation == 'sum':
                    column = variant['properties']['sum_field']
                    variant['properties']['sum_field'] = {
                        'type':'string', 'minLength':1,
                        **({'enum':column['enum']} if 'enum' in column else {})}
                variants.append(variant)
            if variants: props['settings'] = {**settings, 'anyOf':variants}
    visit(result)
    return result


def draft_setting_issues(draft):
    """Check required values even when a provider ignores its response schema."""
    issues = []
    if not isinstance(draft.get('instructions'), str) or not draft['instructions'].strip():
        issues.append('에이전트 지시사항을 비우지 말고 사용자가 요청한 업무를 작성하세요.')
    for node in (draft.get('workflow') or {}).get('nodes', []):
        settings = node.get('settings', {})
        if node.get('tool') == 'aggregate_table' and not settings.get('operation') and settings.get('aggregation') == 'sum':
            column = settings.get('sum_field')
            if not isinstance(column, str) or not column.strip():
                issues.append('합계 단계의 sum_field에 사용자 요청의 숫자 열을 지정하세요. 열이 미정이면 questions로 질문하고 workflow=null로 대기하세요.')
    return issues
