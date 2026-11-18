"""Attach source timestamps and count matched cards in the final Slack digest."""
import re
from collections import defaultdict, deque
from datetime import datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_builder.design.gmail_slack import SUMMARY_CARD_FORMAT, SUMMARY_CARD_FORMATS


_TITLE = re.compile(r'^[ \t]*(?:(?:[-•*]|\d+[.)])[ \t]*)?(?:\*\*)?(?:제목|subject)(?:\*\*)?[ \t]*[:：](?:\*\*)?[ \t]*(.+)$', re.I | re.M)
_SENDER = re.compile(r'^[ \t]*(?:[-•*][ \t]*)?(?:\*\*)?(?:보낸 사람|발신자|from)(?:\*\*)?[ \t]*[:：](?:\*\*)?[ \t]*(.+)$', re.I | re.M)
_SENT_TIME = re.compile(r'^[ \t]*(?:(?:[-•*]|\d+[.)]|#{1,6})[ \t]*)?(?:\*\*)?(?:보낸 시간|보낸 시각|sent at)(?:\*\*)?[ \t]*[:：][^\n]*(?:\n|$)', re.I | re.M)
_METADATA = re.compile(r'^[ \t]*citations[ \t]*[:=][ \t]*\[[^\r\n]*\][ \t]*$', re.I | re.M)
_POINT = re.compile(r'^[ \t]*(?:[-•*][ \t]*)?(?:\*\*)?(?:핵심(?: 내용)?|요약)(?:\*\*)?[ \t]*[:：](?:\*\*)?', re.M)
_UNKNOWN_SENT_TIME = '확인할 수 없음'


def is_slack_card_summary(config, node):
    instructions = (node['settings'].get('instructions') or '').rstrip()
    if instructions.endswith(SUMMARY_CARD_FORMATS):
        binding = {'input': 'text', 'node_id': node['id'], 'output': 'text'}
        return any(step['tool'] == 'slack.post_message' and binding in step.get('bindings', [])
                   for step in config.get('workflow', {}).get('nodes', []))

    from agent_builder.conditions.input_requirements import _legacy_fixed_gmail_slack_summary

    return _legacy_fixed_gmail_slack_summary({**config, 'request_input_mode': None})


def compact_slack_instructions(instructions):
    """Replace the generated card format while preserving the saved task's instructions."""
    instructions = (instructions or '').rstrip()
    for card_format in SUMMARY_CARD_FORMATS:
        if instructions.endswith(card_format):
            instructions = instructions.removesuffix(card_format).rstrip()
            break
    return (instructions + SUMMARY_CARD_FORMAT
            + '\n메일마다 요약을 뒷받침하는 대표 근거 ID 1개만 citations에 반환하세요. '
            '보낸 시간, 메일 처리 결과와 조회 안내는 시스템이 작성합니다. '
            '요약 본문에 보낸 시간이나 집계, citations 목록을 넣지 마세요.')


def _mail_key(title, sender):
    address = parseaddr(sender)[1]
    sender = address if '@' in address else sender
    return (' '.join(title.split()).casefold(), ' '.join(sender.split()).casefold())


def _sent_time(value):
    if isinstance(value, str):
        try:
            sent_at = datetime.fromisoformat(value)
            if sent_at.utcoffset() is not None:
                return sent_at.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M (%Z)')
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            pass
    return _UNKNOWN_SENT_TIME


def annotate_mail_summaries(text, documents):
    """Attach the Gmail header time without guessing which duplicate header supplied it."""
    available = defaultdict(deque)
    sent_times = defaultdict(set)
    for doc in documents:
        title = doc['file_name']
        prefix = f'제목: {title}\n보낸 사람: '
        source = doc['text']
        if source.startswith(prefix):
            sender, separator, _ = source[len(prefix):].partition('\n\n')
            if separator and sender.strip():
                key = _mail_key(title, sender)
                sent_times[key].add(_sent_time(doc.get('sent_at')))
                if not doc.get('body_unavailable'):
                    available[key].append(doc['source_id'])

    text = _SENT_TIME.sub('', text.replace('\r\n', '\n').replace('\r', '\n'))
    headings = list(_TITLE.finditer(text))
    fragments = [text[:headings[0].start()] if headings else text]
    matched = set()
    seen_cards = set()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        card = text[heading.end():end]
        sender = _SENDER.search(card)
        if not sender:
            fragments.append(heading[0] + card)
            continue
        key = _mail_key(heading[1], sender[1])
        times = sent_times.get(key)
        sent_time = next(iter(times)) if times is not None and len(times) == 1 else _UNKNOWN_SENT_TIME
        body = card[sender.end():]
        fragments.append(f'{heading[0]}{card[:sender.end()]}\n보낸 시간: {sent_time}{body}')
        choices = available.get(key)
        content = _POINT.sub('', _METADATA.sub('', body)).strip()
        card_key = (key, ' '.join(content.split()).casefold())
        if choices and card_key not in seen_cards and any(character.isalnum() for character in content):
            matched.add(choices.popleft())
            seen_cards.add(card_key)
    return ''.join(fragments), matched


def append_summary_counts(text, read_count, summarized_count):
    omitted = read_count - summarized_count
    return f'{text}\n\n메일 처리 결과: 읽음 {read_count}건 · 요약 {summarized_count}건 · 누락 {omitted}건'
