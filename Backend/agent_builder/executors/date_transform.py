"""Explicit ISO date/time adjustment or formatting via the local tool."""
def execute(node, state, storage, config, llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    settings=node['settings']; operation=settings.get('date_operation')
    if operation=='adjust':
        seconds=settings.get('date_seconds')
        if type(seconds) is not int:raise ValueError('조정할 초 단위를 입력하세요.')
        args={'timestamp':inputs['text'],'seconds':seconds}
    elif operation=='format':
        fmt=settings.get('date_output_format')
        if not isinstance(fmt,str) or not fmt.strip():raise ValueError('날짜 출력 형식이 필요합니다.')
        args={'timestamp':inputs['text'],'format':fmt}
    else:raise ValueError('날짜 조정 또는 형식 변경을 선택하세요.')
    result=execute_unified('date_transform',operation,args)
    if not isinstance(result,str) or not result.strip():raise ValueError('날짜 변환 결과가 비어 있습니다.')
    return {'text':result}
