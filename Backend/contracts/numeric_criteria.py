"""Numeric filter literals shared by request validation and execution."""
import re
from decimal import Decimal, InvalidOperation


_DECIMAL = re.compile(r'[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?')


def number_value(value):
    """Accept grouped decimals such as 1,000.25, never malformed forms such as 1,2."""
    text = str(value).strip()
    if not _DECIMAL.fullmatch(text):
        raise ValueError('숫자 표기를 확인하세요: ' + text[:80])
    try:
        number = Decimal(text.replace(',', ''))
        if not number.is_finite():
            raise ValueError('유한한 숫자가 필요합니다.')
        return number
    except InvalidOperation as error:
        raise ValueError('숫자 표기를 확인하세요: ' + text[:80]) from error
