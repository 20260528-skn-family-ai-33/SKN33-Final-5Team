"""Order-sensitive table operations; legacy filter_sort runs dedup before filtering."""
import re


ORDER_INSTRUCTIONS = '''
filter_sort 내부 순서는 중복 제거 → 빈 값 처리 → 선별 → 정렬입니다.
선별한 뒤 중복을 제거하라는 요청은 filter_sort 두 단계로 작성합니다.
첫 단계는 선별 조건과 duplicate_policy=keep, duplicate_field=null을 사용합니다.
다음 단계는 앞 단계 table 출력을 입력으로 받아 filters=[], sort=[], missing_policy=keep과 요청한 중복 정책을 적용합니다.
중복 제거 후 선별 요청은 그 순서를 유지합니다. 단계별 execution_plan을 지정하고 공통 설정에 합쳐 순서를 바꾸지 않습니다.
'''


def deduplication_step(spec):
    return spec.get('kind') == 'policy' and spec.get('duplicate_policy') in ('first', 'last', 'error')


def same_node_precedes(before, after):
    """Dependencies sharing a native filter_sort must follow its actual phases."""
    def phases(spec):
        if spec['kind'] == 'filter':
            return [2]
        if spec['kind'] == 'sort':
            return [3]
        if spec['kind'] == 'policy':
            return ([0] if deduplication_step(spec) else []) + (
                [1] if spec.get('missing_policy') in ('exclude', 'error') else [])
        return None
    left, right = phases(before), phases(after)
    return left is not None and right is not None and (not left or not right or max(left) <= min(right))


_FILTER = re.compile(r'선별|필터(?:링)?|골라|고른|고르|추려|추린|거른')
_DEDUP = re.compile(r'중복|(?:첫|마지막)\s*행')
_KEEP = re.compile(r'중복(?:은|을|도)?\s*(?:그대로\s*)?(?:유지|보존|남겨|남기)(?!(?:하)?지\s*(?:마|말|않))')
_CANCEL_FILTER = re.compile(r'(?:선별|필터(?:링)?)(?:하)?지\s*(?:마|말|않)|(?:선별|필터(?:링)?)(?:은|을)?\s*(?:취소|빼|제외)')
_CANCEL_DEDUP = re.compile(r'중복.{0,15}(?:제거(?:하)?지\s*(?:마|말|않)|제거(?:는|를)?\s*(?:취소|빼|제외))|(?:첫|마지막)\s*행.{0,10}선택(?:하)?지\s*(?:마|말|않)')
_SEQUENCE = re.compile(r'(?:뒤|후|다음|나서)(?:에)?(?=\s|[,;]|$)')
_QUOTES = re.compile(r'''“[^”]*”|‘[^’]*’|"[^"]*"|'[^']*'|`[^`]*`''')
_NEGATED_ACTION = re.compile(
    r'(?:선별|필터(?:링)?|제거|선택|남기|남겨)(?:하)?지\s*(?:마|말|않)'
    r'|(?:선별|필터(?:링)?|제거|선택)(?:이|가)?\s*(?:아니|아닌)')


def _ledger_order(state):
    from .requirement_ledger import ledger_fingerprint, plan_digest, validate_plan
    ledger = state.get('requirement_ledger')
    if (not state.get('requirement_required') or not ledger or
            state.get('requirement_fingerprint') != ledger_fingerprint(state) or
            state.get('requirement_digest') != plan_digest(ledger)):
        return None
    try:
        plan = validate_plan(ledger, state)
    except (ValueError, TypeError, KeyError):
        return None
    if plan['unresolved']:
        return None
    tasks = {task['id']: task for task in plan['tasks']}
    found = set()
    for task in tasks.values():
        pending = list(task['depends_on'])
        seen = set()
        while pending:
            dependency = pending.pop()
            if dependency in seen:
                continue
            seen.add(dependency)
            before, after = tasks[dependency]['spec'], task['spec']
            if before['kind'] == 'filter' and deduplication_step(after):
                found.add('filter_first')
            if deduplication_step(before) and after['kind'] == 'filter':
                found.add('dedup_first')
            pending.extend(tasks[dependency]['depends_on'])
    return 'filter_first' if 'filter_first' in found else 'dedup_first' if found else None


def _text_steps(state):
    """Retain the latest explicit chain, with each action's own source phrase.

    None means no textual instruction; [] means an explicit cancellation, which
    must not fall back to an earlier ledger. Quotes never change the active chain.
    """
    from agent_builder.capabilities.capability_judgement import _assessment_messages
    steps = None
    for message in _assessment_messages(state):
        if message.get('role') != 'user':
            continue
        text = _QUOTES.sub(' ', message['content'])
        chains = []
        for clause in re.split(r'[.!?;\n]', text):
            if _KEEP.search(clause) or _CANCEL_FILTER.search(clause) or _CANCEL_DEDUP.search(clause):
                steps = []
                chains = []
                continue
            markers = sorted([(m.start(), m.end(), 'filter') for m in _FILTER.finditer(clause)] +
                             [(m.start(), m.end(), 'deduplicate') for m in _DEDUP.finditer(clause)])
            linked = any(any(a[0] < link.start() and b[0] >= link.end() and a[2] != b[2]
                             for a, b in zip(markers, markers[1:]))
                         for link in _SEQUENCE.finditer(clause))
            if not linked:
                continue
            if _NEGATED_ACTION.search(clause) or re.search(r'또는|혹은', clause):
                chains.append('ambiguous')
                continue
            chain = []
            start = 0
            for position, end, kind in markers:
                phrase = clause[start:end]
                new_filter = kind == 'filter' and any(
                    column in phrase for resource in state.get('resources', [])
                    for column in resource.get('columns', []))
                if chain and chain[-1]['kind'] == kind and not new_filter:
                    chain[-1]['text'] += phrase
                else:
                    chain.append(dict(kind=kind, text=phrase))
                start = end
            chains.append(chain)
        if chains:
            # Separate contradictory clauses have no single unambiguous chain.
            steps = chains[0] if len(chains) == 1 else 'ambiguous'
    return steps


def filter_dedup_order(state):
    steps = _text_steps(state)
    if steps is None:
        return _ledger_order(state)
    if steps == 'ambiguous':
        return 'ambiguous'
    if not steps:
        return None
    # Any filter -> dedup transition needs independent per-stage settings,
    # including a longer filter -> dedup -> filter chain.
    return ('filter_first' if any(a['kind'] == 'filter' and b['kind'] == 'deduplicate'
                                 for a, b in zip(steps, steps[1:])) else 'dedup_first')


def separate_filter_dedup(state):
    if filter_dedup_order(state) in ('filter_first', 'ambiguous'):
        return True
    from .requirement_generation import generation_requirements
    # Fixed IDs and a shared confirmed plan cannot represent repeated stages,
    # even when their ordering comes entirely from the sealed requirement ledger.
    return generation_requirements(state)[1].count('filter_sort') > 1


def _step_candidates(step, nodes, draft):
    from agent_builder.conditions.condition_readiness import _amounts
    from contracts.numeric_criteria import number_value
    candidates = []
    fields = set()
    amounts = _amounts(step['text']) if step['kind'] == 'filter' else set()
    operators = {operator for word, operator in [('초과', 'gt'), ('이상', 'gte'), ('미만', 'lt'), ('이하', 'lte')]
                 if word in step['text']}
    for identifier, node in nodes.items():
        if node['tool'] != 'filter_sort' or node['settings'].get('operation'):
            continue
        plan = node['settings'].get('execution_plan') or draft.get('execution_plan') or {}
        if step['kind'] == 'filter':
            used = {f['field'] for f in plan.get('filters', [])}
            # Repeated filters on the same column still represent different
            # requested actions. Bind explicit thresholds to their own stage.
            numeric = [f for f in plan.get('filters', []) if f.get('value_type') == 'number'
                       and f.get('value') is not None]
            if amounts and numeric:
                try:
                    if not any(number_value(f['value']) in amounts and
                               (not operators or f['operator'] in operators) for f in numeric):
                        continue
                except (ValueError, TypeError):
                    continue
        else:
            if plan.get('duplicate_policy') not in ('first', 'last', 'error'):
                continue
            used = {plan['duplicate_field']} if plan.get('duplicate_field') else set()
        if used:
            candidates.append((identifier, used))
            fields.update(used)
    mentioned = {field for field in fields if field in step['text']}
    return [identifier for identifier, used in candidates if not mentioned or mentioned <= used]


def table_order_issues(state, draft):
    """Assign requested actions to a consistent chain of actual table inputs."""
    steps = _text_steps(state)
    if draft.get('questions') or draft.get('unsupported') or steps == []:
        return []
    if steps is None:
        # A sealed ledger already matches exact settings and per-task dependencies
        # in coverage_report, including chains containing both order directions.
        return []
    if steps == 'ambiguous':
        return ['선별·중복 제거 순서를 명확히 확인해야 합니다.']
    from .content_lineage import content_ancestors
    nodes = {n['id']: n for n in (draft.get('workflow') or {}).get('nodes', [])}
    upstream = {identifier: content_ancestors(nodes, identifier) for identifier in nodes}
    candidates = [_step_candidates(step, nodes, draft) for step in steps]
    from workflow_engine.execution_path import artifact_output_ids, artifact_origin
    try:
        outputs = artifact_output_ids(draft['workflow'])
        if outputs:
            final = artifact_origin(draft['workflow'], outputs[-1])
            saved_path = upstream[final] | {final}
            candidates = [[identifier for identifier in options if identifier in saved_path]
                          for options in candidates]
    except (ValueError, KeyError, TypeError):
        candidates = [[] for _ in steps]
    reachable = set(candidates[0])
    for index, options in enumerate(candidates[1:], 1):
        share = steps[index - 1]['kind'] == 'deduplicate' and steps[index]['kind'] == 'filter'
        reachable = {b for b in options if any((a == b and share) or a in upstream[b] for a in reachable)}
    if reachable:
        return []
    expected = ' → '.join('선별' if step['kind'] == 'filter' else '중복 제거' for step in steps)
    return [f'요청한 처리 순서({expected})가 실제 표 데이터 연결에 반영되지 않았습니다. '
            'filter_sort 내부는 중복 제거가 먼저이므로 다른 순서는 단계별 설정과 입력 연결을 분리해야 합니다.']
