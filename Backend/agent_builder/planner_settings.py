"""Per-tool model output settings; absent values are neutral, never inferred."""
from .models import NodeSettings

FIELDS={
    'read_table':{'file_ids','fields'}, 'read_document':{'file_ids'},
    'filter_sort':{'execution_plan','instructions'},
    'write_result':{'format'}, 'export_result':{'format'},
    'search_document':{'query'}, 'extract_information':{'fields','instructions'},
    'compose_answer':{'instructions'}, 'reply_review':{'fields','instructions'},
    'review_summary':set(), 'agent_node':{'instructions','fields'},
    'condition':{'condition_field','condition_operator','instructions'},
    'ask_user':{'question','instructions'}, 'finish':{'instructions'},
    'aggregate_table':{'aggregation','sum_field','fields'},
    'calculate_columns':{'table_operation','output_column','calculation_formula','rename_from','rename_to','fields','date_input_format','date_timezone'},
    'scalar_calculate':{'calculation_formula'},
    'text_transform':{'text_find','text_replacement'}, 'base64_codec':{'codec_mode'},
    'text_hash':set(), 'date_transform':{'date_operation','date_seconds','date_output_format'},
    'text_statistics':{'statistics_unit','include_whitespace'},
    'json_transform':{'json_operation','json_path'}, 'list_transform':{'list_operation','list_index'},
    'join_tables':{'join_keys','join_how','join_cardinality'},
    'compare_tables':{'compare_keys','compare_columns','compare_tolerance'},
    'saved_workflow':{'agent_id','agent_version','output_node','output_port'},
}
NEUTRAL={'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,
         'question':None,'condition_field':None,'condition_operator':None}

def compact_settings_schema(schema,tool):
    if tool not in FIELDS:raise ValueError('도구의 모델 출력 설정 계약이 없습니다: '+tool)
    result=dict(schema)
    allowed=FIELDS[tool]|{'node_ref'}
    result['properties']={k:v for k,v in schema['properties'].items() if k in allowed}
    result['required']=list(result['properties'])
    result['additionalProperties']=False
    return result

def expand_workflow_settings(workflow):
    if not workflow:return workflow
    for node in workflow.get('nodes',[]):
        tool=node.get('tool');values=node.get('settings')
        if tool not in FIELDS or not isinstance(values,dict):raise ValueError('노드 설정을 확인하세요.')
        # Reject unexpected settings rather than discarding meaningful model output.
        allowed=FIELDS[tool]|{'node_ref'}
        if any(values[k] not in (None,[]) for k in set(values)-allowed):
            raise ValueError('해당 도구에 없는 설정이 있습니다: '+tool)
        neutral={k:(list(v) if isinstance(v,list) else v) for k,v in NEUTRAL.items()}
        node['settings']=NodeSettings.model_validate({**neutral,**values}).model_dump()
    return workflow

def compact_prompt_context(value):
    """Keep all meaningful settings while avoiding repeated null fields."""
    if isinstance(value,list):return [compact_prompt_context(v) for v in value]
    if not isinstance(value,dict):return value
    return {k:({sk:compact_prompt_context(sv) for sk,sv in v.items() if sv is not None}
               if k=='settings' and isinstance(v,dict) else compact_prompt_context(v))
            for k,v in value.items()}
