"""Count source-matched mail cards in the final Slack digest."""
import re
from collections import defaultdict, deque
from email.utils import parseaddr

from agent_builder.design.gmail_slack import SUMMARY_CARD_FORMAT


_TITLE = re.compile(r'^[ \t]*(?:(?:[-•*]|\d+[.)])[ \t]*)?(?:\*\*)?(?:제목|subject)(?:\*\*)?[ \t]*[:：](?:\*\*)?[ \t]*(.+)$', re.I | re.M)
_SENDER = re.compile(r'^[ \t]*(?:[-•*][ \t]*)?(?:\*\*)?(?:보낸 사람|발신자|from)(?:\*\*)?[ \t]*[:：](?:\*\*)?[ \t]*(.+)$', re.I | re.M)
_METADATA = re.compile(r'^[ \t]*citations[ \t]*[:=][ \t]*\[[^\r\n]*\][ \t]*$', re.I | re.M)
_POINT = re.compile(r'^[ \t]*(?:[-•*][ \t]*)?(?:\*\*)?(?:핵심(?: 내용)?|요약)(?:\*\*)?[ \t]*[:：](?:\*\*)?', re.M)


def is_slack_card_summary(config, node):
    instructions = (node['settings'].get('instructions') or '').rstrip()
    if instructions.endswith(SUMMARY_CARD_FORMAT):
        binding = {'input': 'text', 'node_id': node['id'], 'output': 'text'}
        return any(step['tool'] == 'slack.post_message' and binding in step.get('bindings', [])
                   for step in config.get('workflow', {}).get('nodes', []))

    from agent_builder.conditions.input_requirements import _legacy_fixed_gmail_slack_summary

    return _legacy_fixed_gmail_slack_summary({**config, 'request_input_mode': None})


def _mail_key(title, sender):
    address = parseaddr(sender)[1]
    sender = address if '@' in address else sender
    return (' '.join(title.split()).casefold(), ' '.join(sender.split()).casefold())


def summarized_mail_ids(text, documents):
    """Count a card only when it has content and matches a collected message."""
    available = defaultdict(deque)
    for doc in documents:
        if doc.get('body_unavailable'):
            continue
        title = doc['file_name']
        prefix = f'제목: {title}\n보낸 사람: '
        source = doc['text']
        if source.startswith(prefix):
            sender, separator, _ = source[len(prefix):].partition('\n\n')
            if separator and sender.strip():
                available[_mail_key(title, sender)].append(doc['source_id'])

    headings = list(_TITLE.finditer(text))
    matched = set()
    seen_cards = set()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        card = text[heading.end():end]
        sender = _SENDER.search(card)
        if not sender:
            continue
        key = _mail_key(heading[1], sender[1])
        choices = available.get(key)
        content = _POINT.sub('', _METADATA.sub('', card[sender.end():])).strip()
        card_key = (key, ' '.join(content.split()).casefold())
        if choices and card_key not in seen_cards and any(character.isalnum() for character in content):
            matched.add(choices.popleft())
            seen_cards.add(card_key)
    return matched


def append_summary_counts(text, read_count, summarized_count):
    omitted = read_count - summarized_count
    return f'{text}\n\n메일 처리 결과: 읽음 {read_count}건 · 요약 {summarized_count}건 · 누락 {omitted}건'
