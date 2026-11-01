"""Literal text replacement through the existing unified tool implementation."""
def execute(node, state, storage, config, llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs = {b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    settings = node['settings']; find = settings.get('text_find'); replacement = settings.get('text_replacement')
    if not isinstance(find, str) or not find or not isinstance(replacement, str):
        raise ValueError('바꿀 문자열과 교체 문자열이 필요합니다.')
    result = execute_unified('text_transform','replace',{'text':inputs['text'],'find':find,'replacement':replacement})
    if not isinstance(result, str) or not result.strip():
        raise ValueError('변환 결과가 비어 있습니다.')
    return {'text':result}
