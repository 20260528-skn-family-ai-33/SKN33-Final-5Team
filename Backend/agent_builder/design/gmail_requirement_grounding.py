"""Validate general Gmail requirements without requiring a whole-sentence recipe match."""
import re

from .gmail_summary import MAX_RESULTS, SCHEDULED_MAX_RESULTS, PREVIOUS_DAY_QUERY, UNREAD_QUERY


class MissingGmailSchedule(ValueError):
    def __init__(self,questions):
        super().__init__('예약 실행에 필요한 시각이나 시간대를 확인해주세요.')
        self.questions=questions


def validate_general_gmail_task(task,state,*,schedule_elsewhere=False):
    spec=task['spec']
    users=[message['content'] for message in state.get('messages',[]) if message.get('role')=='user']
    text='\n'.join(users)
    if not re.search(r'gmail|지메일',text,re.I) or not re.search(r'요약|summari[sz]',text,re.I):
        raise ValueError('Gmail 조회와 요약 요청의 사용자 근거를 확인해주세요.')
    if spec['request']!=task['quote']:
        raise ValueError('메일 요약 지침은 해당 사용자 요청 원문을 보존해야 합니다.')
    instructions=spec.get('summary_instructions')
    if instructions and not any(instructions in user for user in users):
        raise ValueError('추가 요약 지침이 사용자 요청에 없습니다.')
    # A count omitted by the user uses the existing bounded read budget. It is
    # an application resource limit, not an invented selection criterion.
    counts=[]
    for user in users:
        matches=list(re.finditer(r'(?:메일|이메일).{0,16}?([1-9]\d{0,3})\s*(?:개|건)|([1-9]\d{0,3})\s*(?:개|건)(?:의|만)?\s*(?:메일|이메일)',user))
        counts.extend(int(match[1] or match[2]) for match in matches)
    expected_count=counts[-1] if counts else SCHEDULED_MAX_RESULTS
    if not 1<=expected_count<=MAX_RESULTS or spec['max_results']!=expected_count:
        raise ValueError('메일 조회 한도가 사용자 요청 또는 앱의 기본 조회 한도와 다릅니다.')
    unread=False;previous_day=False
    for user in users:
        if re.search(r'안\s*읽은|읽지\s*않은|미열람|unread',user,re.I):
            unread=not bool(re.search(r'(?:안\s*읽은|읽지\s*않은|미열람).{0,14}(?:말고|제외|빼고)',user))
        elif re.search(r'(?:모든|전체)\s*(?:메일|이메일)',user):unread=False
        if re.search(r'전날|어제|previous\s*day',user,re.I):previous_day=True
    if unread and previous_day:
        raise ValueError('안 읽은 메일과 전날 기간을 함께 요청한 조회 조건을 보존해야 합니다.')
    query=UNREAD_QUERY if unread else PREVIOUS_DAY_QUERY if previous_day else None
    if spec.get('query')!=query:
        raise ValueError('메일 조회 조건이 사용자가 요청한 읽음 상태·조회 기간과 다릅니다.')
    from .requirement_clarification import missing_schedule_questions
    scheduled=spec['kind']=='gmail_scheduled_summary'
    from .execution_mode import effective_execution_mode
    if scheduled or effective_execution_mode(state)=='scheduled':
        missing=missing_schedule_questions(state)
        if missing:raise MissingGmailSchedule(missing)
    if scheduled:
        from .requirement_ledger import grounded_schedule
        if spec['schedule']!=grounded_schedule(state):
            raise ValueError('메일 예약이 사용자가 확인한 실행 시각·시간대와 다릅니다.')
    elif effective_execution_mode(state)=='scheduled' and not schedule_elsewhere:
        raise ValueError('사용자가 요청한 예약 실행 일정이 업무 목록에서 빠졌습니다.')
