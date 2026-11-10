"""Timezone-aware native recurrences, independent of storage and job claiming."""
from calendar import monthrange
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def schedule_errors(spec):
    issues = []
    frequency = spec.get('frequency')
    if frequency not in ('hourly', 'daily', 'weekly', 'monthly'):
        issues.append('예약 주기는 매시간·매일·매주·매월 중 하나여야 합니다.')
    zone = spec.get('timezone')
    if not isinstance(zone, str) or not zone:
        issues.append('예약 시간대를 알려주세요.')
    else:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            issues.append('유효한 IANA 시간대가 필요합니다.')
    minute = spec.get('minute')
    if type(minute) is not int or not 0 <= minute <= 59:
        issues.append('예약 실행 분을 0~59 중에서 알려주세요.')
    if frequency != 'hourly':
        hour = spec.get('hour')
        if type(hour) is not int or not 0 <= hour <= 23:
            issues.append('예약 실행 시를 0~23 중에서 알려주세요.')
    if frequency == 'weekly':
        weekday = spec.get('weekday')
        if type(weekday) is not int or not 0 <= weekday <= 6:
            issues.append('매주 실행할 요일을 알려주세요.')
    if frequency == 'monthly':
        monthday, month_end = spec.get('monthday'), spec.get('month_end', False)
        if type(month_end) is not bool:
            issues.append('매월 말일 실행 여부를 확인하세요.')
        elif month_end and monthday is not None:
            issues.append('매월 실행할 일자와 말일 중 하나만 선택하세요.')
        elif not month_end and (type(monthday) is not int or not 1 <= monthday <= 31):
            issues.append('매월 실행할 일자(1~31일) 또는 말일을 알려주세요.')
    return issues


def _wall_instant(day, hour, minute, zone):
    # Daily/weekly/monthly use the first occurrence of a repeated wall time.
    # Nonexistent DST times move forward by the transition, as older schedules did.
    candidate = datetime.combine(day, time(hour, minute), zone)
    return candidate.astimezone(timezone.utc).astimezone(zone)


def next_occurrence(spec, current=None):
    problems = schedule_errors(spec)
    if problems:
        raise ValueError(' / '.join(problems))
    current = current or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError('예약 기준 시각에는 시간대가 필요합니다.')
    current_utc = current.astimezone(timezone.utc)
    zone = ZoneInfo(spec['timezone'])
    local = current_utc.astimezone(zone)
    frequency, minute = spec['frequency'], spec['minute']
    if frequency == 'hourly':
        beginning = local.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        candidates = set()
        for offset in range(73):
            wall = beginning + timedelta(hours=offset, minutes=minute)
            for fold in (0, 1):
                candidate = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                # Skip missing wall times; count both real hours at a fall-back.
                if candidate.astimezone(zone).replace(tzinfo=None) == wall and candidate > current_utc:
                    candidates.add(candidate)
        if candidates:
            return min(candidates).isoformat()
    elif frequency in ('daily', 'weekly'):
        day = local.date()
        if frequency == 'weekly':
            day += timedelta(days=(spec['weekday'] - day.weekday()) % 7)
        for offset in range(3):
            candidate = _wall_instant(day + timedelta(days=offset * (7 if frequency == 'weekly' else 1)),
                                      spec['hour'], minute, zone).astimezone(timezone.utc)
            if candidate > current_utc:
                return candidate.isoformat()
    else:
        for offset in range(25):
            month_index = local.year * 12 + local.month - 1 + offset
            year, month = divmod(month_index, 12)
            month += 1
            last = monthrange(year, month)[1]
            day = last if spec.get('month_end', False) else spec['monthday']
            if day > last:
                continue
            from datetime import date
            candidate = _wall_instant(date(year, month, day), spec['hour'], minute, zone).astimezone(timezone.utc)
            if candidate > current_utc:
                return candidate.isoformat()
    raise ValueError('다음 예약 시각을 계산하지 못했습니다. 시간대와 반복 조건을 확인하세요.')
