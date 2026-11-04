"""Recognize directly printed receipt headers without estimating missing OCR values."""
from datetime import date
import re


_ALIASES = {
    '날짜': 'date', '거래일': 'date', '거래일자': 'date', '결제일': 'date', '결제일자': 'date',
    '상호': 'merchant', '상호명': 'merchant', '가맹점명': 'merchant', '매장명': 'merchant',
    '결제금액': 'paid', '총결제금액': 'paid', '최종결제금액': 'paid', '실결제금액': 'paid',
}
_DATE = re.compile(r'(?<!\d)(?P<y>(?:19|20)\d{2})\s*[-./년]\s*(?P<m>\d{1,2})\s*[-./월]\s*(?P<d>\d{1,2})(?!\d)(?:\s*일)?')
_TRANSACTION = re.compile(r'날짜|거래일|구매일|판매일|결제일|영수일|승인일|매출일|일시|transactiondate|date:', re.I)
_OTHER_DATE = re.compile(r'유효|기한|만료|교환|반품기간|쿠폰|적립기간|expir|\bexp\.?\s*date|valid\s+until|coupon', re.I)
_TIME = re.compile(r'\b(?:[01]?\d|2[0-3])[ \t]*:[ \t]*[0-5]\d(?:\b|:)')
_MONEY = (r'(?P<value>(?:(?:USD|EUR|GBP|KRW|JPY)[ \t]*)?(?:[₩￦$€£¥][ \t]*)?-?'
          r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?:[ \t]*(?:원|USD|EUR|GBP|KRW|JPY))?)')
_METHOD = r'(?:[ \t]*\((?:신용카드|체크카드|카드|현금|일시불|visa|mastercard|credit|debit|cash)\))?'


def _labeled(labels, value, boundary=''):
    # Korean receipts frequently space out headers such as "합  계".
    pattern = '|'.join(r'[ \t]*'.join(map(re.escape, label)) for label in labels)
    return re.compile(r'^[ \t]*(?:' + pattern + ')' + boundary + r'[ \t]*[:：]?[ \t]*' + value + r'[ \t]*$', re.I)


_PAID = _labeled(('최종결제금액', '실결제금액', '총결제금액', '결제금액', '결제합계', '결제총액', '받을금액', 'amount paid'), _MONEY + _METHOD)
_TOTAL = _labeled(('합계', '총합계', '합계금액', 'total', 'grand total'), _MONEY + _METHOD)
_MERCHANT = _labeled(('상호명', '상호', '가맹점명', '매장명', 'merchant'), r'(?P<value>[^:：\r\n]{1,80})', r'(?=[ \t:：])')
_AMOUNT_ONLY = re.compile(_MONEY)
_PAID_PREFIX = re.compile(
    r'(?:최종결제금액|실결제금액|총결제금액|결제금액|결제총액|받을금액|총합계|합계(?:금액)?|'
    r'(?<![a-z])(?:grand\s+total|total(?:\s+amount)?|amount\s+(?:paid|due)|balance\s+due))'
    r'[\s:：=|.-]*(?:(?:USD|EUR|GBP|KRW|JPY|[$€£₩￦¥])\s*)*$', re.I)
_COMPONENT = re.compile(r'부가세|과세|공급가|소계|할인|적립|포인트|상품|품목|거스름|잔돈|\b(?:vat|tax|sub[\s-]*total|discount|savings|change|item)\b', re.I)
_CURRENCY_VALUE = re.compile(r'(?:(?:[A-Z]{3}|[$€£₩￦¥])\s*)?-?\d[\d.,\' \u00a0]*(?:\s*(?:원|[A-Z]{3}|[$€£₩￦¥]))?', re.I)
_DATE_PREFIX = re.compile(r'(?:날짜|거래일자?|결제일자?|구매일자?|일시|date(?:\s+of\s+(?:purchase|sale))?)[\s:：=|-]*$', re.I)
_MONTH = re.compile(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b', re.I)


def _date_candidate(line):
    compact = re.sub(r'\s+', '', line)
    if _OTHER_DATE.search(compact):
        return None
    matches = list(_DATE.finditer(line))
    if len(matches) != 1:
        return None
    match = matches[0]
    try:
        key = date(*(int(match[k]) for k in ('y', 'm', 'd')))
    except ValueError:
        return None
    if _TRANSACTION.search(compact):
        rank = 2
    elif _TIME.search(line) or match.group().strip() == line.strip():
        rank = 1
    else:
        return None
    return rank, key, match.group().strip()


def recognized_facts(fields, parts):
    """A missing/ambiguous total must not be replaced by a grounded VAT or subtotal."""
    wanted = {field: _ALIASES.get(''.join(field.split())) for field in fields}
    candidates = {kind: [] for kind in wanted.values() if kind}
    for part in parts:
        previous = ''
        for raw_line in part['text'].splitlines(keepends=True):
            line = raw_line.strip()
            hits = {}
            if 'date' in candidates:
                hits['date'] = _date_candidate(line)
            if 'paid' in candidates:
                # Without OCR geometry a lone label and its amount can occupy consecutive
                # lines. Join only within the same source, with nothing between them.
                split_amount = previous and _AMOUNT_ONLY.fullmatch(line)
                printed = previous.strip() + ' ' + line if split_amount else line
                paid = _PAID.fullmatch(printed)
                total = paid or _TOTAL.fullmatch(printed)
                if total:
                    value = total['value'].strip()
                    quote = (previous + raw_line).strip() if split_amount else line
                    candidates['paid'].append((2 if paid else 1, re.sub(r'[,\s₩￦원]', '', value),
                                               dict(source_id=part['source_id'], quote=quote, value=value)))
            if 'merchant' in candidates:
                shop = _MERCHANT.fullmatch(line)
                if shop and shop['value'].strip():
                    value = shop['value'].strip()
                    hits['merchant'] = (1, value, value)
            for kind, hit in hits.items():
                if hit:
                    rank, key, value = hit
                    candidates[kind].append((rank, key, dict(source_id=part['source_id'], quote=line, value=value)))
            previous = raw_line
    result = {}
    for field, kind in wanted.items():
        if not kind:
            continue
        hits = candidates[kind]
        if hits:
            rank = max(hit[0] for hit in hits)
            best = {key: fact for priority, key, fact in hits if priority == rank}
            result[field] = next(iter(best.values())) if len(best) == 1 else None
    return result


def model_fact(field, fact, sources):
    """In other layouts still accept a cited value when its printed label supports it."""
    kind = _ALIASES.get(''.join(field.split()))
    if not fact or kind not in ('date', 'paid'):
        return fact
    source = sources.get(fact.get('source_id'))
    value, quote = fact.get('value'), fact.get('quote')
    if not source or not value or not quote or value not in quote:
        return None
    if kind == 'paid' and not _CURRENCY_VALUE.fullmatch(value):
        return None
    if kind == 'date':
        iso = _DATE.fullmatch(value)
        if iso and not _date_candidate(value):
            return None
        if not (iso or len(re.findall(r'\d+', value)) >= 3 or (_MONTH.search(value) and len(re.findall(r'\d+', value)) >= 2)):
            return None
    text = source['text']
    for citation in re.finditer(re.escape(quote), text):
        start = citation.start() + quote.index(value)
        end = start + len(value)
        if (re.search(r'\d[.,]?$', text[max(0, start-2):start]) or re.match(r'[.,]?\d', text[end:end+2])):
            continue
        line_start = text.rfind('\n', 0, start) + 1
        line_end = text.find('\n', end)
        line = text[line_start:line_end if line_end >= 0 else len(text)].strip()
        before = re.sub(r'(?<=[가-힣])[ \t]+(?=[가-힣])', '', text[line_start:start])
        if kind == 'paid':
            if _PAID_PREFIX.search(before) and not _COMPONENT.search(before):
                return {**fact, 'quote': line}
        elif _DATE_PREFIX.search(before) and not _OTHER_DATE.search(line):
            return {**fact, 'quote': line}
    return None
