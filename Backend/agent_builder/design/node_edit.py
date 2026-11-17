"""Apply a node popover edit only when its visible request matches the current draft.

This is an alternative to asking the design model to re-create a workflow for a
literal settings change. The proof is checked again at review and save time.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from contracts.models import AgentDraft
from workflow_engine.workflow import workflow_errors


_ARG = re.compile(r'arguments_json\.([A-Za-z_][A-Za-z0-9_]*)\Z')
_FILTER = re.compile(r'execution_plan\.filters\[([0-4])\]\.(field|value|compare_field)\Z')
_SENSITIVE = re.compile(r'secret|token|password|credential|api.?key|authorization', re.I)
_SIMPLE = re.compile(r'- ([^\n()]+) \((arguments_json\.[A-Za-z_][A-Za-z0-9_]*|execution_plan\.filters\[[0-4]\]\.(?:field|value|compare_field))\): (.*?) → (.*)\Z')
_COMPARISON = re.compile(
    r'- 조건 ([1-5])(?: \([^\n()]*\))?: (?:숫자 직접 입력|비교 열)로 설정\. '
    r'(execution_plan\.filters\[[0-4]\]\.value): ([^;→]*) → ([^;→]*); '
    r'(execution_plan\.filters\[[0-4]\]\.compare_field): ([^;→]*) → ([^;→]*); '
    r'value_type은 number로 유지\.\Z')
_FIELD_LABELS = {'max_results': '조회 한도', 'limit': '한도', 'count': '건수',
                 'threshold': '기준값', 'query': '검색 조건', 'channel_id': '채널 ID',
                 'value_column': '계산할 열', 'sum_field': '합계 열', 'field': '열 이름',
                 'compare_field': '비교할 열', 'format': '결과 형식',
                 'output_format': '결과 형식', 'null_policy': '빈 값 처리',
                 'model': '모델', 'temperature': '모델 온도'}
_TEXT_ARGUMENTS = {'query', 'channel_id', 'value_column', 'sum_field', 'field',
                   'compare_field', 'format', 'output_format', 'null_policy', 'model'}


def _field_label(path):
    arg = _ARG.fullmatch(path)
    if arg:
        return _FIELD_LABELS.get(arg[1], arg[1])
    field = _FILTER.fullmatch(path)
    return f"조건 {int(field[1]) + 1} · {('기준값' if field[2] == 'value' else _FIELD_LABELS.get(field[2], field[2]))}" if field else None


def _digest(draft):
    clean = {key: value for key, value in draft.items() if key != 'node_snapshots'}
    canonical = AgentDraft.model_validate(clean).model_dump()
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number_equal(displayed, value):
    if type(value) not in (int, float) or not displayed.strip():
        return False
    try:
        return Decimal(displayed.strip()) == Decimal(str(value))
    except InvalidOperation:
        return False


def _simple_equal(displayed, value, *, new=False):
    if type(value) is str:
        return displayed == ('(비움)' if new and value == '' else value)
    return _number_equal(displayed, value)


def _json_equal(displayed, value):
    try:
        parsed = json.loads(displayed)
    except (ValueError, TypeError):
        return False
    return type(parsed) is type(value) and parsed == value or (
        type(parsed) in (int, float) and type(value) in (int, float) and parsed == value)


def _same_value(current, expected):
    return (type(current) is type(expected) and current == expected or
            type(current) in (int, float) and type(expected) in (int, float)
            and math.isfinite(current) and math.isfinite(expected) and current == expected)


def _visible_changes(edit):
    """Bind hidden typed values to the exact text the user reviewed and sent."""
    request = edit['request']
    header = f"`{edit['label']}` 작업 (노드 ID: {edit['node_id']})의 설정을 다음과 같이 바꿔줘."
    lines = request.split('\n')
    if (len(lines) < 3 or lines[0] != header or lines[-1] != '다른 작업의 설정은 유지해줘.'
            or '\r' in request):
        raise ValueError('노드 변경 요청 형식이 화면에 표시된 내용과 다릅니다. 다시 입력해 주세요.')
    found = []
    for line in lines[1:-1]:
        simple = _SIMPLE.fullmatch(line)
        if simple:
            if simple[1] != _field_label(simple[2]):
                raise ValueError('표시된 변경 항목 이름과 경로가 다릅니다.')
            found.append((simple[2], simple[3], simple[4], 'simple'))
            continue
        pair = _COMPARISON.fullmatch(line)
        if (not pair or pair[2].rsplit('.', 1)[0] != pair[5].rsplit('.', 1)[0]
                or int(pair[1]) != int(_FILTER.fullmatch(pair[2])[1]) + 1):
            raise ValueError('노드 변경 요청의 경로와 값 형식을 확인해 주세요.')
        found.extend(((pair[2], pair[3], pair[4], 'json'),
                      (pair[5], pair[6], pair[7], 'json')))
    if len(found) != len(edit['changes']):
        raise ValueError('표시된 변경 항목과 실제 변경 항목의 개수가 다릅니다.')
    for (path, old, new, mode), change in zip(found, edit['changes']):
        match = _json_equal if mode == 'json' else _simple_equal
        if (path != change['path'] or not match(old, change['old_value'])
                or not (match(new, change['new_value']) if mode == 'json'
                        else match(new, change['new_value'], new=True))):
            raise ValueError('표시된 이전 값 또는 새 값이 실제 변경 요청과 다릅니다.')


def _validated_edit(state):
    edit = state.get('node_edit')
    if not isinstance(edit, dict) or set(edit) != {'request', 'node_id', 'label', 'changes'}:
        raise ValueError('노드 변경 요청의 대상과 값을 확인해 주세요.')
    if (not isinstance(edit['request'], str) or len(edit['request']) > 4000
            or not isinstance(edit['node_id'], str) or not isinstance(edit['label'], str)
            or not isinstance(edit['changes'], list) or not 1 <= len(edit['changes']) <= 20):
        raise ValueError('노드 변경 요청의 대상과 값을 확인해 주세요.')
    users = [(i, m['content']) for i, m in enumerate(state.get('messages', [])) if m.get('role') == 'user']
    if not users or users[-1][1] != edit['request']:
        raise ValueError('표시된 노드 변경 요청과 전송된 메시지가 다릅니다.')
    failure = state.get('node_edit_failure') or {}
    retry = (failure.get('draft_digest') == _digest(state.get('draft') or {})
             if failure else False)
    correction = bool(state.get('node_edit_proof') and state.get('status') == 'validation_failed')
    if not (state.get('base_agent_id') or state.get('status') == 'ready' or retry or correction):
        raise ValueError('먼저 워크플로우 초안을 검증한 뒤 노드 설정을 수정해 주세요.')
    if state.get('pending_questions') or (state.get('issues') and not (retry or correction)) or (state.get('draft') or {}).get('questions'):
        raise ValueError('진행 중인 확인 질문을 해결한 뒤 노드 설정을 수정해 주세요.')
    _visible_changes(edit)
    return edit, users[-1][0]


def _change_value(node, change):
    path = change['path']
    old, new = change['old_value'], change['new_value']
    if type(old) not in (int, float, str, type(None)) or type(new) not in (int, float, str, type(None)):
        raise ValueError('노드 변경 값은 문자열 또는 숫자여야 합니다.')
    if (type(new) is str and len(new) > 200 or type(new) in (int, float)
            and (not math.isfinite(new) or len(str(new)) > 32)):
        raise ValueError('노드 변경 값의 길이 또는 숫자 범위를 확인해 주세요.')
    arg = _ARG.fullmatch(path)
    if arg:
        key = arg[1]
        if (key in {'__proto__', 'prototype', 'constructor'} or _SENSITIVE.search(key)
                or type(old) is type(None) or type(new) is not type(old)
                or type(old) is str and key not in _TEXT_ARGUMENTS):
            raise ValueError('이 인자는 노드 설정창에서 직접 변경할 수 없습니다: ' + path)
        try:
            arguments = json.loads(node['settings'].get('arguments_json') or '{}')
        except (ValueError, TypeError):
            raise ValueError('현재 노드의 인자 JSON을 읽을 수 없습니다.') from None
        if not isinstance(arguments, dict) or key not in arguments or not _same_value(arguments[key], old):
            raise ValueError('노드의 이전 값이 변경되었습니다. 설정창을 다시 열어 주세요: ' + path)
        arguments[key] = new
        node['settings']['arguments_json'] = json.dumps(arguments, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        return old != new
    field = _FILTER.fullmatch(path)
    if not field:
        raise ValueError('설정창에서 지원하지 않는 변경 경로입니다: ' + path)
    plan = node['settings'].get('execution_plan') or {}
    filters = plan.get('filters') or []
    index, key = int(field[1]), field[2]
    if index >= len(filters) or key not in filters[index]:
        raise ValueError('노드의 비교 조건이 변경되었습니다. 설정창을 다시 열어 주세요: ' + path)
    current = filters[index][key]
    if not _same_value(current, old):
        raise ValueError('노드의 이전 값이 변경되었습니다. 설정창을 다시 열어 주세요: ' + path)
    if key == 'field' and (type(new) is not str or not new.strip()):
        raise ValueError('조건 열 이름을 입력해 주세요.')
    if key == 'compare_field' and type(new) not in (str, type(None)):
        raise ValueError('비교할 열 이름을 확인해 주세요.')
    if key == 'value' and type(new) not in (int, float, str, type(None)):
        raise ValueError('비교 기준값을 확인해 주세요.')
    filters[index][key] = new
    return current != new


def _sync_count_label(node, changes):
    """Keep the edited node's count label honest without touching other nodes."""
    if len(changes) != 1:
        return
    change = changes[0]
    if (change['path'] not in ('arguments_json.max_results', 'arguments_json.limit', 'arguments_json.count')
            or type(change['old_value']) is not int or type(change['new_value']) is not int):
        return
    old = str(change['old_value'])
    pattern = re.compile(r'(?<!\d)' + re.escape(old) + r'(?=(?:건|개|통)(?!\w))')
    if len(pattern.findall(node['label'])) == 1:
        node['label'] = pattern.sub(str(change['new_value']), node['label'], count=1)


def _dependent_text(draft):
    top = [draft.get(key) or '' for key in ('name', 'purpose', 'instructions', 'explanation')]
    nodes = (draft.get('workflow') or {}).get('nodes') or []
    # The target may contain a second setting or label derived from the old value.
    settings = [node['label'] + '\n' + json.dumps(node['settings'], ensure_ascii=False, allow_nan=False)
                for node in nodes]
    return '\n'.join([*top, *settings])


def _sync_known_count_text(state, before, after, edit, source_turn):
    """Refresh generated Gmail→summary→Slack count prose only when its recipe is proven."""
    changes = edit['changes']
    if (len(changes) != 1 or changes[0]['path'] != 'arguments_json.max_results'
            or type(changes[0]['old_value']) is not int or type(changes[0]['new_value']) is not int):
        return False
    nodes = (before.get('workflow') or {}).get('nodes') or []
    if (len(nodes) != 3 or [node['tool'] for node in nodes] !=
            ['email.list', 'compose_answer', 'slack.post_message'] or nodes[0]['id'] != edit['node_id']):
        return False
    from .gmail_slack import gmail_slack_request
    base_turn = (state.get('node_edit_failure') or {}).get('base_turn', source_turn)
    prior = {**state, 'messages': state['messages'][:base_turn], 'base_agent_id': None,
             'resources': [], 'required_node_refs': []}
    recipe = gmail_slack_request(prior)
    summary = nodes[1]['settings'].get('instructions')
    old, new = changes[0]['old_value'], changes[0]['new_value']
    old_phrase, new_phrase = f'최대 {old}건', f'최대 {new}건'
    if (not recipe or recipe['count'] != old or summary != recipe['summary_instructions']
            or before.get('instructions') != before.get('explanation')
            or old_phrase not in before.get('instructions', '') or old_phrase not in summary):
        return False
    for key in ('name', 'purpose', 'instructions', 'explanation'):
        value = after.get(key)
        if isinstance(value, str):
            after[key] = value.replace(old_phrase, new_phrase)
    after['workflow']['nodes'][1]['settings']['instructions'] = summary.replace(old_phrase, new_phrase)
    return True


def _check_semantic_dependencies(before, after, edit, reconciled):
    """Do not approve a literal patch while a visible explanation still claims the old value."""
    text = _dependent_text(after)
    for change in edit['changes']:
        path, old = change['path'], change['old_value']
        if path == 'arguments_json.query':
            raise ValueError('검색 조건은 작업 범위와 다른 단계의 설명에 연결돼 있어 자동 변경하지 않았습니다. 대화에서 전체 조건을 수정해 주세요.')
        if type(old) is str and old and old in text:
            raise ValueError('다른 단계나 설명에 이전 설정값이 남아 있어 자동 변경하지 않았습니다: ' + path)
        if type(old) in (int, float):
            phrase = str(old)
            if path in ('arguments_json.max_results', 'arguments_json.limit', 'arguments_json.count'):
                if re.search(r'(?<!\d)' + re.escape(phrase) + r'\s*(?:건|개|통)(?!\w)', text):
                    raise ValueError('다른 단계나 설명에 이전 수량이 남아 있어 자동 변경하지 않았습니다: ' + path)
            elif re.search(r'(?<![\d.])' + re.escape(phrase) + r'(?![\d.])', text):
                raise ValueError('다른 단계나 설명에 이전 숫자가 남아 있어 자동 변경하지 않았습니다: ' + path)
    if reconciled:
        return '관련 설명과 요약 지시문의 수량 표기도 함께 맞췄습니다. '
    return ''


def proof_issue(state, draft=None):
    proof = state.get('node_edit_proof')
    if not isinstance(proof, dict):
        return '노드 변경 검증 기록이 없습니다.'
    users = [(i, m['content']) for i, m in enumerate(state.get('messages', [])) if m.get('role') == 'user']
    if not users or users[-1] != (proof.get('source_turn'), proof.get('request')):
        return '노드 변경 후 요청이 바뀌었습니다. 다시 검증해 주세요.'
    try:
        edit = proof['edit']
        _visible_changes(edit)
        baseline = deepcopy(proof['baseline_node'])
        targets = [node for node in (draft or state.get('draft') or {})['workflow']['nodes']
                   if node['id'] == edit['node_id']]
        if len(targets) != 1 or baseline['label'] != edit['label']:
            raise ValueError('변경 대상 노드가 다릅니다.')
        for item in edit['changes']:
            _change_value(baseline, item)
        _sync_count_label(baseline, edit['changes'])
        if baseline != targets[0] or _digest(draft or state.get('draft') or {}) != proof['result_digest']:
            raise ValueError('노드 변경 후 설정이 바뀌었습니다.')
    except (ValueError, KeyError, TypeError):
        return '노드 변경 후 설정 또는 검증 기록이 바뀌었습니다. 다시 검증해 주세요.'
    return None


def edit_coverage(state, draft):
    issue = proof_issue(state, draft)
    proof = state.get('node_edit_proof') or {}
    return {'enabled': True, 'issues': [issue] if issue else [], 'tasks': [
        {'requirement_id': 'node_edit', 'description': '사용자가 지정한 노드 설정 변경',
         'kind': 'node_edit', 'status': 'covered' if not issue else 'unverified',
         'node_ids': [proof.get('node_id')], 'expected': (proof.get('edit') or {}).get('changes', [])}]}


def apply_node_edit(state):
    """Return a bounded patch or a concrete error; never pass popup prose to an LLM."""
    try:
        edit, source_turn = _validated_edit(state)
        draft = deepcopy(state.get('draft') or {})
        AgentDraft.model_validate({k: v for k, v in draft.items() if k != 'node_snapshots'})
        nodes = (draft.get('workflow') or {}).get('nodes') or []
        targets = [node for node in nodes if node.get('id') == edit['node_id']]
        if len(targets) != 1 or targets[0].get('label') != edit['label']:
            raise ValueError('변경할 노드를 찾을 수 없습니다. 설정창을 다시 열어 주세요.')
        baseline_node = deepcopy(targets[0])
        seen = set()
        changed = False
        for item in edit['changes']:
            if not isinstance(item, dict) or set(item) != {'path', 'old_value', 'new_value'} or not isinstance(item['path'], str):
                raise ValueError('노드 변경 항목의 경로와 값을 확인해 주세요.')
            if item['path'] in seen:
                raise ValueError('같은 설정을 두 번 변경할 수 없습니다: ' + item['path'])
            seen.add(item['path'])
            changed = _change_value(targets[0], item) or changed
        if not changed:
            raise ValueError('변경된 설정 값이 없습니다.')
        _sync_count_label(targets[0], edit['changes'])
        reconciled = _sync_known_count_text(state, state['draft'], draft, edit, source_turn)
        note = _check_semantic_dependencies(state['draft'], draft, edit, reconciled)
        checked = AgentDraft.model_validate({k: v for k, v in draft.items() if k != 'node_snapshots'}).model_dump()
        issues = workflow_errors(checked['workflow'], checked['tool_ids'], state.get('resources', []), checked['execution_plan'])
        if issues:
            raise ValueError('변경한 설정을 실행할 수 없습니다: ' + ' / '.join(issues[:3]))
        summary = ', '.join(f"{item['path']}: {item['old_value']} → {item['new_value']}"
                            for item in edit['changes'] if item['old_value'] != item['new_value'])[:1200]
        reply = f"{edit['node_id']} 설정을 변경했습니다: {summary}. {note}다른 작업의 실행 설정은 유지했습니다. 저장 전에 변경된 값을 확인해 주세요."
        proof = {'request': edit['request'], 'source_turn': source_turn, 'node_id': edit['node_id'],
                 'edit': deepcopy(edit), 'baseline_node': baseline_node,
                 'result_digest': _digest(draft)}
        return {'draft': draft, 'node_edit': None, 'node_edit_proof': proof, 'node_edit_failure': None,
                'edit_route': 'validate',
                'messages': state.get('messages', []) + [{'role': 'assistant',
                    'content': reply}],
                'confirmed': False, 'saved_agent_id': None, 'repair_count': 2,
                'pending_questions': [], 'issues': [], 'semantic_issues': [],
                'capability_enforced': False, 'capability_assessment': None,
                'condition_enforced': False, 'condition_required': False,
                'requirement_required': True, 'requirement_ledger': None,
                'verification_enforced': False, 'verification_required': False,
                'trace': [*state.get('trace', []), {'node': 'node_edit', 'label': '노드 설정 직접 변경',
                    'status': 'completed', 'detail': edit['node_id'], 'model_calls': 0}]}
    except (ValueError, TypeError, KeyError) as error:
        message = str(error) or '노드 변경 요청을 확인할 수 없습니다.'
        marker = None
        if state.get('draft') and (state.get('status') == 'ready' or state.get('base_agent_id')
                                   or state.get('node_edit_failure')):
            previous = state.get('node_edit_failure') or {}
            last_user = max((i for i, message in enumerate(state.get('messages', []))
                             if message.get('role') == 'user'), default=0)
            try: marker = {'draft_digest': _digest(state['draft']),
                           'base_turn': previous.get('base_turn', last_user)}
            except (ValueError, TypeError, KeyError): pass
        return {'node_edit': None, 'node_edit_proof': None, 'node_edit_failure': marker,
                'edit_route': 'stop',
                'status': 'validation_failed', 'confirmed': False, 'issues': [message],
                'pending_questions': [], 'messages': state.get('messages', []) +
                    [{'role': 'assistant', 'content': message}],
                'trace': [*state.get('trace', []), {'node': 'node_edit', 'label': '노드 설정 변경 보류',
                    'status': 'needs_input', 'detail': message, 'model_calls': 0}]}
