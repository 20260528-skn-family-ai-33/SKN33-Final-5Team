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
    return next(iter(found)) if len(found) == 1 else 'ambiguous' if found else None


def filter_dedup_order(state):
    """Read explicit Korean before/after clauses only, never unordered policy lists.

    The latest explicit ordering wins; an unrelated follow-up retains it.
    Unknown wording stays with the planner and the structured requirement ledger.
    """
    from agent_builder.capabilities.capability_judgement import _assessment_messages
    order = None
    for message in _assessment_messages(state):
        if message.get('role') != 'user':
            continue
        found = set()
        for clause in re.split(r'[.!?;\n]', _QUOTES.sub(' ', message['content'])):
            markers = sorted([(m.start(), 'filter') for m in _FILTER.finditer(clause)] +
                             [(m.start(), 'deduplicate') for m in _DEDUP.finditer(clause)])
            for link in _SEQUENCE.finditer(clause):
                before = [kind for position, kind in markers if position < link.start()]
                after = [kind for position, kind in markers if position >= link.end()]
                if before and after and before[-1] != after[0]:
                    if _NEGATED_ACTION.search(clause):
                        found.add('ambiguous')
                    else:
                        found.add('filter_first' if before[-1] == 'filter' else 'dedup_first')
        if found:
            order = next(iter(found)) if len(found) == 1 else 'ambiguous'
    return order or _ledger_order(state)


def separate_filter_dedup(state):
    return filter_dedup_order(state) in ('filter_first', 'ambiguous')


def table_order_issues(state, draft):
    """Check data bindings, not node labels or the array/edge presentation order."""
    order = filter_dedup_order(state)
    if not order or draft.get('questions') or draft.get('unsupported'):
        return []
    if order == 'ambiguous':
        return ['선별·중복 제거 순서를 명확히 확인해야 합니다.']
    from .content_lineage import content_ancestors
    nodes = {n['id']: n for n in (draft.get('workflow') or {}).get('nodes', [])}
    filters, duplicates = [], []
    for identifier, node in nodes.items():
        if node['tool'] != 'filter_sort' or node['settings'].get('operation'):
            continue
        plan = node['settings'].get('execution_plan') or draft.get('execution_plan') or {}
        if plan.get('filters'):
            filters.append(identifier)
        if plan.get('duplicate_policy') in ('first', 'last', 'error'):
            duplicates.append(identifier)
    first, second = (filters, duplicates) if order == 'filter_first' else (duplicates, filters)
    if first and second and all(
        (a == b and order == 'dedup_first') or a in content_ancestors(nodes, b)
        for a in first for b in second
    ):
        return []
    expected = '선별 → 중복 제거' if order == 'filter_first' else '중복 제거 → 선별'
    return [f'요청한 처리 순서({expected})가 실제 표 데이터 연결에 반영되지 않았습니다. '
            'filter_sort 내부는 중복 제거가 먼저이므로 다른 순서는 단계별 설정과 입력 연결을 분리해야 합니다.']
