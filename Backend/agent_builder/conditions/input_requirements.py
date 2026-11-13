"""Describe input files and preflight the unconditional execution prefix.

Branches and table inputs produced by subgraphs still receive runtime checks.
This does not execute tools, query a model, or prove output correctness.
"""
import json
import re

from agent_builder.conditions.condition_readiness import input_issues
from workflow_engine.executors.sources import source_file_ids
from workflow_engine.catalog.registry import uses_operation
from documents.resources.document_resources import RemovedUpload


def _legacy_fixed_gmail_slack_summary(config):
    """Recognize only the previously saved, three-step fixed Gmail-to-Slack recipe."""
    purpose = config.get('purpose')
    if (config.get('request_input_mode') is not None or config.get('document_ids') or
            config.get('tool_ids') != ['email.list', 'compose_answer', 'slack.post_message'] or
            not (config.get('instructions') or '').strip() or
            not isinstance(purpose, str) or '요약' not in purpose or
            not re.search(r'Gmail|지메일', purpose, re.I) or
            not re.search(r'Slack|슬랙', purpose, re.I)):
        return False
    workflow = config.get('workflow')
    if not isinstance(workflow, dict):
        return False
    nodes = workflow.get('nodes')
    if not isinstance(nodes, list) or len(nodes) != 3:
        return False
    mail, summary, slack = nodes
    try:
        if ([node['tool'] for node in nodes] != config['tool_ids'] or
                workflow.get('entry') != mail['id'] or
                workflow.get('edges') != [
                    {'source': mail['id'], 'target': summary['id'], 'branch': 'next'},
                    {'source': summary['id'], 'target': slack['id'], 'branch': 'next'}] or
                mail['settings'].get('operation') != 'read_messages' or
                summary.get('bindings') != [{'input': 'documents', 'node_id': mail['id'], 'output': 'documents'}] or
                not (summary['settings'].get('instructions') or '').strip() or
                slack['settings'].get('operation') != 'execute' or
                slack.get('bindings') != [{'input': 'text', 'node_id': summary['id'], 'output': 'text'}]):
            return False
        mail_args = json.loads(mail['settings'].get('arguments_json') or '{}')
        slack_args = json.loads(slack['settings'].get('arguments_json') or '{}')
        from agent_builder.design.gmail_summary import MAX_RESULTS
        query = mail_args.get('query') if isinstance(mail_args, dict) else None
        if not isinstance(mail_args, dict) or type(mail_args.get('max_results')) is not int:
            return False
        count = mail_args['max_results']
        unread = bool(re.search(r'(?:^|\s)is:unread(?:\s|$)', query or '', re.I)) if isinstance(query, str) else False
        expected_summary = (f'조회한 {"안 읽은 " if unread else ""}Gmail 메일 최대 {count}건을 사용자 요청에 맞춰 요약하세요: {purpose} '
                            '메일이 없으면 없다고 안내하세요. Slack 전송은 다음 단계가 처리하므로 요약 텍스트만 작성하세요.')
        return (set(mail_args) <= {'max_results', 'query'} and
                1 <= count <= MAX_RESULTS and
                (query is None or isinstance(query, str) and bool(query.strip())) and
                summary['settings']['instructions'] == expected_summary and
                isinstance(slack_args, dict) and
                set(slack_args) == {'channel_id'} and isinstance(slack_args['channel_id'], str) and
                re.fullmatch(r'[CGD][A-Z0-9]{8,24}', slack_args['channel_id']) is not None)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _request_inputs(config, dependencies, supplied_prompt, depth=0):
    """Return (has a file reader, consumes the initial user request), including pinned children."""
    if config.get('request_input_mode') == 'required' and not supplied_prompt:
        return False, True
    workflow = config.get('workflow')
    if not workflow:
        table_plan = 'read_table' in config.get('tool_ids', []) and bool(config.get('execution_plan'))
        return table_plan, not table_plan
    saved_instructions = (config.get('request_input_mode') == 'saved_instructions' or
                          _legacy_fixed_gmail_slack_summary(config))
    file_reader = False
    for node in workflow['nodes']:
        tool, settings = node['tool'], node['settings']
        if uses_operation(node):
            # Operations consume the arguments and upstream bindings saved in the graph.
            continue
        if tool in ('read_table', 'read_document'):
            file_reader = True
        elif tool == 'saved_workflow':
            child = dependencies.get(node['id'])
            if not child or depth >= 10:
                return file_reader, True
            child_prompt = any(b['input'] == 'text' for b in node.get('bindings', [])) or (
                supplied_prompt and child['config'].get('request_input_mode') == 'saved_instructions')
            files, needs_text = _request_inputs(child['config'], child.get('workflow_dependencies', {}), child_prompt, depth + 1)
            if needs_text:
                return file_reader, True
            file_reader |= files
        elif tool == 'condition' and settings.get('condition_operator') == 'semantic' and not any(b['input']=='answer' for b in node.get('bindings', [])):
            return file_reader, True
        elif tool == 'reply_review' and not any(b['input'] == 'table' for b in node.get('bindings', [])):
            return file_reader, True  # A customer's message cannot come from saved schedule instructions.
        elif tool == 'search_document' and not supplied_prompt and not (settings.get('query') or '').strip():
            return file_reader, True
        elif tool in ('agent_node', 'compose_answer') and not supplied_prompt and not (
                saved_instructions and
                (settings.get('instructions') or config.get('instructions') or '').strip()):
            return file_reader, True
        # ask_user collects its own answer after the workflow starts.
    return file_reader, False


def execution_request_required(config, dependencies=None):
    scheduled_prompt = bool(config.get('schedule') and config.get('request_input_mode') != 'required'
                            and (config.get('instructions') or '').strip())
    file_reader, needs_text = _request_inputs(config, dependencies or {}, scheduled_prompt)
    return needs_text or not (file_reader or scheduled_prompt or (config.get('instructions') or '').strip())


def is_launch_request(request):
    """Shared distinction between starting a run and supplying business criteria."""
    import re
    return bool(re.fullmatch(r'\s*(?:(?:지금|다시)\s*)?(?:실행|시작)(?:해\s*(?:줘|주세요))?[.!\s]*|\s*(?:run|start)\s*', request, re.I))


def table_run_request_notice(config, request, dependencies=None):
    """A fixed table plan cannot silently interpret a new filter or formula."""
    if not request.strip():
        return ''
    _, consumes_request = _request_inputs(config, dependencies or {}, False)
    # Document/AI workflows keep their existing free-text input contract.
    def has_table(current, children, depth=0):
        if depth > 10:
            return False
        workflow = current.get('workflow')
        if not workflow:
            return 'read_table' in current.get('tool_ids', [])
        return any(n['tool'] == 'read_table' or
                   n['tool'] == 'saved_workflow' and n['id'] in children and
                   has_table(children[n['id']]['config'], children[n['id']].get('workflow_dependencies', {}), depth + 1)
                   for n in workflow['nodes'])
    if consumes_request or not has_table(config, dependencies or {}):
        return ''
    # Preserve legacy callers which pass only a launch command, not business criteria.
    if is_launch_request(request):
        return ''
    return ('이 표 워크플로우에는 저장된 조건이 적용됐으며, 추가 요청의 필터·계산·정렬은 적용되지 않았습니다. '
                     '필터·계산·정렬을 바꾸려면 설정 수정에서 변경하고 저장하세요. '
                     '기존 조건으로 실행하려면 추가 요청을 비워주세요.')


def _prefix(workflow):
    by = {n['id']: n for n in workflow['nodes']}
    result, seen, current = [], set(), workflow['entry']
    while current in by and current not in seen:
        seen.add(current)
        node = by[current]
        result.append(node)
        if node['tool'] == 'condition':
            break
        edges = [e for e in workflow['edges'] if e['source'] == current]
        if len(edges) != 1:
            break
        current = edges[0]['target']
    return result


def _fields(nodes, reader_id, config):
    origins, fields = {reader_id}, []
    for _ in nodes:
        for node in nodes:
            if not any(b['input'] == 'table' and b['node_id'] in origins for b in node['bindings']):
                continue
            settings = node['settings']
            if node['tool'] == 'filter_sort':
                plan = settings.get('execution_plan') or config.get('execution_plan') or {}
                fields += [f['field'] for f in plan.get('filters', [])]
                fields += [f['compare_field'] for f in plan.get('filters', []) if f.get('compare_field')]
                fields += [s['field'] for s in plan.get('sort', [])]
                fields += [plan['duplicate_field']] if plan.get('duplicate_field') else []
                origins.add(node['id'])
            elif node['tool'] in ('aggregate_table', 'reply_review'):
                fields += settings.get('fields', [])
                if settings.get('aggregation') == 'sum' and settings.get('sum_field'):
                    fields.append(settings['sum_field'])
    return list(dict.fromkeys(fields))


def execution_requirements(config, storage, file_ids=None, dependencies=None, path='', depth=0, _external_ports=()):
    workflow = config.get('workflow')
    if not workflow and not execution_request_required(config, dependencies):
        # Saved table plans from before the graph builder still run through the legacy table reader.
        issues = []
        try:
            selected = [storage.asset(i) for i in file_ids or config.get('document_ids', [])]
        except RemovedUpload as error:
            selected = []
            issues.append(str(error))
        tables = [asset['id'] for asset in selected if asset.get('kind') == 'table']
        ready = bool(tables) and (not file_ids or len(tables) == len(selected))
        return [dict(node_id=path+'read_table', kind='table', timing='execution', required_before_start=True,
                     fields=[], file_ids=tables, ready=ready,
                     issues=[] if ready else issues or ['이번 실행에 사용할 표 파일을 연결하세요.'])]
    if not workflow or depth > 10:
        return []
    prefix = _prefix(workflow)
    prefix_ids = {n['id'] for n in prefix}
    result = []
    for node in workflow['nodes']:
        if node['tool'] == 'saved_workflow' and node['id'] in prefix_ids:
            child = (dependencies or {}).get(node['id'])
            if child:
                result += execution_requirements(child['config'], storage, file_ids,
                    child.get('workflow_dependencies'), path + node['id'] + '/', depth + 1,
                    _external_ports={b['input'] for b in node['bindings']})
        if node['tool'] not in ('read_table', 'read_document'):
            continue
        if node['settings'].get('operation'):
            # Raw-file operations resolve their declared asset/path arguments;
            # native parsed-table/document selection does not apply to them.
            continue
        source_port = 'table' if node['tool'] == 'read_table' else 'documents'
        if source_port in _external_ports:
            # The runner supplies this source directly from the parent graph.
            # Other child sources still need their own execution-file checks.
            continue
        kind = 'table' if node['tool'] == 'read_table' else 'document'
        fields = _fields(prefix, node['id'], config) if kind == 'table' else []
        issues = []
        try:
            selected = [storage.asset(id) for id in source_file_ids(node,file_ids or [],storage,config)]
        except ValueError as error:
            selected = []
            issues.append(str(error))
        if not selected:
            if not issues:issues.append('실행할 표 파일을 연결하세요.' if kind == 'table' else '실행할 문서를 연결하세요.')
        elif kind == 'table' and len(selected) != 1:
            issues.append('표 파일을 하나만 선택하세요.')
        else:
            issues += input_issues({'resources': selected})
            if kind == 'table':
                missing = [f for f in fields if f not in selected[0].get('columns', [])]
                if missing:
                    issues.append('필요한 열이 없습니다: ' + '·'.join(missing))
        result.append({'node_id': path + node['id'], 'kind': kind, 'timing': 'execution',
                       'required_before_start': node['id'] in prefix_ids,
                       'fields': fields, 'file_ids': [a['id'] for a in selected],
                       'ready': not issues, 'issues': issues,
                       'alternatives': ['자료 연결', '열 이름·업무 기준을 먼저 정하고 실행 때 파일 연결']})
    return result


def preflight_execution(config, storage, file_ids=None, dependencies=None):
    requirements = execution_requirements(config, storage, file_ids, dependencies)
    issues = list(dict.fromkeys(issue for r in requirements if r['required_before_start'] for issue in r['issues']))
    if issues:
        fields = list(dict.fromkeys(f for r in requirements if r['required_before_start'] for f in r['fields']))
        raise ValueError('실행 전 자료 확인: ' + ' / '.join(issues) +
                         (' 필요한 열: ' + '·'.join(fields) if fields else '') + ' 자료를 연결한 뒤 다시 실행하세요.')
    return requirements


def design_requirements(state):
    """Only known contract inputs; typed schema is not registered row data."""
    ledger = state.get('condition_ledger') or {}
    if not ledger.get('operations'):
        return []
    fields = [v['value'] for k, v in ledger.get('slots', {}).items()
              if k.endswith('.field') and v['status'] == 'specified']
    tables = [r for r in state.get('resources', []) if r.get('kind') == 'table']
    return [{'kind': 'table', 'timing': 'design' if 'schedule' in ledger['operations'] else 'execution',
             'fields': list(dict.fromkeys(fields)), 'ready': bool(tables),
             'description': ('예약할 관리 표를 지금 연결하세요.' if 'schedule' in ledger['operations'] else
                             '열 이름·처리 기준으로 먼저 설계하고, 실제 표 파일은 실행 전에 연결할 수 있습니다.'),
             'alternatives': ['지금 자료 연결', '열 이름·기준 직접 입력', '실행 전에 파일 연결']}]
