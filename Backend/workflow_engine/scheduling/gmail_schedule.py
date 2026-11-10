"""Narrow source-free Gmail summaries and execution-time calendar-day queries."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREVIOUS_DAY_QUERY='{{gmail.previous_day}}'


def previous_day_window(config):
    spec=config.get('schedule') or {}
    try:
        zone=ZoneInfo(spec['timezone'])
        reference_text=config['_execution_time']
        if not isinstance(reference_text,str):raise ValueError('Execution time is required')
        reference=datetime.fromisoformat(reference_text.replace('Z','+00:00'))
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError('Timezone is required')
    except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError) as error:
        raise ValueError('전날 메일 조회에는 예약 시간대와 실행 기준 시각이 필요합니다.') from error
    today=reference.astimezone(zone).date()
    start=datetime.combine(today-timedelta(days=1),time.min,zone)
    end=datetime.combine(today,time.min,zone)
    return start,end


def resolve_gmail_query(query,config):
    if query!=PREVIOUS_DAY_QUERY:
        return query
    start,end=previous_day_window(config)
    return f'after:{int(start.timestamp())} before:{int(end.timestamp())} -in:sent -in:drafts'
