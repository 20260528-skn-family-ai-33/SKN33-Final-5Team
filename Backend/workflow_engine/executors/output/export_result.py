import csv,io,json

from workflow_engine.catalog.registry import uses_operation
from workflow_engine.execution_path import TABLE_SCOPE_ERROR, records_source_node, records_table_scope_incomplete
from workflow_engine.executors.document.legacy_table_headers import validate_legacy_record_headers
from workflow_engine.library.reusable_workflows import REVIEW_EXPORT_ERROR

REVIEW_PORTS=('reviews','_reviews','_review_updates')


def validate_export_inputs(inputs):
    if ('records' in inputs and 'text' in inputs or
            'records' not in inputs and 'text' in inputs and 'answer' in inputs):
        raise ValueError('저장할 본문 입력이 여러 개입니다. 결과별로 export_result 노드를 나누세요. records와 answer는 추출 결과와 사용자 확인 답변으로 함께 저장할 수 있습니다.')


def safe_cell(value):
    # Neutralize spreadsheet formulas on export; the record values stay original.
    if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False)
    if value is None:return ''
    return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')) else value


def records_artifact(records,format,confirmation=None,fields=None):
    if format=='json':
        # A user's confirmation from ask_user is saved with the records it approved.
        data={'records':records,'user_confirmation':confirmation} if confirmation else records
        return {'name':'추출_결과.json','content':json.dumps(data,ensure_ascii=False,indent=2),'mime':'application/json; charset=utf-8'}
    if format!='csv':raise ValueError('표 결과는 CSV/JSON을 선택하세요.')
    columns=fields or list(dict.fromkeys(k for r in records for k in r))
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
    for r in records:writer.writerow({k:safe_cell(r.get(k)) for k in columns})
    return {'name':'추출_결과.csv','content':'﻿'+stream.getvalue(),'mime':'text/csv; charset=utf-8'}


def text_artifact(text,format):
    if format=='markdown':
        return {'name':'답변.md','content':text,'mime':'text/markdown; charset=utf-8'}
    if format!='json':raise ValueError('텍스트 결과는 Markdown 또는 JSON을 선택하세요.')
    try:json.loads(text)
    except ValueError:raise ValueError('JSON으로 저장할 텍스트가 올바른 JSON 문서가 아닙니다.') from None
    return {'name':'답변.json','content':text,'mime':'application/json; charset=utf-8'}


def project_records(records, fields, *, field_records=False):
    """Column projection does not assert that these are parser-native table rows."""
    if field_records:
        groups = {}
        for record in records:
            key = record.get('file_id') or record.get('file_name')
            if not key:
                raise ValueError('단일 값 결과의 원본 문서를 구분할 수 없습니다.')
            group = groups.setdefault(key, {})
            field = record['field']
            if field in group:
                raise ValueError('같은 문서에 중복된 추출 항목이 있습니다. 결과 범위를 확인해 주세요.')
            group[field] = record['value']
        records = list(groups.values())
    if any(any(field not in record for field in fields) for record in records):
        raise ValueError('출력할 열이 추출 결과에 없습니다. 추출 항목과 출력 열을 확인해 주세요.')
    return [{field: record[field] for field in fields} for record in records]


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    validate_export_inputs(inputs)
    # Review candidates are unapproved; even a reused workflow that hides them in metadata must not export them.
    if any(state['outputs'][b['node_id']].get(port) for b in node['bindings'] for port in REVIEW_PORTS):
        raise ValueError(REVIEW_EXPORT_ERROR)
    format=settings.get('format')
    if 'records' in inputs:
        if records_table_scope_incomplete(node, state['outputs'], config):
            raise ValueError(TABLE_SCOPE_ERROR)
        records = inputs['records']
        validate_legacy_record_headers(records, storage)
        from workflow_engine.executors.document.receipt_check import CHECK_FIELD
        warnings = list(dict.fromkeys(
            str(record[CHECK_FIELD]) for record in records
            if record.get(CHECK_FIELD) and record[CHECK_FIELD] != '일치'))
        fields = settings.get('fields') or []
        from workflow_engine.executors.document.table_rows import COVERAGE_KEY
        if any(record.get(COVERAGE_KEY) == 'unverified' for record in records):
            raise ValueError('값의 인용 근거만 확인됐으며 원본 전체 행의 추출 여부는 확인하지 못했습니다. 표 인식 결과를 검토해 주세요.')
        if not fields and records and all(record.get(COVERAGE_KEY) == 'complete' for record in records):
            from workflow_engine.executors.document.extract_information import ROW_RESERVED_FIELDS
            fields = [field for field in records[0] if field not in ROW_RESERVED_FIELDS]
        if fields:
            workflow = (config or {}).get('workflow') or {}
            nodes = {n['id']: n for n in workflow.get('nodes', [])}
            source = records_source_node(node, nodes, dependencies=(config or {}).get('_saved_workflows'))
            field_records = (source is not None and source['tool'] == 'extract_information' and not uses_operation(source)
                             and source['settings'].get('extraction_mode') not in ('rows', 'receipt_items'))
            records = project_records(records, fields, field_records=field_records)
        artifact = records_artifact(records,format,inputs.get('answer'),fields=fields)
        if warnings:
            artifact['warnings'] = ['영수증 검산: ' + warning for warning in warnings]
        return {'artifact': artifact}
    text=inputs.get('text') or inputs.get('answer')
    if isinstance(text,str) and text.strip():return {'artifact':text_artifact(text,format)}
    raise ValueError('저장할 records/text/answer 결과 입력이 필요합니다.')
