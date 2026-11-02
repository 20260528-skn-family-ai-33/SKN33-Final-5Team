"""Compare two explicit tables without mutating either source."""
import json,math

def execute(node,state,storage,config,llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    baseline,candidate=inputs['baseline'],inputs['candidate'];s=node['settings']
    keys=s.get('compare_keys');columns=s.get('compare_columns');tolerance=s.get('compare_tolerance')
    for values,limit in ((keys,10),(columns,50)):
        if (not isinstance(values,list) or not values or len(values)>limit
            or any(not isinstance(v,str) or not v.strip() for v in values) or len(set(values))!=len(values)):
            raise ValueError('중복되지 않은 비교 키와 비교 열을 지정하세요.')
    if type(tolerance) not in (int,float) or not math.isfinite(tolerance) or tolerance<0:
        raise ValueError('0 이상의 유한한 허용 오차가 필요합니다.')
    for table in (baseline,candidate):
        if table.get('review_report') is True:raise ValueError('검수 보고서는 비교할 수 없습니다.')
        if not isinstance(table.get('rows'),list) or len(table['rows'])>10000 or not isinstance(table.get('columns'),list):
            raise ValueError('표 형식이나 행 수를 확인하세요.')
        if not set(keys+columns)<=set(table['columns']):raise ValueError('키와 비교 열은 양쪽 표에 있어야 합니다.')
        if any(not isinstance(row,dict) or any(row.get(k) is None or row.get(k)=='' for k in keys)
               or any(c not in row for c in columns) for row in table['rows']):
            raise ValueError('빈 키 또는 누락된 비교 값이 있습니다.')
    result=execute_unified('compare_tables','diff',{'baseline':baseline,'candidate':candidate,
             'keys':keys,'compare_columns':columns,'absolute_tolerance':tolerance})['differences']
    value=json.dumps(result,ensure_ascii=False,allow_nan=False)
    if len(value)>1000000:raise ValueError('비교 결과가 너무 큽니다.')
    return {'text':value}
