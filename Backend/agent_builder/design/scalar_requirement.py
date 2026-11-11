"""Ground literal scalar arithmetic; do not infer table or variable semantics.

This deliberately supports written arithmetic and explicit ``name=number``
assignments only. Broader prose/variable bindings need clarification; the
registered scalar operation and its execution validator remain unchanged.
"""
import ast
import re
from decimal import Decimal

from workflow_engine.table_formula import formula_columns


NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
TOKEN = re.compile(NUMBER + r'|[A-Za-z_][A-Za-z_0-9]*|[()+*/%\-]')
TABLE_SCOPE = re.compile(
    r'\b(?:table|column|row|dataframe)s?\b|'
    r'(?<![가-힣A-Za-z_])(?:표|행|열|컬럼)(?=$|[\s은는이가을를의에별마다])|'
    r'(?:출력|계산|결과|새로운|새)\s*(?:열|컬럼)', re.I)
UNCERTAIN_CONTEXT = re.compile(
    r'말고|아니라|대신|취소|제외|미정|모르|하지\s*마|하지\s*않|계산하지|'
    r'\b(?:not|instead|cancel|ignore|unknown|unspecified)\b', re.I)


def _written_expression(formula, text):
    tokens = TOKEN.findall(formula)
    if ''.join(tokens) != re.sub(r'\s+', '', formula):
        return False
    body = ast.parse(formula, mode='eval').body
    while isinstance(body, ast.UnaryOp):body = body.operand
    atomic = isinstance(body, (ast.Constant, ast.Name))
    pattern = r'(?<![A-Za-z_0-9.])' + r'\s*'.join(map(re.escape, tokens)) + r'(?![A-Za-z_0-9.])'
    for match in re.finditer(pattern, text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        # A valid substring of a larger expression is not the whole request.
        if before[-1:] and before[-1] in '+-*/%()=':continue
        if after[:1] and after[0] in '+-*/%()=':continue
        # A number/name in an assignment or explanation is not itself a
        # requested calculation. Keep explicit single-constant requests.
        if atomic and not (
            re.match(r'^(?:을|를)?\s*(?:계산|평가|구해)', after)
            or re.match(r'^의\s*(?:값|결과)(?:을|를)?\s*(?:계산|보여|알려)', after)
            or re.search(r'\b(?:calculate|evaluate)\s*$', before, re.I)
        ):continue
        return True
    return False


def validate_scalar_requirement(spec, quote, user_messages):
    formula = spec['formula']
    names = formula_columns(formula)
    if TABLE_SCOPE.search(quote):
        raise ValueError('표의 행·열 계산을 독립 스칼라 계산으로 바꿀 수 없습니다.')
    if UNCERTAIN_CONTEXT.search(quote):
        raise ValueError('변경·부정된 계산식을 추측하지 않고 명확한 식을 확인해야 합니다.')
    if not _written_expression(formula, quote):
        raise ValueError('스칼라 계산식 전체를 사용자 원문에서 확인할 수 없습니다.')
    variables = spec['variables']
    if set(variables) != names or any(type(v) not in (int, float) for v in variables.values()):
        raise ValueError('스칼라 수식의 변수와 명시된 숫자 값이 일치하지 않습니다.')
    for name, value in variables.items():
        assignments = []
        pattern = r'(?<![\w.])' + re.escape(name) + r'\s*=\s*([+-]?' + NUMBER + r')(?![A-Za-z_0-9.])'
        for message in user_messages:
            matches = list(re.finditer(pattern, message))
            mentions = re.search(r'(?<![A-Za-z_0-9])' + re.escape(name) + r'(?![A-Za-z_0-9])', message)
            if mentions and (TABLE_SCOPE.search(message) or UNCERTAIN_CONTEXT.search(message)):
                assignments = []
                continue
            for match in matches:
                tail = message[match.end():].lstrip()
                # x=2+3 is not an explicit assignment of the number 2.
                if tail[:1] and tail[0] in '+-*/%()':
                    assignments = []
                else:
                    assignments.append(match.group(1))
        if not assignments or Decimal(assignments[-1]) != Decimal(str(value)):
            raise ValueError('스칼라 변수의 숫자 값을 사용자 원문에서 확인할 수 없습니다: ' + name)
    from workflow_engine.unified_tools.expansion.runtime import expression
    try:
        expression(formula, variables)
    except (ArithmeticError, ValueError, TypeError) as error:
        raise ValueError('스칼라 계산의 숫자 조건을 확인하세요.') from error
