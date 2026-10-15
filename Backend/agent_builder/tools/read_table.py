def read_table_node(context,file_id):
    if file_id not in context['file_ids']:raise ValueError('이 에이전트에 연결되지 않은 자료입니다.')
    asset=context['storage'].asset(file_id)
    if asset['kind']!='table':raise ValueError('표 파일을 선택하세요.')
    rows=context['storage'].rows(file_id)
    context['rows']=rows;context['columns']=asset['columns'];context['file_id']=file_id
    context['artifact']=None;context['filtered']=False
    context['trace'].append({'node':'read_table','label':'표 파일 읽기','status':'completed','detail':f"{asset['name']}에서 {len(rows)}행을 읽었습니다.",'source_file_id':file_id,'parameters':{'file_id':file_id}})
    return {'file_id':file_id,'columns':asset['columns'],'row_count':len(rows),'profile':asset['profile'],'preview':rows[:20]}
