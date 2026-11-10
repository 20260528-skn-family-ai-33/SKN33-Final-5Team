"""Preserve explicit user-supplied date parameters; never infer missing values.

This checks structured parameters in the latest user turn, not attached document
contents or reference answers. Natural-language-only criteria remain the request
extractor's responsibility. No tasks or workflow nodes are inserted here.
"""
import json


def requested_dates(state):
    users=[m['content'] for m in state.get('messages',[]) if m['role']=='user']
    if not users:return []
    text=users[-1];decoder=json.JSONDecoder();found=[];position=0
    while True:
        start=text.find('{',position)
        if start<0:break
        try:value,length=decoder.raw_decode(text[start:])
        except ValueError:position=start+1;continue
        position=start+length
        if not isinstance(value,dict):continue
        keys=('date_column','date_input_format','date_timezone')
        if not all(k in value for k in keys):continue
        if any(not isinstance(value[k],str) or not value[k].strip() for k in keys):
            raise ValueError('날짜 대상 열·입력 형식·시간대는 비어 있지 않은 문자열이어야 합니다.')
        spec=dict(kind='parse_dates',fields=[value['date_column']],
                  input_format=value['date_input_format'],timezone=value['date_timezone'])
        if spec not in found:found.append(spec)
    return found


def validate_requested_dates(plan,state):
    for expected in requested_dates(state):
        if not any(t['spec']['kind']=='parse_dates'
                   and set(t['spec']['fields'])==set(expected['fields'])
                   and t['spec']['input_format']==expected['input_format']
                   and t['spec']['timezone']==expected['timezone'] for t in plan['tasks']):
            raise ValueError('사용자가 명시한 날짜 변환이 업무 목록에서 누락되거나 달라졌습니다. '
                             'parse_dates에 대상 열·입력 형식·시간대를 그대로 기록하세요: '+
                             json.dumps(expected,ensure_ascii=False))
