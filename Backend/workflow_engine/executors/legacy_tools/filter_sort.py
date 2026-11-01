from functools import cmp_to_key
import math

def empty(v):return v is None or isinstance(v,str) and not v.strip()
def numeric(v):
    if empty(v) or isinstance(v,bool):raise ValueError('빈 값이나 논리값은 숫자로 비교할 수 없습니다.')
    try:n=float(v)
    except (TypeError,ValueError):raise ValueError('숫자로 변환할 수 없는 값이 있습니다. 처리 정책을 확인하세요.')
    if not math.isfinite(n):raise ValueError('유한한 숫자만 사용할 수 있습니다.')
    return n

def filter_sort_node(context,filters,sort,match='all',missing_policy='ask',duplicate_policy='keep',duplicate_field=None):
    if context.get('rows') is None:raise ValueError('먼저 read_table로 실제 파일을 읽으세요.')
    if match not in ['all','any'] or missing_policy not in ['ask','exclude','keep','error'] or duplicate_policy not in ['ask','keep','first','last','error']:raise ValueError('처리 정책을 확인하세요.')
    if not isinstance(filters,list) or len(filters)>20 or not isinstance(sort,list) or len(sort)>5:raise ValueError('조건 또는 정렬 항목 수가 잘못됐습니다.')
    columns=context['columns'];fields=[]
    for f in filters:
        if not isinstance(f,dict) or f.get('operator') not in ['eq','ne','lt','lte','gt','gte','contains','is_empty','not_empty'] or f.get('value_type','text') not in ['number','text','boolean']:raise ValueError('조건의 연산자와 자료형을 확인하세요.')
        fields.append(f.get('field'))
        if f.get('compare_field'):fields.append(f['compare_field'])
    for s in sort:
        if not isinstance(s,dict) or s.get('direction') not in ['ascending','descending'] or s.get('value_type') not in ['number','text']:raise ValueError('정렬의 방향과 자료형을 확인하세요.')
        fields.append(s.get('field'))
    if duplicate_field:fields.append(duplicate_field)
    if any(field not in columns for field in fields):raise ValueError('요청한 열이 실제 파일에 없습니다. 실제 열 이름을 확인하세요.')
    source=context['rows'];rows=list(source)
    if duplicate_policy in ['first','last','error','ask']:
        if not duplicate_field:raise ValueError('중복을 판단할 기준 열을 알려주세요.')
        seen=set();duplicates=False
        for r in rows:
            value=str(r.get(duplicate_field));duplicates|=value in seen;seen.add(value)
        if duplicates and duplicate_policy in ['ask','error']:raise ValueError('중복 값이 있습니다. 유지·첫 행·마지막 행 중 처리 정책을 확인하세요.')
        if duplicate_policy in ['first','last']:
            selected={}
            for i,r in enumerate(rows):
                key=str(r.get(duplicate_field))
                if duplicate_policy=='last' or key not in selected:selected[key]=i
            rows=[r for i,r in enumerate(rows) if i in set(selected.values())]
    # is_empty / not_empty explicitly define a missing-value condition themselves.
    normal_fields=set(f['field'] for f in filters if f['operator'] not in ['is_empty','not_empty'])|set(f['compare_field'] for f in filters if f.get('compare_field'))|set(s['field'] for s in sort)
    if missing_policy=='exclude' and not normal_fields and not filters and not sort:normal_fields=set(columns)
    missing=lambda row:any(empty(row.get(k)) for k in normal_fields)
    if any(missing(r) for r in rows) and missing_policy in ['ask','error']:raise ValueError('조건에 사용하는 열에 빈 값이 있습니다. 제외 또는 유지 정책을 확인하세요.')
    if missing_policy=='exclude':rows=[r for r in rows if not missing(r)]
    def test(row,f):
        left=row.get(f['field']);op=f['operator']
        if op=='is_empty':return empty(left)
        if op=='not_empty':return not empty(left)
        right=row.get(f['compare_field']) if f.get('compare_field') else f.get('value')
        if empty(left) or empty(right):return False
        kind=f.get('value_type','text')
        if kind=='number':left,right=numeric(left),numeric(right)
        elif kind=='boolean':
            if not isinstance(left,bool) or not isinstance(right,bool):raise ValueError('논리값 조건에는 실제 true/false 값이 필요합니다.')
        else:left,right=str(left),str(right)
        return {'eq':lambda:left==right,'ne':lambda:left!=right,'lt':lambda:left<right,'lte':lambda:left<=right,'gt':lambda:left>right,'gte':lambda:left>=right,'contains':lambda:str(right) in str(left)}[op]()
    def keep(row):
        values=[]
        for f in filters:
            unknown=f['operator'] not in ['is_empty','not_empty'] and (empty(row.get(f['field'])) or (f.get('compare_field') and empty(row.get(f['compare_field']))))
            if missing_policy=='keep' and unknown:continue
            values.append(test(row,f))
        return not values or (all(values) if match=='all' else any(values))
    rows=[r for r in rows if keep(r)]
    def compare(a,b):
        for s in sort:
            x,y=a.get(s['field']),b.get(s['field'])
            if empty(x) or empty(y):
                diff=0 if empty(x) and empty(y) else 1 if empty(x) else -1
                if diff:return diff # Missing values are always last, independent of direction.
                continue
            x,y=(numeric(x),numeric(y)) if s['value_type']=='number' else (str(x),str(y))
            diff=(x>y)-(x<y)
            if diff:return diff if s['direction']=='ascending' else -diff
        return 0
    if sort:rows.sort(key=cmp_to_key(compare))
    context['rows']=rows;context['filtered']=True;context['artifact']=None
    params={'filters':filters,'sort':sort,'match':match,'missing_policy':missing_policy,'duplicate_policy':duplicate_policy,'duplicate_field':duplicate_field}
    context['trace'].append({'node':'filter_sort','label':'조건에 따라 정리','status':'completed','detail':f'{len(source)}행에서 {len(rows)}행으로 처리했습니다.','source_file_id':context['file_id'],'parameters':params})
    return {'row_count':len(rows),'preview':rows[:20],'applied_conditions':params}
