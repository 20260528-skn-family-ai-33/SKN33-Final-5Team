"""Check explicit requests to save a local CSV or JSON report."""
import re

_REPORT = re.compile(
    r'(?<![A-Za-z])(CSV|JSON)\s*(?:파일|형식|산출물)?\s*(?:으로|로|을|를)?\s*'
    r'(?:(?:변환(?:해서|하고)\s*)?저장(?=해|하|할|\s|[.!?,]|$)|만들(?=어|기|\s|[.!?,]|$)|내보내(?=\s|줘|주|기|[.!?,]|$))', re.I)
_NEGATED_SAVE = re.compile(r'\s*(?:하지\s*(?:마|말|않)|(?:주)?지\s*(?:마|말|않)|은\s*하지|어\s*주지\s*(?:마|말|않)|지\s*(?:마|말|않)|말고)')


def requested_report_formats(user_messages):
    requested = set()
    for message in user_messages:
        positive, negative = set(), set()
        for match in _REPORT.finditer(message):
            target = negative if _NEGATED_SAVE.match(message[match.end():]) else positive
            target.add(match[1].lower())
        if positive:
            requested = positive - negative
        elif negative:
            requested -= negative
        elif len(requested) > 1 and (choice := re.fullmatch(
                r'\s*(CSV|JSON)\s*(?:파일)?\s*(?:으로|로)?\s*[.!]?\s*', message, re.I)):
            requested = {choice[1].lower()}
        elif re.search(r'(?:파일|결과|보고서)?\s*저장(?:은|을|하지)\s*(?:취소|하지\s*마|마|말)', message):
            requested = set()
    return requested
