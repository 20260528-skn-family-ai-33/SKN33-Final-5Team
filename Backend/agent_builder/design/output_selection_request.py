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

_EXPORT_END = re.compile(r'\s*(?:해\s*(?:줘|주세요)|하(?:고|며|도록)|[.!;,\n]|$)')
_ALL_COLUMNS = re.compile(r'(?:전체|모든)\s*열(?:들)?(?:을|로)?\s*(?:저장|내보내)')


def _selection_request(state):
    assets = state.get('resources', [])
    if len(assets) != 1 or assets[0].get('kind') != 'table':
        return -1, ''
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    # Keep an explicit output contract through unrelated follow-up answers.
    # A later output revision (even outside this narrow grammar) supersedes it.
    from agent_builder.capabilities.capability_judgement import active_request_messages
    active = active_request_messages(state)
    active_users = [m['content'] for m in active if m.get('role')=='user']
    offset = len(users)-len(active_users)
    for index in range(len(active_users)-1, -1, -1):
        text = re.sub(r'"[^"\n]*"|“[^”\n]*”|\x27[^\x27\n]*\x27', ' ', active_users[index])
        if (re.search(r'뭐야|뭔가요|무슨\s*뜻|설명해|무엇인가요', text)
                and not re.search(r'바꿔|변경해|추가해|제외해|선택해', text)):
            continue  # Asking what an output column means does not replace it.
        if (_NAMED_EXPORT.search(text) or _ALL_COLUMNS.search(text)
                or re.search(r'\w+(?:\s*[·,]\s*\w+)+(?:을|를)\s*(?:내보내|저장)', text)
                or re.search(r'(?:출력|저장할|내보낼)\s*(?:열|컬럼)|(?:열|컬럼).{0,20}(?:추가|제외|변경|바꿔)', text)):
            return offset+index, text
    return -1, ''


def _request_text(state):
    return _selection_request(state)[1]


def _all_columns_requested(state):
    text = _request_text(state)
    return any(_EXPORT_END.match(text[m.end():]) for m in _ALL_COLUMNS.finditer(text))


def output_selection(state):
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='table':
        return None
    # Only a new output instruction replaces the existing column selection.
    text = _request_text(state)
    if _all_columns_requested(state):
        return None
    matches = [m for m in _NAMED_EXPORT.finditer(text) if _EXPORT_END.match(text[m.end():])]
    if len(matches) != 1:
        return None
    fields = re.split(r'\s*·\s*', matches[0]['columns'])
    if fields[-1].endswith('만'):
        literal, without_particle = fields[-1], fields[-1][:-1]
        columns = assets[0].get('columns', [])
        if literal in columns and without_particle in columns:
            return None  # Both are real columns; leave disambiguation to the planner.
        if literal not in columns:
            fields[-1] = without_particle
    return fields


def validate_output_selection(plan, state):
    if plan['unresolved']:
        return
    specs = [task['spec'] for task in plan['tasks']]
    if _all_columns_requested(state):
        expected = set(state['resources'][0].get('columns', []))
        for spec in specs:
            if spec['kind'] == 'compute':
                expected.add(spec['output_column'])
            elif spec['kind'] == 'rename':
                expected.discard(spec['source'])
                expected.add(spec['target'])
            elif spec['kind'] == 'select_fields' and not expected <= set(spec['fields']):
                raise ValueError('전체 열 저장 요청에서 일부 출력 열을 제거할 수 없습니다.')
    fields=output_selection(state)
    if fields is None:
        return
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


def output_selection_issues(state, draft):
    """Check the actual output even if extraction omitted a selection task."""
    all_columns = _all_columns_requested(state)
    fields = output_selection(state)
    if (not all_columns and fields is None) or not draft.get('workflow'):
        return []
    from .content_lineage import content_ancestors
    from .requirement_coverage import _arguments
    from .workflow_paths import workflow_paths
    workflow = draft['workflow']
    nodes = {n['id']: n for n in workflow['nodes']}
    try:
        for path, _ in workflow_paths(workflow):
            writers = [n for n in path if n['tool'] in ('write_result','export_result')]
            for writer in writers:
                active = content_ancestors(nodes, writer['id']) | {writer['id']}
                expected = list(state['resources'][0].get('columns', []))
                actual = list(expected)
                for n in path:
                    if n['id'] not in active:
                        continue
                    settings = n['settings']
                    if n['tool']=='calculate_columns' and not settings.get('operation'):
                        if settings.get('table_operation')=='compute':
                            expected.append(settings['output_column']); actual.append(settings['output_column'])
                        elif settings.get('table_operation')=='rename':
                            expected=[settings['rename_to'] if c==settings['rename_from'] else c for c in expected]
                            actual=[settings['rename_to'] if c==settings['rename_from'] else c for c in actual]
                    if settings.get('operation')=='select_columns':
                        actual = _arguments(n)['columns']
                        if all_columns and not set(expected)<=set(actual):
                            return ['전체 열 저장 요청과 실제 출력 열 선택이 다릅니다: '+n['id']]
                    if n['id']==writer['id']:
                        columns = _arguments(n).get('columns') if settings.get('operation') else None
                        if columns is not None:
                            if set(columns)!=set(actual):
                                return ['전체 열/출력 열 검증: CSV 헤더와 입력 데이터 열이 다릅니다: '+n['id']]
                            actual = columns
                        if fields is not None and actual!=fields:
                            return ['명시한 최종 출력 열의 목록·순서와 실제 저장 헤더가 다릅니다: '+n['id']]
                        if all_columns and not set(expected)<=set(actual):
                            return ['전체 열 저장 요청과 실제 저장 헤더가 다릅니다: '+n['id']]
    except (ValueError, KeyError, TypeError):
        return ['출력 열 요청의 최종 저장 경로를 확인하지 못했습니다.']
    return []


def project_output_selection(candidates,state):
    fields=output_selection(state)
    if not fields or any(c['spec'].get('kind')=='select_fields' for c in candidates):return candidates
    result=deepcopy(candidates)
    index=next((i for i,item in enumerate(result) if item['spec'].get('kind')=='export'),len(result))
    result.insert(index,dict(source_message=_selection_request(state)[0],description='명시한 최종 출력 열',spec=dict(kind='select_fields',fields=fields)))
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
