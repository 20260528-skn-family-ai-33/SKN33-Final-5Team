"""Application-owned calendar context, fixed for each user turn and its repairs.

Calendar dates are never inferred from the execution host's local timezone. The
application default matches the existing workspace's Asia/Seoul default and can
be overridden with A4B_DEFAULT_TIMEZONE or an explicit user_timezone in state.
An empty/invalid configured zone remains unknown rather than silently falling
back. This context resolves request dates; it does not authorize a schedule.
"""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TEMPORAL_INSTRUCTIONS = '''temporal_context는 앱이 제공한 요청 기준시각과 시간대입니다.
단발 요청의 오늘·어제·내일은 해당 사용자 발화의 기준시각과 시간대로 해석합니다. relative_days가 있으면 날짜를 다시 묻지 않습니다.
지난주는 relative_periods.last_week의 월요일부터 다음 월요일 전까지입니다. 별도 회계 주간을 명시한 경우 그 정의를 우선하고 불명확한 부분만 확인합니다.
이전 발화의 상대 날짜는 user_message_anchors의 해당 source_message 기준으로 유지합니다. 후속 답변이나 검증 재시도로 날짜를 이동하지 않습니다.
timezone이 null이면 결과가 달라지는 상대 날짜의 기준 시간대만 확인하고 서버 시간대를 추측하지 않습니다.
반복 예약의 전날·어제는 매 실행일 기준 상대 기간입니다. 현재 날짜의 절대 검색 기간으로 고정하지 않습니다.
요청 해석의 기본 시간대가 있다고 예약의 미정 시각·주기를 임의로 확정하지 않습니다.'''


def _utc_now():
    return datetime.now(timezone.utc)


def validate_user_timezone(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError('요청 시간대를 확인하세요.')
    try:
        return ZoneInfo(value).key
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError('요청 시간대를 확인하세요.') from error


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('요청 기준시각에는 UTC 오프셋이 필요합니다.')
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def _zone(state):
    if 'user_timezone' in state:
        value, source = state['user_timezone'], 'user'
    elif 'A4B_DEFAULT_TIMEZONE' in os.environ:
        value, source = os.environ['A4B_DEFAULT_TIMEZONE'], 'configuration'
    else:
        value, source = 'Asia/Seoul', 'application_default'
    try:
        zone = ZoneInfo(value) if isinstance(value, str) and value.strip() else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    return (zone.key if zone else None), source


def _calendar(reference_time, zone):
    if not zone:
        return {'local_time': None, 'relative_days': {}, 'relative_periods': {}}
    local = _instant(reference_time).astimezone(ZoneInfo(zone))
    days = {}
    for name, offset in (('yesterday', -1), ('today', 0), ('tomorrow', 1)):
        date = local.date() + timedelta(days=offset)
        start = datetime.combine(date, time.min, tzinfo=local.tzinfo)
        end = datetime.combine(date + timedelta(days=1), time.min, tzinfo=local.tzinfo)
        days[name] = {'date': date.isoformat(), 'start': start.isoformat(), 'end': end.isoformat(),
                      'start_epoch': int(start.timestamp()), 'end_epoch': int(end.timestamp())}
    # Calendar weeks start Monday; use local midnights rather than fixed 168-hour offsets.
    monday = local.date() - timedelta(days=local.weekday())
    start = datetime.combine(monday - timedelta(days=7), time.min, tzinfo=local.tzinfo)
    end = datetime.combine(monday, time.min, tzinfo=local.tzinfo)
    return {'local_time': local.isoformat(), 'relative_days': days,
            'relative_periods': {'last_week': {'start': start.isoformat(), 'end': end.isoformat(),
                'start_epoch': int(start.timestamp()), 'end_epoch': int(end.timestamp()),
                'week_starts_on': 'monday'}}}


def temporal_context(state, *, now=None):
    """Return a serializable context; callers persist it before graph planning.

    A matching transcript reuses its clock even across retries/restarts. Every
    added user message receives its own anchor, so answering a question tomorrow
    does not move yesterday in the original request. Older unanchored history is
    explicitly marked unknown rather than assigned a fabricated receipt time.
    """
    users = [m.get('content', '') for m in state.get('messages', []) if m.get('role') == 'user']
    hashes = [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in users]
    fingerprint = hashlib.sha256(json.dumps(hashes).encode()).hexdigest()
    zone, source = _zone(state)
    cached = state.get('temporal_context')
    if (isinstance(cached, dict) and cached.get('version') == 2
            and cached.get('request_fingerprint') == fingerprint
            and cached.get('timezone') == zone and cached.get('timezone_source') == source):
        return deepcopy(cached)
    same_turn = isinstance(cached, dict) and cached.get('request_fingerprint') == fingerprint
    reference = (_iso(_instant(cached['reference_time'])) if same_turn else
                 _iso(_instant(now if now is not None else _utc_now())))
    old = cached.get('user_message_anchors', []) if isinstance(cached, dict) else []
    anchors = []
    for index, digest in enumerate(hashes):
        previous = old[index] if index < len(old) and isinstance(old[index], dict) else {}
        if previous.get('content_sha256') == digest and not (same_turn and index == len(hashes) - 1):
            anchor = deepcopy(previous)
            if 'relative_periods' not in anchor and anchor.get('reference_time') and anchor.get('timezone'):
                anchor['relative_periods'] = _calendar(anchor['reference_time'], anchor['timezone'])['relative_periods']
        elif index == len(hashes) - 1:
            anchor = {'source_message': index, 'content_sha256': digest,
                      'reference_time': reference, 'timezone': zone,
                      **_calendar(reference, zone)}
        else:
            anchor = {'source_message': index, 'content_sha256': digest,
                      'reference_time': None, 'timezone': None,
                      'local_time': None, 'relative_days': {}}
        anchors.append(anchor)
    return {'version': 2, 'request_fingerprint': fingerprint, 'reference_time': reference,
            'timezone': zone, 'timezone_source': source, **_calendar(reference, zone),
            'user_message_anchors': anchors}


def ensure_temporal_context(state, *, now=None):
    """Graph update emitted before the first interpretation stage of a turn."""
    return {'temporal_context': temporal_context(state, now=now)}


def selected_day(state, selection):
    """Resolve a reviewed relative day against its originating turn's clock."""
    anchors = (state.get('temporal_context') or {}).get('user_message_anchors', [])
    index = selection.get('period_source_message')
    if type(index) is not int or not 0 <= index < len(anchors):
        return None
    anchor = anchors[index]
    zone = selection.get('calendar_timezone') or anchor.get('timezone')
    if not zone or not anchor.get('reference_time'):
        return None
    calendar = anchor if zone == anchor.get('timezone') else _calendar(anchor['reference_time'], zone)
    return calendar.get('relative_days', {}).get(selection['period'])


@lru_cache(maxsize=128)
def _zone_names(zone):
    from babel.dates import get_timezone_location
    location = ZoneInfo(zone)
    return (zone, *(get_timezone_location(location, locale=locale, return_city=True) for locale in ('ko', 'en')))


def calendar_qualifier(value, zone):
    """A date qualifier must name exactly the supplied IANA zone or its CLDR city."""
    if not zone or not isinstance(value, str):
        return False
    names = '|'.join(re.escape(name) for name in _zone_names(zone))
    return bool(re.fullmatch(rf'(?:{names})\s*(?:의\s*)?(?:현지\s*)?(?:시간대?|시각|달력)?\s*'
                             r'(?:기준(?:으로)?|으로|로|에서)?', value.strip(), re.I))


def source_calendar_zone(state, index):
    """Ground a location which directly qualifies a single relative receipt day."""
    anchors = (state.get('temporal_context') or {}).get('user_message_anchors', [])
    users = [m['content'] for m in state.get('messages', []) if m['role']=='user']
    if type(index) is not int or not 0 <= index < min(len(anchors), len(users)):
        return None
    zone = anchors[index].get('timezone')
    days = list(re.finditer(r'오늘|어제|전날|\b(?:today|yesterday)\b', users[index], re.I))
    if len(days) != 1:
        return None
    prefix = users[index][:days[0].start()].strip()
    prefix = re.sub(r'^(?:(?:내|제)\s*)?(?:Gmail|지메일)(?:에서|의)?\s*', '', prefix, flags=re.I)
    return (zone, prefix) if calendar_qualifier(prefix, zone) else None
