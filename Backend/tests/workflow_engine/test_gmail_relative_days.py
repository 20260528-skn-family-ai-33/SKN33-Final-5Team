"""One-off and scheduled "어제/오늘" Gmail periods (team clarification set T01-T06).

The day is the local calendar day of the run's execution time, so a daylight-saving day is
23 or 25 hours long, and no timezone is assumed when none is configured.
"""
from datetime import datetime

import pytest

from workflow_engine.scheduling.gmail_schedule import period_window, resolve_gmail_query, query_period, parse_period


def window(query, execution_time, schedule=None):
    config = {'_execution_time': execution_time, **({'schedule': schedule} if schedule else {})}
    parsed = parse_period(query)
    start, end = period_window(config, *parsed)
    # Elapsed time from epoch seconds (what the Gmail query uses); subtracting two datetimes
    # that share a ZoneInfo would give wall-clock hours instead.
    return start.isoformat(), end.isoformat(), (end.timestamp() - start.timestamp()) / 3600


@pytest.mark.parametrize('case,query,execution,start,end,hours', [
    ('T01 서울 2026-11-10', '{{gmail.previous_day|Asia/Seoul}}', '2026-11-10T00:30:00Z',
     '2026-11-09T00:00:00+09:00', '2026-11-10T00:00:00+09:00', 24),
    ('T02 뉴욕 오늘, 2026-11-10 01:00 UTC', '{{gmail.today|America/New_York}}', '2026-11-10T01:00:00Z',
     '2026-11-09T00:00:00-05:00', '2026-11-10T00:00:00-05:00', 24),
    ('T03 서울 자정 직전', '{{gmail.previous_day|Asia/Seoul}}', '2026-11-09T23:59:59+09:00',
     '2026-11-08T00:00:00+09:00', '2026-11-09T00:00:00+09:00', 24),
    ('T04 서울 연도 경계', '{{gmail.previous_day|Asia/Seoul}}', '2027-01-01T00:00:00+09:00',
     '2026-12-31T00:00:00+09:00', '2027-01-01T00:00:00+09:00', 24),
    ('T05 뉴욕 서머타임 시작 다음 날', '{{gmail.previous_day|America/New_York}}', '2026-03-09T12:00:00Z',
     '2026-03-08T00:00:00-05:00', '2026-03-09T00:00:00-04:00', 23),
    ('T06 뉴욕 서머타임 종료 다음 날', '{{gmail.previous_day|America/New_York}}', '2026-11-02T12:00:00Z',
     '2026-11-01T00:00:00-04:00', '2026-11-02T00:00:00-05:00', 25),
])
def test_local_calendar_day_of_the_run(case, query, execution, start, end, hours):
    assert window(query, execution) == (start, end, hours), case


def test_zone_falls_back_to_the_schedule_then_the_server_default(monkeypatch):
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', '')
    assert window('{{gmail.previous_day}}', '2026-11-10T00:30:00Z', {'timezone': 'Asia/Seoul'})[0] == '2026-11-09T00:00:00+09:00'
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', 'America/New_York')
    assert window('{{gmail.today}}', '2026-11-10T01:00:00Z')[0] == '2026-11-09T00:00:00-05:00'
    # an explicit zone in the query wins over the schedule and the default
    assert window('{{gmail.today|Asia/Seoul}}', '2026-11-10T01:00:00Z', {'timezone': 'UTC'})[0] == '2026-11-10T00:00:00+09:00'


def test_default_timezone_is_seoul_unless_disabled(monkeypatch):
    monkeypatch.delenv('A4B_DEFAULT_TIMEZONE', raising=False)
    assert window('{{gmail.previous_day}}', '2026-11-10T00:30:00Z')[0] == '2026-11-09T00:00:00+09:00'
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', '')  # explicitly disabled: no zone is assumed
    with pytest.raises(ValueError, match='시간대'):
        resolve_gmail_query('{{gmail.previous_day}}', {'_execution_time': '2026-11-10T00:30:00Z'})
    with pytest.raises(ValueError):
        resolve_gmail_query('{{gmail.today|Not/AZone}}', {'_execution_time': '2026-11-10T00:30:00Z'})


def test_resolved_query_and_recorded_period():
    config = {'_execution_time': '2026-11-10T00:30:00Z'}
    query = resolve_gmail_query('{{gmail.previous_day|Asia/Seoul}}', config)
    start = int(datetime.fromisoformat('2026-11-09T00:00:00+09:00').timestamp())
    assert query == f'after:{start} before:{start + 86400} -in:sent -in:drafts'
    assert query_period('{{gmail.previous_day|Asia/Seoul}}', config)['timezone'] == 'Asia/Seoul'
    assert resolve_gmail_query('from:boss is:unread', config) == 'from:boss is:unread'
    assert query_period('from:boss', config) is None and parse_period('{{gmail.yesterday}}') is None
