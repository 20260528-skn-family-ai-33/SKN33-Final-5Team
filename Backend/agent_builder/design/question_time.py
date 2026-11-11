"""Clock input contracts shared by question projection and answer validation."""
import re


_CLOCK_KEY = re.compile(r'^(?:schedule\.(?:time|hour|execution_time|start_time)|execution[_.]time(?:[_.]setting)?|run[_.]time|start[_.]time|time_of_day)$',re.I)
_CLOCK_EXAMPLE = re.compile(r'(?:HH\s*:\s*MM|(?:[01]?\d|2[0-3]):[0-5]\d)',re.I)


def clock_question(question):
    key=question.get('key') or ''
    if _CLOCK_KEY.fullmatch(key):return True
    text=question.get('text') or ''
    if not re.search(r'소요|걸리|걸릴|대기|제한\s*시간|타임아웃|timeout|duration',text,re.I) and re.search(r'(?:실행|예약|시작).{0,12}(?:시각|시간)|몇\s*시(?!간)',text):return True
    # An HH:MM example disambiguates a clock from a duration. Planners also
    # write "구체적인 시간" rather than the exact "실행 시간" wording.
    return bool(_CLOCK_EXAMPLE.search(text) and re.search(r'시각|몇\s*시|시간|time\s+of\s+day|execution\s+time',text,re.I))


def clock_response(question,response):
    if response and response.get('type') in ('number','text','time') and clock_question(question):
        return {'type':'time'}
    return response


def execution_clock_question(question):
    """A data field's clock value is not a workflow start time."""
    key=question.get('key') or ''
    if key.startswith('schedule.'):return bool(_CLOCK_KEY.fullmatch(key))
    text=question.get('text') or ''
    if re.search(r'(?:조회|검색|수신|집계|분석).{0,12}(?:기간|날짜|시간\s*범위)|(?:회의|약속|행사|배송|제출|마감).{0,12}(?:시간|시각|시작|종료)',text):return False
    return bool(_CLOCK_KEY.fullmatch(key) and not key.startswith(('start.','start_')) or
                clock_question(question) and re.search(r'실행|예약|매일|매주|매월|매시간|execution|scheduled?',text,re.I))


def schedule_question_keys(question):
    """Native trigger fields requested by one question, excluding task times."""
    if isinstance(question,str):question={'key':question,'text':question}
    key=(question.get('key') or '').replace('_','.')
    text=question.get('text') or ''
    # Even a model-assigned native key cannot authorize dropping an unanswered
    # query window or event time that was combined into the same sentence.
    if re.search(r'(?:조회|검색|수신|집계|분석).{0,12}(?:기간|날짜|시간\s*범위)|(?:회의|약속|행사|배송|제출|마감).{0,12}(?:시간|시각|시작|종료)',text):return set()
    if key.startswith('schedule.'):
        if key in ('schedule.time','schedule.execution.time','schedule.start.time'):
            result={'schedule.hour','schedule.minute'}
        else:result={key}
    else:
        from .execution_mode import execution_schedule_question
        if not execution_schedule_question(question):return set()
        result=set()
        if execution_clock_question(question) or clock_question(question):result.update(('schedule.hour','schedule.minute'))
    if re.search(r'주기|얼마나\s*자주|매시간[·/ ,]+매일|frequency',text,re.I):result.add('schedule.frequency')
    if re.search(r'요일|weekday|day\s+of\s+(?:the\s+)?week',text,re.I):result.add('schedule.weekday')
    if re.search(r'몇\s*일|며칠|일자|monthday|day\s+of\s+(?:the\s+)?month',text,re.I):result.add('schedule.monthday')
    if re.search(r'말일|마지막\s*날|month.end',text,re.I):result.add('schedule.month_end')
    return result


def time_parts(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value.strip()):
        raise ValueError('실행 시각은 09:00처럼 24시간 기준 HH:MM 형식으로 입력해주세요.')
    hour,minute=value.strip().split(':')
    return int(hour),int(minute)
