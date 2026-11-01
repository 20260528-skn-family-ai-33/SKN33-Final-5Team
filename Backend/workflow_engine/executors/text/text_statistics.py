"""Count characters or whitespace-delimited words using the local tool."""
def execute(node, state, storage, config, llm):
    from workflow_engine.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    settings=node['settings'];unit=settings.get('statistics_unit');include=settings.get('include_whitespace')
    if unit not in ('characters','words') or (unit=='characters' and type(include) is not bool):
        raise ValueError('문자·단어 단위와 공백 포함 여부를 확인하세요.')
    count=execute_unified('text_statistics','measure_text',
        {'text':inputs['text'],'unit':unit,'include_whitespace':include if unit=='characters' else True})
    return {'records':[{'unit':unit,'count':count}]}
