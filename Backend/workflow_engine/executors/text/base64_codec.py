"""UTF-8 Base64 conversion using the existing local tool operation."""
def execute(node, state, storage, config, llm):
    from workflow_engine.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    mode=node['settings'].get('codec_mode')
    if mode not in ('encode','decode'):raise ValueError('Base64 인코딩 또는 디코딩을 선택하세요.')
    result=execute_unified('base64_codec','convert',{'text':inputs['text'],'mode':mode})
    if not isinstance(result,str) or not result.strip():raise ValueError('변환 결과가 비어 있습니다.')
    return {'text':result}
