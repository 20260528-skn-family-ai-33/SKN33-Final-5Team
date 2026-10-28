"""Describe input files and preflight the unconditional execution prefix.

Branches and table inputs produced by subgraphs still receive runtime checks.
This does not execute tools, query a model, or prove output correctness.
"""
from .condition_readiness import input_issues


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


def execution_requirements(config, storage, file_ids=None, dependencies=None, path='', depth=0):
    workflow = config.get('workflow')
    if not workflow or depth > 10:
        return []
    prefix = _prefix(workflow)
    prefix_ids = {n['id'] for n in prefix}
    provided = [storage.asset(id) for id in file_ids or []]
    result = []
    for node in workflow['nodes']:
        if node['tool'] == 'saved_workflow' and node['id'] in prefix_ids:
            child = (dependencies or {}).get(node['id'])
            # A table binding is supplied by the parent graph, not a new file.
            if child and not any(b['input'] == 'table' for b in node['bindings']):
                result += execution_requirements(child['config'], storage, file_ids,
                    child.get('workflow_dependencies'), path + node['id'] + '/', depth + 1)
        if node['tool'] not in ('read_table', 'read_document'):
            continue
        kind = 'table' if node['tool'] == 'read_table' else 'document'
        selected = [a for a in provided if a['kind'] == kind]
        if not selected:
            selected = [storage.asset(id) for id in node['settings'].get('file_ids') or config.get('document_ids', [])]
            selected = [a for a in selected if a['kind'] == kind]
        fields = _fields(prefix, node['id'], config) if kind == 'table' else []
        issues = []
        if not selected:
            issues.append('실행할 표 파일을 연결하세요.' if kind == 'table' else '실행할 문서를 연결하세요.')
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
