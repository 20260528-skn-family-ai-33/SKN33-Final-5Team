import csv,io,json

def write_result_node(context,format):
    if context.get('rows') is None:raise ValueError('실제 파일을 먼저 읽어야 결과를 만들 수 있습니다.')
    if format not in ['csv','json']:raise ValueError('CSV 또는 JSON을 선택하세요.')
    rows=context['rows'];columns=context['columns']
    if format=='csv':
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='raise');writer.writeheader()
        # Neutralize spreadsheet formulas on export; the stored row values remain original.
        for row in rows:writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})
        content='\ufeff'+stream.getvalue();mime='text/csv; charset=utf-8'
    else:content=json.dumps(rows,ensure_ascii=False,indent=2);mime='application/json; charset=utf-8'
    context['artifact']={'name':'에이전트_시험결과.'+format,'content':content,'mime':mime}
    context['trace'].append({'node':'write_result','label':'결과 파일 만들기','status':'completed','detail':f'{len(rows)}행을 {format.upper()} 파일로 만들었습니다.','parameters':{'format':format}})
    return {'file_name':context['artifact']['name'],'row_count':len(rows),'download_available':True}
