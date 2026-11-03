"""Receipt arithmetic check for rows extraction, computed in code without a model call.

OCR misreads digits consistently (0 -> 8 turned 3,300 into 3,380 and 7,500 into 7,580 on the same
receipt), so item amounts can agree with a misread subtotal. Checking both relations catches it:
items == subtotal, and subtotal + discounts == paid amount. Values are never corrected; the result is
only reported so a person can look at the receipt.
"""
import re

# Fields that hold an item's line amount; the first one present in the requested fields is used.
AMOUNT_FIELDS = ('금액', '판매금액', 'amount')
CHECK_FIELD = '합계_검산'
# Korean may touch a number ("7,080원", "1,182점"); ASCII letters, *, / and : mark card numbers, dates and times.
_NUMBER = re.compile(r'(?<![0-9A-Za-z*])-?\d{1,3}(?:[,.]\d{3})+(?![0-9A-Za-z*])|(?<![0-9A-Za-z*.,/:-])-?\d+(?![0-9A-Za-z*.,/:])')
# Labels as OCR returns them; the item-count line was read as "한계수량/금액" on the test receipt.
_SUBTOTAL = re.compile(r'수량/금액|계수량|소계|판매총액|총액')
_DISCOUNT = re.compile(r'할인|에누리')
_PAID = re.compile(r'사용금액|결제금액|받을금액|합계|신용카드|현금')


def amount(text):
    """Integer won value when the whole text is one receipt number ("7,080원", "1.350", "-420"), or None."""
    if text is None:
        return None
    value = re.sub(r'[원점]$', '', str(text).replace(' ', ''))
    if not re.fullmatch(r'-?\d{1,3}(?:[,.]\d{3})+|-?\d+', value):
        return None
    return int(value.replace(',', '').replace('.', '')) if re.search(r'[,.]\d{3}$', value) else int(value)


def _last_number(line):
    numbers = _NUMBER.findall(line)
    return amount(numbers[-1]) if numbers else None


def totals(text):
    """Subtotal, discounts and paid-amount candidates found on labelled lines."""
    subtotal, discount, paid = None, 0, []
    for line in (text or '').splitlines():
        label = re.sub(r'\s+', '', line)
        if '번호' in label or re.search(r'받은(?:금액|돈)|거스름|잔돈', label):  # not the purchase total
            continue
        value = _last_number(line)
        if value is None:
            continue
        if _SUBTOTAL.search(label):
            subtotal = value if subtotal is None else subtotal
        elif _DISCOUNT.search(label) and value < 0:
            discount += value
        elif _PAID.search(label) and value > 0:
            paid.append(value)
    return subtotal, discount, paid


def _line_amounts(text):
    """Last number of each line that has at most one number (total lines, not item rows)."""
    found = set()
    for line in (text or '').splitlines():
        numbers = _NUMBER.findall(line)
        if len(numbers) == 1 and '번호' not in line:
            found.add(amount(numbers[0]))
    return found


def won(value):
    return f'{value:,}'


def check(records, fields, text):
    """Return the check message for one file's rows, or None when it cannot apply."""
    field = next((f for f in AMOUNT_FIELDS if f in fields), None)
    if not field or not records:
        return None
    subtotal, discount, paid = totals(text)
    values = [amount(r.get(field)) for r in records]
    if subtotal is None and not paid:
        # Large-font labels are often misread ("합 계" -> "게"); an exact item sum on its own line
        # is only a review hint: an item line or unrelated figure can also equal the sum.
        if all(v is not None for v in values) and sum(values) in _line_amounts(text):
            return f'확인 필요 (합계 라벨 미인식, {won(sum(values))} 줄 확인)'
        return None
    if any(v is None for v in values):
        return f'확인 불가 ({field} 없는 행 {sum(v is None for v in values)}개)'
    items = sum(values)
    problems = []
    if subtotal is not None and items != subtotal:
        problems.append(f'상품 {field} 합 {won(items)} ≠ 영수증 합계 {won(subtotal)}')
    expected = (subtotal if subtotal is not None else items) + discount
    # Conflicting labelled amounts require review even when one matches the arithmetic.
    if paid and any(value != expected for value in paid):
        base = '영수증 합계' if subtotal is not None else f'상품 {field} 합'
        problems.append(f'{base} {won(expected - discount)} + 할인 {won(discount)} = {won(expected)} ≠ 결제 금액 {"/".join(won(p) for p in dict.fromkeys(paid))}')
    return '불일치: ' + '; '.join(problems) if problems else '일치'
