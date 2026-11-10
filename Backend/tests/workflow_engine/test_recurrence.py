"""Calendar recurrence boundaries are checked without running a clock or worker."""
from datetime import datetime, timezone

import pytest

from contracts.models import ScheduleSpec
from workflow_engine.scheduling.recurrence import next_occurrence, schedule_errors


BASE = dict(frequency='daily', timezone='Asia/Seoul', hour=9, minute=0, weekday=None)


@pytest.mark.parametrize('spec,current,expected', [
    (BASE, '2026-11-09T23:59:59+00:00', '2026-11-10T00:00:00+00:00'),
    (BASE, '2026-11-10T00:00:00+00:00', '2026-11-11T00:00:00+00:00'),
    ({**BASE, 'frequency':'weekly', 'weekday':0}, '2026-10-27T00:00:00+00:00', '2026-11-02T00:00:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None}, '2026-11-10T00:00:00+00:00', '2026-11-10T01:00:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None, 'minute':15}, '2026-11-10T00:15:00+00:00', '2026-11-10T01:15:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None, 'minute':15}, '2026-11-10T00:14:59+00:00', '2026-11-10T00:15:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None, 'timezone':'Asia/Kathmandu'}, '2026-11-10T00:00:00+00:00', '2026-11-10T00:15:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'monthday':1}, '2026-11-01T00:00:00+00:00', '2026-12-01T00:00:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'monthday':31}, '2026-01-31T00:00:00+00:00', '2026-03-31T00:00:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'monthday':30}, '2026-01-30T00:00:00+00:00', '2026-03-30T00:00:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'month_end':True}, '2026-01-31T00:00:00+00:00', '2026-02-28T00:00:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'month_end':True}, '2028-01-31T00:00:00+00:00', '2028-02-29T00:00:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'monthday':31}, '2026-12-31T00:00:00+00:00', '2027-01-31T00:00:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None, 'minute':30, 'timezone':'America/New_York'}, '2026-03-08T06:45:00+00:00', '2026-03-08T07:30:00+00:00'),
    ({**BASE, 'frequency':'hourly', 'hour':None, 'minute':30, 'timezone':'America/New_York'}, '2026-11-01T05:45:00+00:00', '2026-11-01T06:30:00+00:00'),
    ({**BASE, 'hour':2, 'minute':30, 'timezone':'America/New_York'}, '2026-03-08T05:00:00+00:00', '2026-03-08T07:30:00+00:00'),
    ({**BASE, 'hour':1, 'minute':30, 'timezone':'America/New_York'}, '2026-11-01T06:15:00+00:00', '2026-11-02T06:30:00+00:00'),
    ({**BASE, 'frequency':'monthly', 'monthday':1, 'hour':1, 'minute':30, 'timezone':'America/New_York'}, '2026-11-01T06:15:00+00:00', '2026-12-01T06:30:00+00:00'),
])
def test_next_slot_is_a_real_strictly_future_instant(spec, current, expected):
    result = next_occurrence(spec, datetime.fromisoformat(current))
    assert result == expected
    assert datetime.fromisoformat(result) > datetime.fromisoformat(current)


@pytest.mark.parametrize('change', [
    dict(frequency='cron'), dict(timezone=None), dict(timezone='Not/AZone'), dict(hour=None),
    dict(minute=None), dict(minute=60), dict(frequency='weekly', weekday=None),
    dict(frequency='monthly'), dict(frequency='monthly', monthday=0),
    dict(frequency='monthly', monthday=32), dict(frequency='monthly', monthday=True),
    dict(frequency='monthly', monthday=15, month_end=True),
])
def test_incomplete_or_ambiguous_specs_cannot_run(change):
    spec = {**BASE, **change}
    assert schedule_errors(spec)
    with pytest.raises(ValueError):
        next_occurrence(spec)


def test_legacy_daily_and_weekly_json_keep_working_without_new_fields():
    parsed = ScheduleSpec.model_validate(BASE)
    assert parsed.monthday is None and parsed.month_end is False
    assert not schedule_errors(BASE)
    assert not schedule_errors({**BASE, 'frequency':'weekly', 'weekday':2})


def test_hourly_does_not_require_a_wall_clock_hour_or_a_weekday():
    spec = {**BASE, 'frequency':'hourly', 'hour':None}
    assert not schedule_errors(spec)
    assert ScheduleSpec.model_validate(spec).hour is None


def test_repeated_hourly_calculation_after_each_claim_never_reuses_a_slot():
    spec = {**BASE, 'frequency':'hourly', 'hour':None}
    current = datetime(2026, 12, 31, 23, tzinfo=timezone.utc)
    slots = []
    for _ in range(4):
        current = datetime.fromisoformat(next_occurrence(spec, current))
        slots.append(current)
    assert len(set(slots)) == 4
    assert slots[0].isoformat() == '2027-01-01T00:00:00+00:00'


def test_naive_reference_time_is_not_assumed_to_be_host_local_time():
    with pytest.raises(ValueError, match='시간대'):
        next_occurrence(BASE, datetime(2026, 11, 10, 9))
