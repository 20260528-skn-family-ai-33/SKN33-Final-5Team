"""Materialize validated condition references in the legacy executable task schema."""
import re


def additional_row_predicates(state, primary):
    """Only independent, explicit assertions about rows of the same input table.

    A single Condition filter cannot establish how predicates on distinct
    inputs/outputs combine. Leave those cases to requirements validation.
    """
    tables = [resource for resource in state.get('resources', []) if resource.get('kind') == 'table']
    if len(tables) != 1:
        return []
    users = [m['content'] for m in state.get('messages', []) if m['role'] == 'user']
    fields = [re.escape(name) for name in tables[0].get('columns', []) if name != primary and name]
    if not fields:
        return []
    assertion = re.compile(
        r"(?:또한\s+|그리고\s+)?(?P<field>" + '|'.join(fields)
        + r")\s*(?:열[의은는이가]?\s*)?(?:이|가|은|는)\s*"
          r"""(?P<quote>['"]?)(?P<value>[^\s,'".!?]+?)(?P=quote)인\s+행"""
          r"(?:이어야\s*(?:해|합니다)|만\s*(?:남겨|골라|선별해)(?:줘|주세요)?)")
    additions = []
    for source, message in enumerate(users):
        for clause in re.split(r'[.!?\n;]', message):
            match = assertion.fullmatch(clause.strip())
            if not match:
                continue
            if (multiple_outputs(state) or re.search(r'또는|혹은|이거나|이든|아니면', message)
                    or any(match['field'] in m for m in users[source + 1:])):
                raise ValueError('별도 선별 조건을 적용할 출력·수정 범위를 확인하지 못했습니다.')
            predicate = dict(field=match['field'], operator='eq', value=match['value'],
                             compare_field=None, value_type='text')
            if not any(other == predicate for _, other in additions):
                additions.append((source, predicate))
    return additions


def owned_specs(confirmed, state):
    if not confirmed:
        return {}
    slots = confirmed['slots']
    values = {key: slot['value'] for key, slot in slots.items()}
    specs = {}

    def add(kind, label, keys, **fields):
        source = max(slot['source_message'] for key, slot in slots.items() if key in keys)
        specs[kind] = {'source_message': source, 'description': label, 'spec': {'kind': kind, **fields}}

    schedule_keys = [key for key in slots if key.startswith('schedule.')]
    if 'schedule' in confirmed['operations'] and schedule_keys:
        schedule = {name: values.get('schedule.' + name) for name in
                    ('frequency', 'timezone', 'hour', 'minute', 'weekday', 'monthday')}
        schedule['month_end'] = values.get('schedule.month_end', False)
        add('schedule', '확정한 주기와 시각에 예약 실행', schedule_keys, schedule=schedule)
    filter_keys = ['filter.field', 'filter.operator', 'filter.value']
    if 'filter' in confirmed['operations'] and all(key in slots for key in filter_keys):
        value = values['filter.value']
        data_type = 'boolean' if isinstance(value, bool) else 'number' if isinstance(value, (float, int)) else 'text'
        add('filter', '확정한 비교 조건으로 행 선별', filter_keys, match='all', filters=[{
            'field': values['filter.field'], 'operator': values['filter.operator'],
            'value': value, 'compare_field': None, 'value_type': data_type}])
        for source, predicate in additional_row_predicates(state, values['filter.field']):
            specs['filter']['spec']['filters'].append(predicate)
            specs['filter']['source_message'] = max(specs['filter']['source_message'], source)
    policy_keys = ['missing.policy', 'duplicate.policy', 'duplicate.field']
    if any(key in slots for key in policy_keys[:2]):
        add('policy', '확정한 빈값·중복 정책 적용', policy_keys, missing_policy=values.get('missing.policy'),
            duplicate_policy=values.get('duplicate.policy'), duplicate_field=values.get('duplicate.field'))
    if 'output.format' in slots:
        add('export', '요청한 형식으로 결과 저장', ['output.format'], format=values['output.format'])
    return specs


def multiple_outputs(state):
    text = ' '.join(m['content'] for m in state.get('messages', []) if m['role'] == 'user')
    return bool(re.search(r'각각|각자|별도|따로|두\s*(?:개|파일)|둘\s*다|모두|원본.{0,12}결과|(?:csv|json|xlsx|pdf|md)\s*(?:와|과|및|,|/|와 함께|이?랑)\s*(?:csv|json|xlsx|pdf|md)', text, re.I))


def ordered_row_steps(state):
    """Identify an explicit handoff between two confirmed operations on one table."""
    if not state or multiple_outputs(state):
        return ()
    ledger = state.get('condition_ledger') or {}
    if set(ledger.get('operations', [])) != {'filter', 'deduplicate'}:
        return ()
    resources = state.get('resources', [])
    if len(resources) != 1 or resources[0].get('kind') != 'table':
        return ()
    slots = ledger.get('slots', {})
    fields = {kind: slots.get(key, {}).get('value') for kind, key in
              [('filter', 'filter.field'), ('policy', 'duplicate.field')]}
    if (not all(f in resources[0].get('columns', []) for f in fields.values())
            or len(set(fields.values())) != 2):
        return ()
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    # A bare acknowledgement carries no new row or order evidence. For
    # substantive later instructions the source relation must be re-established.
    if not users or any(not re.fullmatch(r'\s*(?:네|예|응|확인(?:했어|했습니다)?|yes|ok)[.!\s]*', reply, re.I)
                        for reply in users[1:]):
        return ()
    text = users[0].casefold()
    positions = {}
    for kind, field in fields.items():
        mentions = list(re.finditer(r'(?<![a-z0-9_])' + re.escape(field.casefold()) + r'(?![a-z0-9_])', text))
        if len(mentions) != 1:
            return ()
        positions[kind] = mentions[0].span()
    before, after = sorted(positions, key=positions.get)
    connector = text[positions[before][1]:positions[after][0]]
    if re.search(r'(?:한|긴|된|낸|친|른)\s*(?:후|뒤|다음)|그\s*결과(?:에서|에|\s*안에서)|\bthen\b', connector):
        return (before, after)
    return ()


_BOUND_OPERATORS = {'초과':'gt', '>':'gt', '이상':'gte', '>=':'gte', '≥':'gte',
                    '미만':'lt', '<':'lt', '이하':'lte', '<=':'lte', '≤':'lte',
                    '같음':'eq', '==':'eq', '=':'eq', '!=':'ne'}
_NUMBER = r'[+\-\d.,eE]+'


def _covered_filter_clause(description, filters):
    """Replace a complete comparison with its operation label only if its operands agree."""
    from contracts.numeric_criteria import number_value

    if len(filters) != 1 or filters[0].get('value_type') != 'number':
        return description
    predicate = filters[0]
    if predicate.get('compare_field') or predicate.get('value') is None:
        return description
    field = re.escape(predicate['field'])
    subject = rf'(?<![\w]){field}(?![A-Za-z0-9_])\s*(?:열\s*)?(?:이|가|은|는)?\s*'
    comparison = (rf'(?:(?P<value>{_NUMBER})\s*(?P<word>초과|이상|미만|이하|같음)'
                  rf'|(?P<symbol>>=|<=|==|!=|[><=≥≤])\s*(?P<number>{_NUMBER})(?![\d.,]))')
    rows = r'(?:\s*인)?\s*(?:(?:행|항목)(?:들)?(?:만|을|를)?)?'
    verb = r'(?:\s*(?:필터링|선별)(?:하여|해서|하고|한\s*(?:뒤|후))?)?'

    def verified(match):
        operator = _BOUND_OPERATORS[match['word'] or match['symbol']]
        try:
            value = number_value(match['value'] or match['number'])
        except ValueError:
            return match.group()
        if operator == predicate['operator'] and value == number_value(predicate['value']):
            return ' 필터 '
        return match.group()

    return re.sub(subject + comparison + rows + verb, verified, description)


def _owned_other(candidate, expected):
    spec = candidate['spec']
    if set(spec) != {'kind', 'action'} or not isinstance(spec['action'], str):
        return False
    # Packed model identifiers are only redundant when all effects, the
    # resulting artifact, and the accompanying task description are covered.
    policy = expected.get('policy', {}).get('spec', {})
    export = expected.get('export', {}).get('spec', {}).get('format')
    effects = {}
    if export:
        words = [export, '저장', '내보내기', 'export', 'save']
        effects.update({'export': words, 'export_' + export: words})
    if policy.get('duplicate_policy') in ('first', 'last') and policy.get('duplicate_field'):
        retention = '첫 행' if policy['duplicate_policy'] == 'first' else '마지막 행'
        effects['deduplicate'] = ['deduplicate', '중복', '행', '제거', '정리', retention, policy['duplicate_field']]
    if 'filter' in expected:
        filters = expected['filter']['spec']['filters']
        effects['filter'] = ['filter', '필터', '필터링', '선별', *(f['field'] for f in filters)]
    if 'schedule' in expected:
        effects['schedule'] = ['schedule', '예약', '실행']
    parts = re.split(r'_and_|\+', spec['action'].lower())
    if any(part not in effects for part in parts) or len(set(parts)) != len(parts):
        return False
    # Exporting an intermediate, or switching the order of two row
    # transformations, can change the user's artifact.
    if any(p.startswith('export') for p in parts[:-1]) or {'filter', 'deduplicate'} <= set(parts):
        return False
    description = candidate.get('description')
    if not isinstance(description, str) or not description.strip() or len(description) > 300:
        return False
    if 'filter' in parts:
        description = _covered_filter_clause(description, expected['filter']['spec']['filters'])
    missing = policy.get('missing_policy')
    if missing in ('error', 'keep', 'exclude'):
        qualified = {'error': r'오류(?:로)?\s*처리|오류', 'keep': '유지|보존', 'exclude': '제외|삭제'}[missing]
        description = re.sub(rf'(?:빈\s*값|결측값)\s*(?:을|은)?\s*(?:{qualified}|처리)', ' ', description)
    # The identifier can claim a final export even while the prose asks for
    # exporting the original before processing. Keep that different lineage.
    if len(parts) > 1 and parts[-1].startswith('export'):
        output = re.search(r'저장|내보내기|export|save', description, re.I)
        if output and re.search(r'중복|필터|선별|예약|deduplicate|filter|schedule', description[output.end():], re.I):
            return False
    words = [word for part in parts for word in effects[part]] + [
        '요청한', '결과', '형식', '파일', '처리', '으로', '로', '을', '를', '및', '후', '만', 'and', 'to', 'as']
    vocabulary = '|'.join(re.escape(word) for word in sorted(set(words), key=len, reverse=True))
    return not re.sub(rf'{vocabulary}|[\s,·/+().-]+', '', description, flags=re.I)


def _only_filter_request(request, predicate, fmt, policy):
    """Prove all clauses of the original qualitative request; leave unrecognized work intact."""
    from agent_builder.conditions.condition_readiness import _DUP_OTHER, _MISSING_ACTS

    direction = {'gt':'큰|높은|많은', 'gte':'큰|높은|많은',
                 'lt':'작은|낮은|적은', 'lte':'작은|낮은|적은'}.get(predicate['operator'])
    clauses = [part.strip() for part in re.split(r'[.,;!?\n]+', request) if part.strip()]
    if not direction or not clauses:
        return False
    field = re.escape(predicate['field'])
    instruction = (rf'(?:(?:연결|첨부)(?:한|된)\s*)?(?:표|테이블)(?:에서|의)\s*'
                   rf'{field}\s*(?:이|가|은|는)?\s*(?:{direction})\s*(?:행|항목)만\s*'
                   rf'{re.escape(fmt)}\s*(?:파일)?(?:으로|로)\s*(?:저장|내보내)(?:해)?\s*(?:줘|주세요|세요)?')
    if not re.fullmatch(instruction, clauses[0], re.I):
        return False
    policies = [(r'빈\s*값|결측값', _MISSING_ACTS.get(policy.get('missing_policy'))),
                ('중복', _DUP_OTHER.get(policy.get('duplicate_policy')))]
    return all(any(action and re.fullmatch(
        rf'(?:{subject})\s*(?:은|는|을|를)?\s*(?:{action})(?:로\s*처리)?(?:해(?:\s*줘|\s*주세요|요)?)?', clause)
        for subject,action in policies) for clause in clauses[1:])


def _answered_filter_export(candidate, expected, state):
    """A generic label supplies no evidence. Match the whole description to a linked refinement."""

    if candidate['spec'] != {'kind':'other', 'action':'execute'} or not {'filter','export'} <= expected.keys():
        return False
    description = candidate.get('description')
    filters = expected['filter']['spec']['filters']
    if not isinstance(description, str) or not 1 <= len(description) <= 300 or len(filters) != 1:
        return False
    fmt = expected['export']['spec']['format']
    clause = _covered_filter_clause(description, filters)
    if (clause == description or not re.fullmatch(
            rf'\s*필터\s*(?:후\s*)?(?:결과(?:를|만)?\s*)?{re.escape(fmt)}\s*(?:형식|파일)?\s*(?:으로|로)?\s*(?:저장|내보내기)[.\s]*',
            clause, re.I)):
        return False
    users = [(i,m['content']) for i,m in enumerate(state['messages']) if m['role'] == 'user']
    source = candidate['source_message']
    answer_index = expected['filter']['source_message']
    if not (source == expected['export']['source_message'] == 0 and answer_index == 1 and len(users) == 2):
        return False
    source_turn, request = users[source]
    turn, reply = users[answer_index]
    origin = (state.get('condition_ledger') or {}).get('slots', {}).get('filter.field', {}).get('evidence', [])
    if not any(e.get('source_id') == 'user' and e.get('turn') == source_turn for e in origin):
        return False
    if not _only_filter_request(request, filters[0], fmt, expected.get('policy', {}).get('spec', {})):
        return False
    keys = {'filter.operator', 'filter.value'}
    linked = any(e.get('turn') == turn and e.get('answer') == reply and e.get('question_ids')
                 and e.get('candidate_keys') and set(e['candidate_keys']) <= keys
                 for e in state.get('condition_answers', []))
    answer_clause = _covered_filter_clause(filters[0]['field'] + ' ' + reply, filters)
    return linked and bool(re.fullmatch(r'\s*필터\s*(?:으로|로)?\s*(?:해\s*(?:줘|주세요|요))?[.!?\s]*', answer_clause))


def project_owned(candidates, expected, state):
    from .requirement_task_identity import restates_table_export

    result = []
    seen = set()
    multi = multiple_outputs(state)
    resources = state.get('resources', [])
    single_table = len(resources) == 1 and resources[0].get('kind') == 'table'
    for kind in ('schedule', 'filter', 'policy'):
        related = [c['spec'] for c in candidates if c['spec'].get('kind') == kind]
        if kind in expected and any(spec != related[0] for spec in related[1:]):
            raise ValueError('서로 다른 ' + kind + ' 업무의 대상과 근거를 구분해야 합니다.')
    for candidate in candidates:
        spec = candidate['spec']
        kind = spec.get('kind')
        if (kind == 'other' and single_table and not multi and
                (_owned_other(candidate, expected) or restates_table_export(candidate, expected, state)
                 or len(candidates) == 1 and _answered_filter_export(candidate, expected, state))):
            continue
        if kind in expected:
            if kind == 'filter' and len(spec.get('filters', [])) > 1:
                from .requirement_coverage import _same_filters
                grounded = expected[kind]['spec']
                if not _same_filters(spec['filters'], grounded['filters'], spec.get('match'), grounded['match']):
                    raise ValueError('복합 선별 조건의 원문·대상·결합 방식을 확인하지 못했습니다.')
            # Keep explicitly separate artifacts. A single Condition output
            # format cannot describe every branch of a multi-output request.
            if kind == 'export' and multi and spec != expected[kind]['spec']:
                result.append(candidate)
                continue
            seen.add(kind)
            candidate = {**candidate, 'spec': expected[kind]['spec']}
        result.append(candidate)
    for kind, candidate in expected.items():
        if kind not in seen:
            insert = len(result) if kind == 'export' else next(
                (i for i, task in enumerate(result) if task['spec']['kind'] == 'export'), len(result))
            result.insert(insert, candidate)
    return result


def validate_owned(plan, expected, state):
    multi = multiple_outputs(state)
    for kind, candidate in expected.items():
        matching = [t['spec'] for t in plan['tasks'] if t['spec']['kind'] == kind]
        if candidate['spec'] not in matching or (not (multi and kind == 'export')
                and any(spec != candidate['spec'] for spec in matching)):
            raise ValueError('검증된 업무 조건과 요구사항 설정이 다릅니다: ' + kind)
    order = ordered_row_steps(state) if expected else ()
    if order:
        pair = {t['spec']['kind']: t for t in plan['tasks'] if t['spec']['kind'] in order}
        if not all(kind in pair for kind in order) or pair[order[0]]['id'] not in pair[order[1]]['depends_on']:
            raise ValueError('요청한 선별·중복 제거 순서를 선행 업무에 반영해야 합니다.')
