"""Narrow source-free Gmail summaries and execution-time calendar-day queries.

A Gmail ``query`` may hold one relative calendar-day placeholder that is resolved when the
workflow runs, from the run's execution time:

- ``{{gmail.previous_day}}``: the previous local calendar day (00:00 to 00:00);
- ``{{gmail.today}}``: the current local calendar day (00:00 to the next 00:00).

An IANA timezone may follow a ``|`` (``{{gmail.previous_day|Asia/Seoul}}``). The zone is,
in order: the placeholder's zone, the workflow schedule's zone, ``A4B_DEFAULT_TIMEZONE``
(Asia/Seoul when the variable is absent, team decision 2026-11-11). Setting it explicitly
empty disables the default, and a run without any zone then fails instead of assuming one. Day boundaries are local
midnights, so a daylight-saving day is 23 or 25 hours long.
"""
import os
import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREVIOUS_DAY_QUERY='{{gmail.previous_day}}'
TODAY_QUERY='{{gmail.today}}'
_PLACEHOLDER=re.compile(r'\{\{gmail\.(previous_day|today)(?:\|([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){0,2}))?\}\}')
_DAY_OFFSET={'previous_day':-1,'today':0}


def parse_period(query):
    """(period, explicit_zone) for a whole-query placeholder, else None."""
    match=_PLACEHOLDER.fullmatch(query) if isinstance(query,str) else None
    return (match[1],match[2]) if match else None


def _zone(config,explicit=None):
    name=explicit or (config.get('schedule') or {}).get('timezone') or os.getenv('A4B_DEFAULT_TIMEZONE','Asia/Seoul').strip() or None
    if not name:
        raise ValueError('timezone is required')
    return ZoneInfo(name)


def _reference(config):
    reference_text=config['_execution_time']
    if not isinstance(reference_text,str):raise ValueError('Execution time is required')
    reference=datetime.fromisoformat(reference_text.replace('Z','+00:00'))
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError('Timezone is required')
    return reference


def period_window(config,period='previous_day',explicit_zone=None):
    """[start, end) of the requested local calendar day for this run."""
    try:
        zone=_zone(config,explicit_zone)
        reference=_reference(config)
    except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError) as error:
        raise ValueError('날짜 기준 메일 조회에는 시간대(조회 조건·예약·서버 기본값 중 하나)와 실행 기준 시각이 필요합니다.') from error
    day=reference.astimezone(zone).date()+timedelta(days=_DAY_OFFSET[period])
    start=datetime.combine(day,time.min,zone)
    end=datetime.combine(day+timedelta(days=1),time.min,zone)
    return start,end


def previous_day_window(config):
    """Backward-compatible: the previous day in the schedule's timezone."""
    return period_window(config,'previous_day')


def resolve_gmail_query(query,config):
    parsed=parse_period(query)
    if not parsed:
        return query
    start,end=period_window(config,*parsed)
    return f'after:{int(start.timestamp())} before:{int(end.timestamp())} -in:sent -in:drafts'


def query_period(query,config):
    """The resolved period recorded with the result, or None for an ordinary query."""
    parsed=parse_period(query)
    if not parsed:
        return None
    start,end=period_window(config,*parsed)
    return {'period':parsed[0],'start':start.isoformat(),'end':end.isoformat(),'timezone':str(start.tzinfo)}
