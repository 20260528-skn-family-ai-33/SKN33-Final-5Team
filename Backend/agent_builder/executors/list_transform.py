"""Explicit JSON-array list selection/count, using the local unified operations."""
import json
from .json_transform import _unique_object

def execute(node,state,storage,config,llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    text=inputs.get('text');settings=node['settings'];operation=settings.get('list_operation')
    if not isinstance(text,str) or not text.strip() or len(text)>1000000:raise ValueError('JSON 배열 텍스트와 크기를 확인하세요.')
    try:
        items=json.loads(text,object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('유한한 JSON 숫자가 필요합니다.')))
        # json.dumps also rejects values such as 1e999 that parse to infinity.
        json.dumps(items,allow_nan=False)
    except (json.JSONDecodeError,RecursionError) as error:raise ValueError('유효한 JSON 배열이 필요합니다.') from error
    if not isinstance(items,list):raise ValueError('JSON 배열이 필요합니다.')
    if operation=='count':value=execute_unified('list_transform','count',{'items':items})
    elif operation=='select':
        index=settings.get('list_index')
        if type(index) is not int or index<0 or index>=len(items):raise ValueError('유효한 0부터 시작하는 인덱스가 필요합니다.')
        value=execute_unified('list_transform','select',{'items':items,'index':index})
    else:raise ValueError('목록 항목 선택 또는 개수 세기를 지정하세요.')
    return {'text':json.dumps(value,ensure_ascii=False,allow_nan=False)}
