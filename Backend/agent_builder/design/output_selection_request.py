"""Check explicit column lists immediately attached to a named file export.

Only a closed list syntax is recognized. Literal selections can be projected
from the user source; conflicting model candidates still fail validation.
"""
import re
from copy import deepcopy

from contracts.numeric_criteria import number_value


_NAMED_EXPORT = re.compile(
    r'(?<![\w·])(?P<columns>[\w]+(?:\s*·\s*[\w]+)+)'
    r'(?:만)?(?:을|를)\s+[\w.-]+\.(?:csv|json|xlsx)'
    r'(?:으로|로)\s*(?:저장|내보내)', re.I)


def output_selection(state):
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='table':
        return None
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    # Later turns may replace the entire output specification. Never restore
    # an older selection after an explicit new instruction.
    matches = list(_NAMED_EXPORT.finditer(users[-1])) if users else []
    if len(matches) != 1:
        return None
    return re.split(r'\s*·\s*', matches[0]['columns'])


def validate_output_selection(plan, state):
    if plan['unresolved']:
        return
    fields=output_selection(state)
    if fields is None:
        return
    specs = [task['spec'] for task in plan['tasks']]
    computed=literal_output_compute(state)
    if computed:
        from .requirement_coverage import _formula
        computations=[s for s in specs if s['kind']=='compute']
        if (len(computations)!=1 or computations[0]['output_column']!=computed['output_column']
                or _formula(computations[0]['formula'])!=_formula(computed['formula'])):
            raise ValueError('명시한 계산식의 결과를 요청한 출력 열로 연결해야 합니다: '+str(computed))
    if not any(s['kind'] == 'select_fields' and s['fields'] == fields for s in specs):
        raise ValueError('명시한 최종 출력 열 선택이 누락되거나 변경됐습니다: ' + ', '.join(fields))
    from .requirement_coverage import _condition
    actual=[_condition(f) for spec in specs if spec['kind']=='filter' for f in spec['filters']]
    if any(_condition(f) not in actual for f in literal_numeric_filters(state)):
        raise ValueError('명시한 출력 열의 숫자 선별 조건이 누락되거나 변경됐습니다.')


def project_output_selection(candidates,state):
    fields=output_selection(state)
    if not fields or any(c['spec'].get('kind')=='select_fields' for c in candidates):return candidates
    users=[m for m in state['messages'] if m.get('role')=='user']
    result=deepcopy(candidates)
    index=next((i for i,item in enumerate(result) if item['spec'].get('kind')=='export'),len(result))
    result.insert(index,dict(source_message=len(users)-1,description='명시한 최종 출력 열',spec=dict(kind='select_fields',fields=fields)))
    return result


def literal_numeric_filters(state):
    """A closed comparison clause over an explicitly selected output column."""
    fields=output_selection(state)
    if not fields:return []
    text=[m['content'] for m in state['messages'] if m['role']=='user'][-1]
    found=[]
    for part in re.split(r',\s+|[\n;]',text):
        match=re.fullmatch(r'\s*('+'|'.join(map(re.escape,fields))+r')(?:이|가|은|는)?\s+'
                          r'(\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)\s*',part)
        if match:
            found.append(dict(field=match[1],operator={'이상':'gte','이하':'lte','초과':'gt','미만':'lt'}[match[3]],
                              value=float(number_value(match[2])),compare_field=None,value_type='number'))
    return found


def literal_output_compute(state):
    """One source expression and one newly requested numeric output, no named intermediate.

    A prose business label ("매출은 ...") is not an explicit intermediate column
    declaration. An assignment/name declaration, multiple formulas, or multiple
    missing output columns makes the correspondence ambiguous and stays general.
    """
    fields=output_selection(state)
    if not fields:return None
    columns=state['resources'][0].get('columns',[])
    missing=[f for f in fields if f not in columns]
    if len(missing)!=1 or not any(f['field']==missing[0] for f in literal_numeric_filters(state)):return None
    from .compute_grounding import _formula_literals, _named_column, _expression
    users=[m['content'] for m in state['messages'] if m['role']=='user']
    if any(name!=missing[0] for text in users for name in _named_column(text)):return None
    if any(re.search(r'집계|합계|합산|결합|평균|조인|\bjoin\b',text,re.I) for text in users):return None
    formulas={_expression(formula):formula for text in users for formula in _formula_literals(text,columns)}
    if len(formulas)!=1:return None
    return dict(kind='compute',formula=next(iter(formulas.values())),output_column=missing[0])


def constrain_literal_selection(schema,state):
    """Constrain the proven projection and describe independently parsed criteria."""
    fields=output_selection(state)
    if not fields:return schema
    from .requirement_generation import _literal
    result=deepcopy(schema)
    result['$defs']['SelectFields']['properties']['fields']=_literal(fields)
    computed=literal_output_compute(state)
    if computed:
        for key in ('formula','output_column'):
            result['$defs']['Compute']['properties'][key]=_literal(computed[key])
    literals=literal_numeric_filters(state)
    if not literals:return result
    result['$defs']['FilterCondition']['description']='명시한 숫자 조건: '+str(literals)
    return result
