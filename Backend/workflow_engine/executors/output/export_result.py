import csv,io,json

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


def records_artifact(records,format,confirmation=None):
    if format=='json':
        # A user's confirmation from ask_user is saved with the records it approved.
        data={'records':records,'user_confirmation':confirmation} if confirmation else records
        return {'name':'추출_결과.json','content':json.dumps(data,ensure_ascii=False,indent=2),'mime':'application/json; charset=utf-8'}
    if format!='csv':raise ValueError('표 결과는 CSV/JSON을 선택하세요.')
    columns=list(dict.fromkeys(k for r in records for k in r))
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


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    validate_export_inputs(inputs)
    # Review candidates are unapproved; even a reused workflow that hides them in metadata must not export them.
    if any(state['outputs'][b['node_id']].get(port) for b in node['bindings'] for port in REVIEW_PORTS):
        raise ValueError(REVIEW_EXPORT_ERROR)
    format=settings.get('format')
    if 'records' in inputs:
        records = inputs['records']
        fields = settings.get('fields') or []
        from workflow_engine.executors.document.table_rows import COVERAGE_KEY
        if any(record.get(COVERAGE_KEY) == 'unverified' for record in records):
            raise ValueError('값의 인용 근거만 확인됐으며 원본 전체 행의 추출 여부는 확인하지 못했습니다. 표 인식 결과를 검토해 주세요.')
        if not fields and records and all(record.get(COVERAGE_KEY) == 'complete' for record in records):
            from workflow_engine.executors.document.extract_information import ROW_RESERVED_FIELDS
            fields = [field for field in records[0] if field not in ROW_RESERVED_FIELDS]
        if fields:
            if not records or any(record.get(COVERAGE_KEY) != 'complete' or any(field not in record for field in fields) for record in records):
                raise ValueError('추출된 값의 근거는 확인했지만 원본 전체 행의 추출 여부는 확인하지 못했습니다. 표 인식 결과를 검토해 주세요. 일부 결과를 완성된 CSV로 저장하지 않았습니다.')
            records = [{field: record[field] for field in fields} for record in records]
        return {'artifact':records_artifact(records,format,inputs.get('answer'))}
    text=inputs.get('text') or inputs.get('answer')
    if isinstance(text,str) and text.strip():return {'artifact':text_artifact(text,format)}
    raise ValueError('저장할 records/text/answer 결과 입력이 필요합니다.')
