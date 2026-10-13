import json,re
from pathlib import Path
from pydantic import BaseModel,ConfigDict
from langgraph.types import interrupt
from deepagents import create_deep_agent
from langchain_core.tools import tool as registered_tool
from langchain.agents.structured_output import ProviderStrategy
from langchain.agents.middleware import wrap_model_call
from ..workflow_runtime import Fact,Facts,SearchTerms,GroundedAnswer,Citation,Decision,InputAssessment,document_chunks,original_quote
from ..tools.filter_sort import filter_sort_node
from ..tools.write_result import write_result_node

def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    table = inputs['table']
    fields = settings['fields']
    if any((f not in table['columns'] for f in fields)):
        raise ValueError('집계할 열이 실제 자료에 없습니다.')
    from decimal import Decimal, InvalidOperation
    operation = settings.get('aggregation') or 'count'
    column = 'sum' if operation == 'sum' else 'count'
    sum_field = settings.get('sum_field')
    if operation == 'sum' and sum_field not in table['columns']:
        raise ValueError('합계할 숫자 열이 없습니다.')
    if column in fields:
        raise ValueError('집계 결과 열과 그룹 열이 충돌합니다.')
    groups = {}
    for row in table['rows']:
        key = json.dumps([row.get(f) for f in fields], ensure_ascii=False)
        if key not in groups:
            groups[key] = {**{f: row.get(f) for f in fields}, column: Decimal(0) if operation == 'sum' else 0}
        amount = 1
        if operation == 'sum':
            value = row.get(sum_field)
            if value is None or isinstance(value, bool) or (not str(value).strip()):
                raise ValueError('합계 열에 빈 값 또는 잘못된 숫자가 있습니다.')
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                raise ValueError('합계 열에 숫자가 아닌 값이 있습니다.') from None
            if not amount.is_finite():
                raise ValueError('합계 열에는 유한한 숫자가 필요합니다.')
        groups[key][column] += amount
    rows = list(groups.values()) if fields else [groups.get('[]', {column: 0})]
    if operation == 'sum':
        for row in rows:
            amount = row[column]
            row[column] = int(amount) if amount == int(amount) else float(amount)
            import math
            if not math.isfinite(row[column]):
                raise ValueError('합계 결과가 숫자 범위를 초과했습니다.')
    result = {'rows': rows, 'columns': fields + [column], 'file_id': table['file_id']}
    if table.get('review_report') is True:
        result['review_report'] = True
    return {'table': result}
