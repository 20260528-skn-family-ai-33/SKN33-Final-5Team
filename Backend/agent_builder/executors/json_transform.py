"""Use the existing local JSON parser/lookup with an explicit service contract."""
import json

def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('JSON 객체에 중복 키가 있습니다.')
        result[key]=value
    return result

def execute(node,state,storage,config,llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    text=inputs.get('text');settings=node['settings'];operation=settings.get('json_operation')
    if not isinstance(text,str) or not text.strip() or len(text)>1000000:raise ValueError('JSON 텍스트와 크기를 확인하세요.')
    if operation not in ('parse','lookup'):raise ValueError('JSON 해석 또는 값 조회를 선택하세요.')
    try:
        # The original parser permits duplicate keys and non-finite literals;
        # reject those before calling it so input data is not silently changed.
        json.loads(text,object_pairs_hook=_unique_object,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError('유한한 JSON 숫자가 필요합니다.')))
        value=execute_unified('json_transform','parse',{'text':text})
        if operation=='lookup':
            path=settings.get('json_path')
            if not isinstance(path,list) or len(path)>30 or any(type(part) not in (str,int) or (type(part) is int and part<0) for part in path):
                raise ValueError('문자열 키와 0 이상 인덱스 경로가 필요합니다.')
            value=execute_unified('json_transform','lookup',{'value':value,'path':path})
        return {'text':json.dumps(value,ensure_ascii=False,allow_nan=False)}
    except (KeyError,IndexError,TypeError,RecursionError,json.JSONDecodeError) as error:
        raise ValueError('JSON 형식 또는 조회 경로를 확인하세요.') from error
