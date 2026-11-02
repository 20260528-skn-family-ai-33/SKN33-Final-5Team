"""Join two explicitly selected tables through the existing local operation."""

def execute(node,state,storage,config,llm):
    from integrations.unified_tools.dispatcher import execute as execute_unified
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    left,right=inputs['left'],inputs['right'];settings=node['settings']
    keys=settings.get('join_keys');how=settings.get('join_how');card=settings.get('join_cardinality')
    if (not isinstance(keys,list) or not keys or len(keys)>10 or len(set(keys))!=len(keys)
        or any(not isinstance(k,str) or not k.strip() for k in keys)):
        raise ValueError('중복되지 않은 결합 열을 지정하세요.')
    if how not in ('inner','left') or card not in ('one_to_one','many_to_one'):
        raise ValueError('결합 방식과 키 관계를 명시하세요.')
    if any(t.get('review_report') is True for t in (left,right)):
        raise ValueError('검수 보고서는 결합할 수 없습니다.')
    for t in (left,right):
        if not isinstance(t.get('rows'),list) or not isinstance(t.get('columns'),list) or len(t['rows'])>10000:
            raise ValueError('표 형식이나 행 수를 확인하세요.')
        if not set(keys)<=set(t['columns']):raise ValueError('결합 열이 양쪽 표에 있어야 합니다.')
        if any(not isinstance(row,dict) or any(row.get(k) is None or row.get(k)=='' for k in keys) for row in t['rows']):
            raise ValueError('빈 결합 키를 사용할 수 없습니다.')
    # Work out the output schema even if the result is empty and reject
    # suffix collisions independent of row contents.
    columns=list(left['columns']);right_outputs=[]
    for col in right['columns']:
        if col in keys:continue
        dest=col+'_right' if col in columns else col
        if dest in columns:raise ValueError('결합 결과의 열 이름이 충돌합니다.')
        columns.append(dest);right_outputs.append(dest)
    result=execute_unified('join_tables','join',{'left':left,'right':right,'keys':keys,
                           'how':how,'cardinality':card,'null_keys':'error','max_rows':10000})['table']
    # The original operation infers right columns from rows. Preserve its
    # declared schema when the right table is empty and left rows are kept.
    for row in result['rows']:
        for col in right_outputs:row.setdefault(col,None)
    return {'table':{'rows':result['rows'],'columns':columns,
                     'source_file_ids':list(dict.fromkeys(x for t in (left,right) for x in ([t['file_id']] if t.get('file_id') else t.get('source_file_ids',[]))))}}
