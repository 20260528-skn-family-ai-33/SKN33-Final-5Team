"""Refresh derived descriptions without rewriting requests or executable settings.

Only registered Gmail recipes and native table plans supply enough evidence to
refresh text in other parts of a draft. Unrecognised prose stays visible to the
node edit dependency guard instead of being changed by numeric substitution.
"""
from copy import deepcopy
import json
import re


_COUNT_PATHS = {'arguments_json.max_results', 'arguments_json.limit', 'arguments_json.count'}
_TABLE_TOOLS = {'read_table', 'filter_sort', 'aggregate_table', 'write_result', 'finish'}


def _count_pattern(value):
    return re.compile(r'(?<![\d.+-])' + re.escape(str(value))
                      + r'\s*(건|개(?!월|년)|통)(?![A-Za-z_\d])')


def _count_change(changes):
    counts = [item for item in changes if item['path'] in _COUNT_PATHS]
    if len(counts) != 1:
        return None
    item = counts[0]
    if type(item['old_value']) is not int or type(item['new_value']) is not int:
        return None
    return item


def _json_mirror(node):
    settings = node.get('settings') or {}
    plan = settings.get('execution_plan')
    if not isinstance(plan, dict) or not isinstance(settings.get('instructions'), str):
        return False
    try:
        return json.loads(settings['instructions']) == plan
    except (ValueError, TypeError):
        return False


def sync_node_text(before_node, after_node, changes):
    """Refresh a target's own derived label and exact execution-plan JSON mirror."""
    item = _count_change(changes)
    if item:
        pattern = _count_pattern(item['old_value'])
        label = before_node.get('label') or ''
        try:
            arguments = json.loads((before_node.get('settings') or {}).get('arguments_json') or '{}')
        except (ValueError, TypeError):
            arguments = {}
        competing = isinstance(arguments, dict) and any(
            key in arguments and arguments[key] == item['old_value']
            for key in ('max_results', 'limit', 'count') if 'arguments_json.' + key != item['path'])
        if not competing and len(pattern.findall(label)) == 1:
            after_node['label'] = pattern.sub(lambda match: str(item['new_value']) + match[1], label)
    if any(item['path'].startswith('execution_plan.filters[') for item in changes) and _json_mirror(before_node):
        after_node['settings']['instructions'] = json.dumps(
            after_node['settings']['execution_plan'], ensure_ascii=False)


def _table_descriptions(draft):
    """Render only from effective per-node plans, on a disposable copy."""
    nodes = (draft.get('workflow') or {}).get('nodes') or []
    if not nodes or any(node.get('tool') not in _TABLE_TOOLS
                        or (node.get('settings') or {}).get('operation') for node in nodes):
        return None
    filters = [node for node in nodes if node.get('tool') == 'filter_sort']
    if not filters:
        return None
    from .presentation import OPS, canonical_table_draft, public_draft_explanation
    rendered = deepcopy(draft)
    # The global fallback can contain an old rule which is overridden by every
    # filter node. It is not a description of the effective workflow.
    rendered['execution_plan'] = deepcopy(filters[0]['settings'].get('execution_plan')
                                        or draft['execution_plan'])
    rendered = canonical_table_draft(rendered, [], [])
    # The legacy renderer also emits raw execution-plan JSON. Keep its general
    # workflow prose, and describe every effective node rule in ordinary text.
    raw_prefixes = tuple('노드 ' + node['label'] + '의 실제 규칙:' for node in filters)
    lines = [line for line in rendered['instructions'].splitlines()
             if not line.startswith(('조건:', '빈 값:', '중복:', '정렬:', *raw_prefixes))]
    for node in filters:
        plan = node['settings'].get('execution_plan') or draft['execution_plan']
        conditions = []
        for condition in plan['filters']:
            if condition.get('compare_field'):
                right = '열 “' + condition['compare_field'] + '”'
            elif condition['operator'] in ('is_empty', 'not_empty'):
                right = ''
            else:
                value = condition['value']
                right = json.dumps(value, ensure_ascii=False) if type(value) is str else str(value)
            conditions.append((condition['field'] + ' ' + OPS[condition['operator']] + ' ' + right).strip())
        prefix = '“' + node['label'] + '” '
        lines.append(prefix + '조건: ' + (' 그리고 ' if plan['match'] == 'all' else ' 또는 ').join(conditions)
                     if conditions else prefix + '조건: 모든 행 유지')
        lines.append(prefix + '빈 값: ' + {'ask': '처리 정책 확인 필요', 'exclude': '제외',
                     'keep': '유지하되 다른 조건도 적용', 'error': '오류로 중단'}[plan['missing_policy']])
        lines.append(prefix + '중복: ' + {'ask': '처리 정책 확인 필요', 'keep': '유지',
                     'first': '원본 첫 행 유지', 'last': '원본 마지막 행 유지', 'error': '오류로 중단'}[
                         plan['duplicate_policy']] + (' · 기준 ' + plan['duplicate_field'] if plan['duplicate_field'] else ''))
        sort = ', '.join(item['field'] + ' ' + ('숫자 ' if item['value_type'] == 'number' else '문자 ')
                         + ('오름차순' if item['direction'] == 'ascending' else '내림차순') for item in plan['sort'])
        lines.append(prefix + '정렬: ' + (sort or '원본 순서 유지'))
    rendered['instructions'] = '\n'.join(lines)
    rendered = public_draft_explanation(rendered)
    return {key: rendered[key] for key in ('purpose', 'instructions', 'explanation')}


def _prior_request_state(state, base_turn):
    return {**state, 'messages': state.get('messages', [])[:base_turn],
            'base_agent_id': None, 'resources': [], 'required_node_refs': []}


def _reader_ancestors(node, by_id, visiting=None):
    visiting = set() if visiting is None else visiting
    if node['id'] in visiting:
        raise ValueError('요약에 연결된 작업의 순서를 확인해 주세요.')
    if node.get('tool') == 'email.list':
        return {node['id']}
    result = set()
    for binding in node.get('bindings') or []:
        parent = by_id.get(binding.get('node_id'))
        if parent:
            result.update(_reader_ancestors(parent, by_id, visiting | {node['id']}))
    return result


def _gmail_count_recipe(state, before, edit, source_turn, base_turn):
    """Prove each affected summary from bindings, regardless of workflow size."""
    item = _count_change(edit['changes'])
    if not item or len(edit['changes']) != 1 or item['path'] != 'arguments_json.max_results':
        return None
    old = item['old_value']
    nodes = (before.get('workflow') or {}).get('nodes') or []
    by_id = {node['id']: node for node in nodes}
    reader = by_id.get(edit['node_id'])
    if not reader or reader.get('tool') != 'email.list' or reader['settings'].get('operation') != 'read_messages':
        return None
    prior = _prior_request_state(state, base_turn)
    try:
        arguments = json.loads(reader['settings'].get('arguments_json') or '{}')
    except (ValueError, TypeError):
        return None
    if not isinstance(arguments, dict) or type(arguments.get('max_results')) is not int or arguments['max_results'] != old:
        return None
    from .gmail_summary import _count_summary_instructions, gmail_summary_request, gmail_summary_reply
    from .gmail_slack import gmail_slack_request, gmail_slack_reply
    generated = None
    candidates = {_count_summary_instructions(old)}
    for request, reply in ((gmail_summary_request, gmail_summary_reply),
                           (gmail_slack_request, gmail_slack_reply)):
        recipe = request(prior)
        if (not recipe or recipe.get('missing_conditions')
                or recipe.get('query') != arguments.get('query')
                or recipe.get('schedule') != before.get('schedule')
                or base_turn == source_turn and recipe['count'] != old):
            continue
        pattern = _count_pattern(recipe['count'])
        expected = pattern.sub(lambda match: str(old) + match[1], recipe['summary_instructions'])
        candidates.add(expected)
        result = reply(prior)
        if result:
            generated = {key: pattern.sub(lambda match: str(old) + match[1], result['draft'][key])
                         for key in ('name', 'purpose', 'instructions', 'explanation')}
    summaries = []
    for node in nodes:
        if node.get('tool') != 'compose_answer' or node['settings'].get('operation'):
            continue
        ancestors = _reader_ancestors(node, by_id)
        if reader['id'] not in ancestors:
            continue
        instructions = node['settings'].get('instructions')
        if instructions not in candidates:
            continue
        if ancestors != {reader['id']}:
            raise ValueError('여러 메일 조회 작업이 같은 요약에 연결되어 있습니다. 어느 조회 한도를 적용할지 대화에서 확인해 주세요.')
        summaries.append((node['id'], instructions))
    if not summaries:
        return None
    return summaries, generated, sum(node.get('tool') == 'email.list' for node in nodes)


def sync_descriptions(state, before, after, edit, source_turn, base_turn):
    """Refresh current derived prose; request records and execution settings stay intact."""
    nodes = (after.get('workflow') or {}).get('nodes') or []
    target = next((node for node in nodes if node.get('id') == edit['node_id']), None)
    if (target and target.get('tool') == 'filter_sort'
            and all(item['path'].startswith('execution_plan.filters[') for item in edit['changes'])):
        descriptions = _table_descriptions(after)
        if descriptions:
            after.update(descriptions)
            return True
    recipe = _gmail_count_recipe(state, before, edit, source_turn, base_turn)
    if not recipe:
        return False
    summaries, generated, reader_count = recipe
    item = _count_change(edit['changes'])
    pattern = _count_pattern(item['old_value'])
    replace = lambda match: str(item['new_value']) + match[1]
    from .presentation import public_draft_explanation
    public_before = public_draft_explanation(before)['explanation']
    public_after = public_draft_explanation(after)['explanation']
    for key in ('name', 'purpose', 'instructions', 'explanation'):
        value = after.get(key)
        if key in ('instructions', 'explanation') and value == public_before:
            after[key] = public_after
        elif reader_count == 1 and generated and value == generated.get(key):
            after[key] = pattern.sub(replace, value)
    summary_text = dict(summaries)
    for node in nodes:
        if node['id'] in summary_text:
            node['settings']['instructions'] = pattern.sub(replace, summary_text[node['id']])
    return True


def descriptions_to_check(before, after, edit, reconciled):
    """Return unproven prose for the caller's stale-value guard.

    A regenerated table description may honestly contain the previous number in
    a different condition. Exact plan mirrors and these deterministic fields
    therefore carry their own proof; arbitrary text and node labels do not.
    """
    regenerated = _table_descriptions(after) if reconciled and all(
        item['path'].startswith('execution_plan.filters[') for item in edit['changes']) else None
    from .presentation import public_draft_explanation
    public_summary = public_draft_explanation(after)['explanation'] if reconciled else None
    fields = [value for key in ('name', 'purpose', 'instructions', 'explanation')
              if isinstance((value := after.get(key)), str)
              and not (regenerated and regenerated.get(key) == value)
              and not (key in ('instructions', 'explanation') and value == public_summary)]
    nodes = (after.get('workflow') or {}).get('nodes') or []
    by_id = {node['id']: node for node in nodes}
    for node in nodes:
        label = node.get('label') or ''
        # Another node's independently bound count can legitimately equal the
        # previous value. Its own typed setting proves that local count phrase.
        if reconciled and node['id'] != edit['node_id']:
            try:
                arguments = json.loads((node.get('settings') or {}).get('arguments_json') or '{}')
            except (ValueError, TypeError):
                arguments = {}
            if isinstance(arguments, dict):
                for key in ('max_results', 'limit', 'count'):
                    if type(arguments.get(key)) is int:
                        label = _count_pattern(arguments[key]).sub('', label)
        fields.append(label)
        instructions = (node.get('settings') or {}).get('instructions')
        known_summary = False
        if reconciled and node.get('tool') == 'compose_answer' and not node['settings'].get('operation'):
            from .gmail_summary import _count_summary_instructions
            ancestors = _reader_ancestors(node, by_id)
            if len(ancestors) == 1:
                reader = by_id[next(iter(ancestors))]
                try:
                    arguments = json.loads(reader['settings'].get('arguments_json') or '{}')
                except (ValueError, TypeError):
                    arguments = {}
                if (isinstance(arguments, dict) and reader['settings'].get('operation') == 'read_messages'
                        and type(arguments.get('max_results')) is int):
                    known_summary = instructions == _count_summary_instructions(arguments['max_results'])
        if isinstance(instructions, str) and not _json_mirror(node) and not known_summary:
            fields.append(instructions)
    return fields
