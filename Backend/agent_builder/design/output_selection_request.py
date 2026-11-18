"""Check explicit column lists immediately attached to a named file export.

Only a closed list syntax is recognized. Literal selections can be projected
from the user source; conflicting model candidates still fail validation.
"""
import re
from copy import deepcopy
from decimal import Decimal

from agent_builder.conditions.condition_readiness import has_written_row_action, written_filter_bounds, written_range_action
from contracts.numeric_criteria import number_value
from .export_intent import explicit_export_intent, request_clauses
from .text_filter_parser import column_projection_requests


_NAMED_EXPORT = re.compile(
    r'(?<![\w·])(?P<columns>[\w]+(?:\s*·\s*[\w]+)+)'
    r'(?:만)?(?:을|를)\s+[\w.-]+\.(?P<format>csv|json|xlsx)'
    r'(?:으로|로)\s*(?:저장|내보내)', re.I)

_EXPORT_END = re.compile(r'\s*(?:해\s*(?:줘|주세요)|하(?:고|며|세요|도록)|[.!;,\n]|$)')
_ALL_COLUMNS = re.compile(r'(?:전체|모든)\s*열(?:들)?(?:만)?(?:을|로)?\s*(?:저장|내보내)')
_SINGLE_EXPORT = re.compile(
    r'(?<![\w·])(?P<column>\w+?)(?:\s+(?P<marker>열|컬럼))?만(?:을|를)?\s*'
    r'(?:[\w.-]+\.(?P<format>csv|json|xlsx)(?:으로|로)\s*)?(?:저장|내보내)', re.I)
_EXAMPLE = re.compile(r'\s*(?:예시(?:로|는)\s+|예를\s*들어\s+|문구는\s+)')
_DISPLAY = re.compile(r'(?:표시|출력|보여)$')
_DISPLAY_END = re.compile(r'\s*(?:해\s*(?:줘|주세요)|하세요|줘|주세요)[.!]?\s*')


def _request_turns(state):
    """Scoped source turns, excluding quoted examples and explanation questions."""
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    from agent_builder.capabilities.capability_judgement import active_request_messages
    active = active_request_messages(state)
    active_users = [m['content'] for m in active if m.get('role')=='user']
    offset = len(users)-len(active_users)
    for index, content in enumerate(active_users):
        text = re.sub(r'```[\s\S]*?```|`[^`\n]*`|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|\x27[^\x27\n]*\x27', ' ', content)
        # An explanation request does not cancel work in another sentence of
        # the same turn. Ignore only explanation-only clauses, not the turn.
        clauses = list(request_clauses(text))
        requested = []
        for _, clause in clauses:
            if _EXAMPLE.match(clause):
                continue
            explanation = re.search(r'뭐야|뭔가요|무슨\s*뜻|설명해|무엇인가요', clause)
            instruction = re.search(
                r'바꿔|변경해|추가해|제외해|선택해|'
                r'(?:계산|집계|합산|선별|필터링|정렬|결합|병합|저장|내보내|추출)\s*'
                r'(?:해\s*(?:줘|주세요)|하(?:고|며|세요|도록))(?=\s|[.!?,;]|$)', clause)
            if not explanation or instruction or has_written_row_action(clause):
                requested.append(clause)
        retained = text if len(requested) == len(clauses) else '\n'.join(requested)
        if retained.strip():
            yield offset+index, retained


def _single_requests(text, columns):
    # These nouns describe the delivery, unless they really name a column or
    # the user explicitly says "열/컬럼". Do not turn "파일만 저장" into a schema.
    generic = {'파일', '결과', '결과물', '산출물', '표'}
    return [m for m in _SINGLE_EXPORT.finditer(text)
            if m['marker'] or m['column'] in columns or m['column'] not in generic]


def _known_columns(state):
    from .compute_grounding import _named_column
    from .output_selection_revision import sealed_output_plan

    columns = set(state.get('resources', [{}])[0].get('columns', []))
    turns = list(_request_turns(state))
    # A follow-up can refer to a computed or renamed field by its existing
    # name. Only the sealed plan for this input and request can supply it.
    if turns and any(re.search(r'표시|보여|출력', text) for _, text in turns):
        messages = state.get('messages', [])
        last = max(i for i, message in enumerate(messages) if message.get('role') == 'user')
        plan = sealed_output_plan(state, messages, messages[:last])
        if plan:
            result_keys = {'compute': 'output_column', 'rename': 'target'}
            for task in plan['tasks']:
                spec = task['spec']
                if task['source_message'] >= turns[0][0] and spec['kind'] in result_keys:
                    columns.add(spec[result_keys[spec['kind']]])
    for _, text in turns:
        columns.update(_named_column(text))
        columns.update(_selection_fields(text, columns) or [])
    return columns


def _selection_request(state, columns, *, display_only=False):
    assets = state.get('resources', [])
    if len(assets) != 1 or assets[0].get('kind') != 'table':
        return -1, ''
    # A new output instruction replaces the old one even if its fields cannot
    # be projected by this narrow grammar. Unrelated replies do not replace it.
    for index, text in reversed(list(_request_turns(state))):
        if display_only:
            # A file's own header selection ends with that file. An independent
            # selection for displayed rows still applies after cancelling it.
            clauses = [clause for _, clause in request_clauses(text)
                       if any(p.affirmed and _DISPLAY.search(clause[p.start:p.end])
                              for p in column_projection_requests(clause, columns, explicit_display=True))]
            if clauses:
                return index, '\n'.join(clauses)
            continue
        if (any(item.affirmed for item in column_projection_requests(text, columns, explicit_display=True)) or _NAMED_EXPORT.search(text) or _ALL_COLUMNS.search(text) or _single_requests(text, columns)
                or re.search(r'\w+(?:\s*[·,]\s*\w+)+(?:을|를)\s*(?:내보내|저장)', text)
                or re.search(r'(?:출력|저장할|내보낼)\s*(?:열|컬럼)|(?:열|컬럼).{0,20}(?:추가|제외|변경|바꿔)', text)):
            return index, text
    return -1, ''


def output_requirements(state):
    """Active file and column requirements, consumed by planning and save validation."""
    intent = explicit_export_intent(state)
    assets = state.get('resources', [])
    columns = _known_columns(state) if len(assets) == 1 and assets[0].get('kind') == 'table' else set()
    source, text = _selection_request(state, columns, display_only=intent['cancelled'])
    all_columns = any(_EXPORT_END.match(text[m.end():]) for m in _ALL_COLUMNS.finditer(text))
    fields = None if all_columns else _selection_fields(text, columns)
    display_clauses = set()
    if fields:
        for _, clause in request_clauses(text):
            for projection in column_projection_requests(clause, columns, explicit_display=True):
                if (projection.affirmed and list(projection.fields) == fields
                        and _DISPLAY.search(clause[projection.start:projection.end])
                        and not clause[:projection.start].strip()
                        and _DISPLAY_END.fullmatch(clause[projection.end:])):
                    display_clauses.add((source, clause.strip()))
    return {**intent, 'fields': fields, 'all_columns': all_columns, 'field_source': source,
            'display_clauses': frozenset(display_clauses)}


def output_selection(state):
    return output_requirements(state)['fields']


def _selection_fields(text, columns):
    projections = column_projection_requests(text, columns, explicit_display=True)
    if projections:
        return list(projections[0].fields) if len(projections) == 1 and projections[0].affirmed else None
    matches = [m for m in _NAMED_EXPORT.finditer(text) if _EXPORT_END.match(text[m.end():])]
    singles = [m for m in _single_requests(text, columns) if _EXPORT_END.match(text[m.end():])]
    # A batch clarification reply can repeat the same answer after each question.
    # Identical instructions name one output, not several ambiguous projections.
    # Include the filename in the identity: separate outputs must stay separate.
    matches = list({m.group(): m for m in matches}.values())
    singles = list({m.group(): m for m in singles}.values())
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


def literal_output_format(state):
    """The format of one unambiguous named projection, never a source filename."""
    return _projectable_format(output_requirements(state))


def _projectable_format(intent):
    if intent['fields'] and intent['projects_writer'] and not intent['multiple'] and len(intent['formats']) == 1:
        return next(iter(intent['formats']))
    return None


def validate_output_selection(plan, state):
    if plan['unresolved']:
        return
    specs = [task['spec'] for task in plan['tasks']]
    intent = output_requirements(state)
    exports = {s['format'] for s in specs if s['kind']=='export'}
    if intent['cancelled'] and exports:
        raise ValueError('취소한 파일 저장 작업이 업무 목록에 남아 있습니다.')
    if exports & intent['forbidden_formats']:
        raise ValueError('취소한 파일 저장 형식이 업무 목록에 남아 있습니다.')
    if intent['additional'] and exports != intent['formats']:
        raise ValueError('같은 결과를 추가로 저장하라는 파일 형식이 누락되거나 변경됐습니다.')
    if intent['formats'] and not intent['multiple'] and exports != intent['formats']:
        raise ValueError('명시한 결과 파일 저장 작업이 누락되거나 형식이 변경됐습니다.')
    # Output revisions do not cancel row criteria. Check these even when a
    # later turn switches to all columns or a different output column list.
    from .numeric_filter_proof import guarantees_filter, contains_alternative
    if any(not any(guarantees_filter(spec, rule) for spec in specs if spec['kind']=='filter')
           for rule in literal_numeric_filters(state)):
        raise ValueError('명시한 출력 열의 숫자 선별 조건이 누락되거나 변경됐습니다.')
    if any(not any(contains_alternative(spec, rule) for spec in specs if spec['kind']=='filter')
           for rule in literal_or_filters(state)):
        raise ValueError('명시한 OR 숫자 조건의 기준 또는 결합 방식이 누락되거나 변경됐습니다.')
    if intent['all_columns']:
        expected = set(state['resources'][0].get('columns', []))
        for spec in specs:
            if spec['kind'] == 'compute':
                expected.add(spec['output_column'])
            elif spec['kind'] == 'rename':
                expected.discard(spec['source'])
                expected.add(spec['target'])
            elif spec['kind'] == 'select_fields' and not expected <= set(spec['fields']):
                raise ValueError('전체 열 저장 요청에서 일부 출력 열을 제거할 수 없습니다.')
    fields = intent['fields']
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
    if intent['formats'] and not intent['multiple'] and sum(s['kind'] == 'export' for s in specs) != 1:
        raise ValueError('명시한 결과 파일 저장 작업이 누락되거나 중복됐습니다.')


def export_intent_issues(state, draft, *, intent=None):
    """An extra writer must not bypass a correct cancellation/revision ledger."""
    from .requirement_coverage import _matches
    from workflow_engine.runner import execution_parameters

    if intent is None:
        intent = output_requirements(state)
    if not (intent['cancelled'] or intent['forbidden_formats'] or intent['formats']):
        return []
    single = bool(intent['formats']) and not intent['multiple']
    for node in (draft.get('workflow') or {}).get('nodes', []):
        if node['tool'] not in ('write_result', 'export_result', 'create_spreadsheet'):
            continue
        if intent['cancelled']:
            return ['취소한 파일 저장 작업이 실제 실행 단계에 남아 있습니다: ' + node['id']]
        try:
            # Match the format the executor will use, including a native
            # write_result node's fallback to the workflow output format.
            effective = {**node, 'settings': {**node['settings'], **execution_parameters(node, draft)}}
            formats = {fmt for fmt in ('csv', 'json', 'xlsx', 'pdf', 'md')
                       if _matches(dict(kind='export', format=fmt), effective, draft, lambda _: set())}
        except (ValueError, KeyError, TypeError):
            return ['요청한 파일 저장 형식의 실제 실행 설정을 확인하지 못했습니다: ' + node['id']]
        if formats & intent['forbidden_formats']:
            return ['취소한 파일 저장 형식이 실제 실행 단계에 남아 있습니다: ' + node['id']]
        if single and formats - intent['formats']:
            return ['명시한 결과 파일 저장 형식과 실제 실행 단계가 다릅니다: ' + node['id']]
    return []


def output_selection_issues(state, draft):
    """Check the actual output even if extraction omitted a selection task."""
    intent = output_requirements(state)
    export_issues = export_intent_issues(state, draft, intent=intent)
    if export_issues:
        return export_issues
    all_columns = intent['all_columns']
    fields = intent['fields']
    if (not all_columns and fields is None) or not draft.get('workflow'):
        return []
    from .content_lineage import content_ancestors
    from .requirement_coverage import _arguments
    from .workflow_paths import workflow_paths
    from workflow_engine.execution_path import result_rows_endpoint
    workflow = draft['workflow']
    nodes = {n['id']: n for n in workflow['nodes']}
    try:
        for path, _ in workflow_paths(workflow):
            endpoints = [(n, True) for n in path if n['tool'] in ('write_result','export_result')]
            if not endpoints:
                preview = result_rows_endpoint(path)
                if preview is None and intent['cancelled']:
                    return ['요청한 열을 표시하는 최종 결과가 없습니다.']
                if preview is not None:
                    endpoints.append((preview[0], False))
            for endpoint, writer in endpoints:
                active = content_ancestors(nodes, endpoint['id']) | {endpoint['id']}
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
                    if n['id']==endpoint['id']:
                        columns = _arguments(n).get('columns') if writer and settings.get('operation') else None
                        if columns is not None:
                            if set(columns)!=set(actual):
                                return ['전체 열/출력 열 검증: CSV 헤더와 입력 데이터 열이 다릅니다: '+n['id']]
                            actual = columns
                        if fields is not None and actual!=fields:
                            return ['명시한 최종 출력 열의 목록·순서와 실제 결과가 다릅니다: '+n['id']]
                        if all_columns and not set(expected)<=set(actual):
                            return ['전체 열 저장 요청과 실제 저장 헤더가 다릅니다: '+n['id']]
    except (ValueError, KeyError, TypeError):
        return ['출력 열 요청의 최종 저장 경로를 확인하지 못했습니다.']
    return []


def project_output_selection(candidates,state):
    intent = output_requirements(state)
    fields = intent['fields']
    if not fields:return candidates
    result=deepcopy(candidates)
    source = intent['field_source']
    if not any(c['spec'].get('kind')=='select_fields' for c in result):
        index=next((i for i,item in enumerate(result) if item['spec'].get('kind')=='export'),len(result))
        result.insert(index,dict(source_message=source,description='명시한 최종 출력 열',spec=dict(kind='select_fields',fields=fields)))
    output_format = _projectable_format(intent)
    if output_format and not any(c['spec'].get('kind')=='export' for c in result):
        result.append(dict(source_message=intent['format_sources'][output_format],description='명시한 결과 파일 저장',
                           spec=dict(kind='export',format=output_format)))
    return result


def literal_numeric_filters(state):
    return _literal_numeric_contracts(state)[0]


def literal_or_filters(state):
    return _literal_numeric_contracts(state)[1]


def _literal_number(value):
    number = number_value(value)
    approximate = float(number)
    return approximate if Decimal(str(approximate)) == number else str(number)


def _literal_numeric_contracts(state):
    """Retain literal criteria until that field is explicitly revised/cancelled.

    Historical output columns remain eligible after an output-only revision.
    Clauses are complete assertions, never numbers in examples or questions.
    """
    assets=state.get('resources',[])
    if len(assets)!=1 or assets[0].get('kind')!='table':return [], []
    from .explicit_table_criteria import explicit_criteria, expected_specs
    explicit = explicit_criteria(state)
    if explicit is not None and 'selection' in explicit:
        filters = [s for s in expected_specs(explicit) if s['kind']=='filter']
        # An explicit replacement contract owns both the operands and their
        # connective. Its separate validator checks the complete OR expression.
        return ([f for s in filters if s['match']=='all' or len(s['filters'])==1
                 for f in s['filters'] if f['value_type']=='number'], [])
    turns=list(_request_turns(state))
    fields=set()
    columns = _known_columns(state)
    for _,text in turns:
        fields.update(_selection_fields(text,columns) or [])
    if not fields:
        return [], []
    names = '|'.join(re.escape(f) for f in sorted(fields, key=len, reverse=True))
    tracked = {}
    alternatives = {}
    threshold = re.compile(
        r'\s*(' + names + r')(?:이|가|은|는)?\s+'
        r'([+-]?\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)')
    for _,text in turns:
        parts = (part for _, clause in request_clauses(text) for part in re.split(r',\s+|[\n;]', clause))
        for part in parts:
            match = threshold.match(part)
            tail = part[match.end():] if match else ''
            if match and (written_range_action(tail, require_action=True) or re.fullmatch(r'\s*[.!]?\s*', tail)):
                tracked[match[1]] = None
                alternatives.pop(match[1], None)
            cancel=re.fullmatch(r'\s*('+names+r')\s*(?:숫자\s*)?(?:조건|기준|필터)(?:은|는|을|를)?\s*'
                                r'(?:취소|삭제|제거|해제)(?:해\s*(?:줘|주세요))?\s*[.!]?\s*',part)
            if cancel:
                tracked.pop(cancel[1], None)
                alternatives.pop(cancel[1], None)
            alternative=re.fullmatch(r'\s*[^,;\n]{1,80}?(?:이거나|또는|혹은)\s+'
                r'('+names+r')(?:이|가|은|는)?\s+(\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)(?:인)?\s*'
                r'(?:주문|행|건)(?:을|만)?\s*(?:대상으로|남겨\s*(?:줘|주세요))\s*[.!]?\s*',part)
            if alternative:
                tracked.pop(alternative[1], None)
                alternatives[alternative[1]]=dict(field=alternative[1],
                    operator={'이상':'gte','이하':'lte','초과':'gt','미만':'lt'}[alternative[3]],
                    value=_literal_number(alternative[2]),compare_field=None,value_type='number')
    # Projection establishes which fields need independent proof. The
    # condition replay alone decides their latest values and polarity.
    messages = [dict(role='user', content=text) for _, text in turns]
    required = [dict(field=field, operator=operator, value=_literal_number(value), compare_field=None, value_type='number')
                for field, value, operator in written_filter_bounds(messages, columns | fields, tracked)] if tracked else []
    return required, list(alternatives.values())


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
    # A complete literal computation + threshold over a single connected table
    # needs three distinct task kinds: read, compute and filter. Prevent native
    # decoding from ending after read. Do not impose a guessed workflow length
    # on general requests, inherited workflows, or condition-owned task kinds.
    kinds = result['$defs']['RequiredTask']['properties']['spec']['discriminator']['mapping']
    if (computed and {'read', 'compute', 'filter'} <= set(kinds)
            and not state.get('base_agent_id') and not state.get('required_node_refs')):
        tasks = result['properties']['tasks']
        tasks['minItems'] = max(tasks.get('minItems', 0), 3)
    return result
