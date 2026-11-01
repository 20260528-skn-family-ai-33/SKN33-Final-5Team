"""Validate the restricted formula supported by the reused v4 tool executor."""
import ast
import math

_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_UNARY = (ast.UAdd, ast.USub)

def formula_columns(formula):
    if not isinstance(formula, str) or not formula.strip() or len(formula) > 1000:
        raise ValueError('계산할 수식을 입력하세요.')
    try:
        tree = ast.parse(formula, mode='eval')
    except (SyntaxError, ValueError) as error:
        raise ValueError('수식 형식이 올바르지 않습니다.') from error
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError('수식이 너무 복잡합니다.')
    columns = set()
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ValueError('유한한 숫자만 사용할 수 있습니다.')
        elif isinstance(node, ast.Name):
            columns.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
            visit(node.left); visit(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY):
            visit(node.operand)
        else:
            raise ValueError('숫자, 열 이름, 사칙연산과 %만 사용할 수 있습니다.')
    visit(tree.body)
    return columns
