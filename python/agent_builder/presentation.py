"""Render deterministic table settings from actual executable parameters."""
import json
PURE_TABLE={'read_table','filter_sort','aggregate_table','write_result','finish'}
OPS={'eq':'=','ne':'≠','lt':'<','lte':'≤','gt':'>','gte':'≥','contains':'포함','is_empty':'빈 값','not_empty':'값 있음'}
def canonical_table_draft(draft,messages,resources):
    workflow=draft.get('workflow')
    if not workflow or any(n['tool'] not in PURE_TABLE for n in workflow['nodes']):return draft
    plan=draft['execution_plan'];schedule=draft.get('schedule');lines=[]
    if schedule:
        frequency='매일' if schedule['frequency']=='daily' else '매주 '+(['월','화','수','목','금','토','일'][schedule['weekday']]+'요일' if schedule['weekday'] is not None else '요일 확인 필요')
        lines.append(f"예약: {frequency} {schedule['hour'] if schedule['hour'] is not None else '시간 확인'}:{str(schedule['minute'] or 0).zfill(2)} · {schedule['timezone'] or '시간대 확인 필요'}")
    lines.append('입력: 연결한 관리 데이터 소스의 실행 시점 버전을 읽습니다.' if schedule else '입력: 이번 실행 자료 또는 연결한 표 파일을 읽습니다.')
    lines.append('처리 순서: '+' → '.join(n['label'] for n in workflow['nodes']))
    if any(n['tool']=='filter_sort' for n in workflow['nodes']):
        conditions=[]
        for f in plan['filters']:
            right=f['compare_field'] if f['compare_field'] else '' if f['operator'] in ['is_empty','not_empty'] else json.dumps(f['value'],ensure_ascii=False)
            conditions.append(f"{f['field']} {OPS[f['operator']]} {right}".strip())
        if conditions:lines.append('조건: '+(' AND ' if plan['match']=='all' else ' OR ').join(conditions))
        lines.append('빈 값: '+{'ask':'처리 정책 확인 필요','exclude':'제외','keep':'유지하되 다른 조건도 적용','error':'오류로 중단'}[plan['missing_policy']])
        lines.append('중복: '+{'ask':'처리 정책 확인 필요','keep':'유지','first':'원본 첫 행 유지','last':'원본 마지막 행 유지','error':'오류로 중단'}[plan['duplicate_policy']]+(' · 기준 '+plan['duplicate_field'] if plan['duplicate_field'] else ''))
        if plan['sort']:lines.append('정렬: '+', '.join(s['field']+' '+('숫자 ' if s['value_type']=='number' else '문자 ')+('오름차순' if s['direction']=='ascending' else '내림차순') for s in plan['sort']))
        else:lines.append('정렬: 원본 순서 유지')
        lines.append('숫자 비교·정렬에서 숫자로 변환할 수 없는 값은 실패로 기록합니다.')
    for node in workflow['nodes']:
        if node['tool']=='aggregate_table':lines.append('집계: '+(', '.join(node['settings']['fields'])+'별 ' if node['settings']['fields'] else '전체 ')+(str(node['settings'].get('sum_field'))+' 합계' if node['settings'].get('aggregation')=='sum' else '건수'))
        if node['tool']=='aggregate_table' and node['settings'].get('aggregation')=='sum':lines.append('합계 열의 빈 값·논리값·비숫자·비유한 숫자는 실행 실패로 기록합니다.')
        if schedule and node['tool']=='read_table':node['settings']['file_ids']=[]
        if node['tool']=='filter_sort':
            own=node['settings'].get('execution_plan') or plan
            node['settings']['instructions']=json.dumps(own,ensure_ascii=False)
            lines.append('노드 '+node['label']+'의 실제 규칙: '+json.dumps(own,ensure_ascii=False))
        if node['tool']=='write_result':node['settings']['format']=node['settings'].get('format') or plan['output_format']
    if any(n['tool']=='write_result' for n in workflow['nodes']):lines.append('출력: '+plan['output_format'].upper()+' · A4B 서버 보고서 보관함')
    draft['instructions']='\n'.join(lines)
    operations=[]
    if any(n['tool']=='filter_sort' for n in workflow['nodes']):operations.append('설정된 조건으로 행을 정리')
    if any(n['tool']=='aggregate_table' for n in workflow['nodes']):operations.append('설정된 항목의 건수 또는 합계를 집계')
    draft['purpose']=('설정된 예약 시간에 ' if schedule else '요청할 때 ')+'연결한 표 데이터를 읽고 '+('하고 '.join(operations) if operations else '처리')+'하여 결과를 제공합니다.'
    # These are original request records, not model-generated claims about quotations.
    users=[m['content'] for m in messages if m['role']=='user'][-30:]
    draft['requirements']=[{'description':f'사용자 요청 {i+1}','source_id':'user','quote':text[:2000]} for i,text in enumerate(users) if text.strip()]
    return draft
