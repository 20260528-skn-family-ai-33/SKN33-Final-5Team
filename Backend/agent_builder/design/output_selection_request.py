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
_ALL_COLUMNS = re.compile(r'(?:전체|모든)\s*열(?:들)?(?:만)?(?:을|로)?\s*(?:저장|내보내)')
_SINGLE_EXPORT = re.compile(
    r'(?<![\w·])(?P<column>\w+?)(?:\s+(?P<marker>열|컬럼))?만(?:을|를)?\s*'
    r'(?:[\w.-]+\.(?:csv|json|xlsx)(?:으로|로)\s*)?(?:저장|내보내)', re.I)
_LIST_SEPARATOR = r'\s*(?:[·,]|와|과|및|하고)\s*'
_LIST_EXPORT = re.compile(
    r'(?<![\w·])(?P<columns>\w+(?:' + _LIST_SEPARATOR + r'\w+)+)'
    r'\s+(?:열|컬럼)만(?:을|를)?\s*'
    r'(?:[\w.-]+\.(?:csv|json|xlsx)(?:으로|로)\s*)?(?:저장|내보내)', re.I)


def _request_turns(state):
    """Scoped source turns, excluding quoted examples and explanation questions."""
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    from agent_builder.capabilities.capability_judgement import active_request_messages
    active = active_request_messages(state)
    active_users = [m['content'] for m in active if m.get('role')=='user']
    offset = len(users)-len(active_users)
    for index, content in enumerate(active_users):
        text = re.sub(r'"[^"\n]*"|“[^”\n]*”|\x27[^\x27\n]*\x27', ' ', content)
        if (re.search(r'뭐야|뭔가요|무슨\s*뜻|설명해|무엇인가요', text)
                and not re.search(r'바꿔|변경해|추가해|제외해|선택해', text)):
            continue
        yield offset+index, text


def _single_requests(text, columns):
    # These nouns describe the delivery, unless they really name a column or
    # the user explicitly says "열/컬럼". Do not turn "파일만 저장" into a schema.
    generic = {'파일', '결과', '결과물', '산출물', '표'}
    return [m for m in _SINGLE_EXPORT.finditer(text)
            if m['marker'] or m['column'] in columns or m['column'] not in generic]


def _known_columns(state):
    columns = set(state.get('resources', [{}])[0].get('columns', []))
    for _, text in _request_turns(state):
        columns.update(_selection_fields(text, columns) or [])
    return columns


def _selection_request(state):
    assets = state.get('resources', [])
    if len(assets) != 1 or assets[0].get('kind') != 'table':
        return -1, ''
    # A new output instruction replaces the old one even if its fields cannot
    # be projected by this narrow grammar. Unrelated replies do not replace it.
    columns = _known_columns(state)
    for index, text in reversed(list(_request_turns(state))):
        if (_NAMED_EXPORT.search(text) or _ALL_COLUMNS.search(text) or _single_requests(text, columns)
                or re.search(r'\w+(?:\s*[·,]\s*\w+)+(?:을|를)\s*(?:내보내|저장)', text)
                or re.search(r'(?:출력|저장할|내보낼)\s*(?:열|컬럼)|(?:열|컬럼).{0,20}(?:추가|제외|변경|바꿔)', text)):
            return index, text
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
    return _selection_fields(text, _known_columns(state))


def _selection_fields(text, columns):
    matches = [m for m in _NAMED_EXPORT.finditer(text) if _EXPORT_END.match(text[m.end():])]
    lists = [m for m in _LIST_EXPORT.finditer(text) if _EXPORT_END.match(text[m.end():])]
    singles = [m for m in _single_requests(text, columns) if _EXPORT_END.match(text[m.end():])]
    if lists:
        # Consume the entire list before considering its single-column suffix.
        # Unknown/ambiguous prefixes must go to the planner, never disappear.
        if len(lists) != 1 or matches or not columns:
            return None
        if any(not lists[0].start() <= m.start() < lists[0].end() for m in singles):
            return None
        names = '|'.join(re.escape(c) for c in sorted(columns, key=len, reverse=True))
        source = lists[0]['columns']
        complete_list = re.fullmatch(r'(?:' + names + r')(?:' + _LIST_SEPARATOR + r'(?:' + names + r'))+', source)
        if source in columns:
            # A conjunction inside a real name (e.g. 연구과제) is not a list.
            # If both interpretations exist, leave the ambiguity to the planner.
            return None if complete_list else [source]
        if not complete_list:
            return None
        fields = []
        while source:
            match = re.match(r'(?:' + names + r')(?=' + _LIST_SEPARATOR + r'|$)', source)
            if not match:
                return None
            fields.append(match[0])
            source = re.sub(r'^' + _LIST_SEPARATOR, '', source[match.end():], count=1)
        return fields if len(fields) == len(set(fields)) else None
    if singles:
        if matches or len(singles)!=1:
            return None
        single = singles[0]
        return [single['column']] if single['marker'] or single['column'] in columns else None
    if len(matches) != 1:
        return None
    fields = re.split(r'\s*·\s*', matches[0]['columns'])
    if fields[-1].endswith('만'):
        literal, without_particle = fields[-1], fields[-1][:-1]
        if literal in columns and without_particle in columns:
            return None  # Both are real columns; leave disambiguation to the planner.
        if literal not in columns:
            fields[-1] = without_particle
    return fields


def validate_output_selection(plan, state):
    if plan['unresolved']:
        return
    specs = [task['spec'] for task in plan['tasks']]
    cancelled = cancelled_numeric_fields(state)
    if any(rule['field'] in cancelled for spec in specs if spec['kind'] == 'filter'
           for rule in spec['filters']):
        raise ValueError('취소한 열의 선별 조건이 요청 업무 목록에 남아 있습니다.')
    # Output revisions do not cancel row criteria. Check these even when a
    # later turn switches to all columns or a different output column list.
    from .numeric_filter_proof import guarantees_filter, contains_alternative
    if any(not any(guarantees_filter(spec, rule) for spec in specs if spec['kind']=='filter')
           for rule in literal_numeric_filters(state)):
        raise ValueError('명시한 출력 열의 숫자 선별 조건이 누락되거나 변경됐습니다.')
    if any(not any(contains_alternative(spec, rule) for spec in specs if spec['kind']=='filter')
           for rule in literal_or_filters(state)):
        raise ValueError('명시한 OR 숫자 조건의 기준 또는 결합 방식이 누락되거나 변경됐습니다.')
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
    computed=literal_output_compute(state)
    if computed:
        from .requirement_coverage import _formula
        computations=[s for s in specs if s['kind']=='compute']
        if (len(computations)!=1 or computations[0]['output_column']!=computed['output_column']
                or _formula(computations[0]['formula'])!=_formula(computed['formula'])):
            raise ValueError('명시한 계산식의 결과를 요청한 출력 열로 연결해야 합니다: '+str(computed))
    if fields is None:
        return
    if not any(s['kind'] == 'select_fields' and s['fields'] == fields for s in specs):
        raise ValueError('명시한 최종 출력 열 선택이 누락되거나 변경됐습니다: ' + ', '.join(fields))


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
    return _literal_numeric_contracts(state)[0]


def literal_or_filters(state):
    return _literal_numeric_contracts(state)[1]


def cancelled_numeric_fields(state):
    return _literal_numeric_contracts(state)[2]


def _literal_numeric_contracts(state):
    """Retain literal criteria until that field is explicitly revised/cancelled.

    Historical output columns remain eligible after an output-only revision.
    Clauses are complete assertions, never numbers in examples or questions.
    """
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='table':return [], [], set()
    from .explicit_table_criteria import explicit_criteria, expected_specs
    explicit = explicit_criteria(state)
    if explicit is not None and 'selection' in explicit:
        filters = [s for s in expected_specs(explicit) if s['kind']=='filter']
        # An explicit replacement contract owns both the operands and their
        # connective. Its separate validator checks the complete OR expression.
        return ([f for s in filters if s['match']=='all' or len(s['filters'])==1
                 for f in s['filters'] if f['value_type']=='number'], [], set())
    turns=list(_request_turns(state))
    fields=set()
    columns = _known_columns(state)
    for _,text in turns:
        fields.update(_selection_fields(text,columns) or [])
    if not fields:return [], [], set()
    names='|'.join(re.escape(f) for f in sorted(fields,key=len,reverse=True))
    criteria={}; alternatives={}; cancelled=set()
    for _,text in turns:
        for part in re.split(r',\s+|[\n;]',text):
            replacement = re.match(r'\s*기존\s*(?:조건|기준|필터)(?:을|를)?\s*대신\s+', part)
            if replacement:
                part = part[replacement.end():]
            match=re.fullmatch(r'\s*('+names+r')(?:이|가|은|는)?\s+'
                              r'(\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)\s*[.!]?\s*',part)
            if match:
                rule=dict(field=match[1],
                    operator={'이상':'gte','이하':'lte','초과':'gt','미만':'lt'}[match[3]],
                    value=float(number_value(match[2])),compare_field=None,value_type='number')
                # Updating an upper bound must not replace the lower bound (or
                # vice versa). Explicit whole-condition replacement is distinct.
                side = {'gte', 'gt'} if rule['operator'] in {'gte', 'gt'} else {'lte', 'lt'}
                previous = [] if replacement else criteria.get(match[1], [])
                criteria[match[1]] = [r for r in previous if r['operator'] not in side] + [rule]
                alternatives.pop(match[1], None)
                cancelled.discard(match[1])
            cancel=re.fullmatch(r'\s*('+names+r')\s*(?:숫자\s*)?(?:조건|기준|필터)(?:은|는|을|를)?\s*'
                                r'(?:취소|삭제|제거|해제)(?:해\s*(?:줘|주세요))?\s*[.!]?\s*',part)
            if cancel:
                criteria[cancel[1]]=[]
                alternatives.pop(cancel[1],None)
                cancelled.add(cancel[1])
            alternative=re.fullmatch(r'\s*[^,;\n]{1,80}?(?:이거나|또는|혹은)\s+'
                r'('+names+r')(?:이|가|은|는)?\s+(\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)(?:인)?\s*'
                r'(?:주문|행|건)(?:을|만)?\s*(?:대상으로|남겨\s*(?:줘|주세요))\s*[.!]?\s*',part)
            if alternative:
                criteria[alternative[1]]=[]
                alternatives[alternative[1]]=dict(field=alternative[1],
                    operator={'이상':'gte','이하':'lte','초과':'gt','미만':'lt'}[alternative[3]],
                    value=float(number_value(alternative[2])),compare_field=None,value_type='number')
                cancelled.discard(alternative[1])
    return [rule for rules in criteria.values() for rule in rules], list(alternatives.values()), cancelled


def literal_output_compute(state):
    """One source expression and one newly requested numeric output, no named intermediate.

    A prose business label ("매출은 ...") is not an explicit intermediate column
    declaration. An assignment/name declaration, multiple formulas, or multiple
    missing output columns makes the correspondence ambiguous and stays general.
    """
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='table':return None
    columns=assets[0].get('columns',[])
    numeric_fields={f['field'] for f in [*literal_numeric_filters(state), *literal_or_filters(state)]}
    candidates=set()
    for _,text in _request_turns(state):
        missing=[f for f in (_selection_fields(text,columns) or []) if f not in columns]
        if len(missing)==1 and missing[0] in numeric_fields:candidates.add(missing[0])
    # Hiding a computed column in the final file does not remove the earlier
    # calculation still needed by an active numeric filter.
    if len(candidates)!=1:return None
    output=next(iter(candidates))
    from .compute_grounding import _formula_literals, _named_column, _expression
    users=[text for _,text in _request_turns(state)]
    if any(name!=output for text in users for name in _named_column(text)):return None
    if any(re.search(r'집계|합계|합산|결합|평균|조인|\bjoin\b',text,re.I) for text in users):return None
    formulas={_expression(formula):formula for text in users for formula in _formula_literals(text,columns)}
    if len(formulas)!=1:return None
    return dict(kind='compute',formula=next(iter(formulas.values())),output_column=output)


def constrain_literal_selection(schema,state):
    """Constrain the proven projection and describe independently parsed criteria."""
    fields=output_selection(state)
    from .requirement_generation import _literal
    result=deepcopy(schema)
    if fields:result['$defs']['SelectFields']['properties']['fields']=_literal(fields)
    computed=literal_output_compute(state)
    if computed:
        for key in ('formula','output_column'):
            result['$defs']['Compute']['properties'][key]=_literal(computed[key])
    literals=literal_numeric_filters(state)
    if not literals:return result
    result['$defs']['FilterCondition']['description']='명시한 숫자 조건: '+str(literals)
    return result
