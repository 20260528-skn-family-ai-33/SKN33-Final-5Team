"""Evaluate explicit constant arithmetic using the existing v4 implementation."""
import json
from ..table_formula import formula_columns

def execute(node,state,storage,config,llm):
    from integrations.unified_tools.expansion.runtime import math_calculate
    formula=node['settings'].get('calculation_formula')
    if formula_columns(formula):raise ValueError('단일 계산에 변수는 지원하지 않습니다.')
    value=math_calculate(formula,{})
    return {'text':json.dumps(value,ensure_ascii=False,allow_nan=False)}
