"""Ask for a bounded connection correction using actual, typed source ports."""
from copy import deepcopy
import json

from workflow_engine.catalog.registry import node_ports
from workflow_engine.workflow import workflow_errors


def correction_contract(workflow):
    """Keep the graph, settings and valid bindings; offer only existing data.

    This deliberately handles linear graphs only. Branch availability still
    belongs to the full workflow validator, rather than guessed ancestry.
    """
    nodes = workflow['nodes']
    ids = [node['id'] for node in nodes]
    if len(set(ids)) != len(ids) or not ids or workflow['entry'] != ids[0]:
        return None
    expected = [{'source': a, 'target': b, 'branch': 'next'} for a, b in zip(ids, ids[1:])]
    if workflow['edges'] != expected:
        return None
    actual = {}; pending = []; properties = {}; by = {n['id']:n for n in nodes}
    for node in nodes:
        inputs, outputs = node_ports(node)
        valid = []; invalid = []
        for binding in node['bindings']:
            kind = actual.get(binding['node_id'], {}).get(binding['output'])
            if kind and binding['input'] in inputs and inputs[binding['input']] in (kind, 'json'):
                valid.append(binding)
            else:
                invalid.append(binding)
        if invalid:
            # Unknown sources and forward references need graph redesign, not
            # a connection spelling correction.
            sources = {b['node_id'] for b in node['bindings']}
            if not sources <= actual.keys():
                return None
            choices = {}
            for source in ids[:ids.index(node['id'])]:
                if source not in sources:
                    continue
                for output, kind in actual[source].items():
                    for port, expected_kind in inputs.items():
                        if expected_kind not in (kind, 'json'):
                            continue
                        if node['tool'] != 'ask_user' and port not in {b['input'] for b in node['bindings']}:
                            continue
                        choices['b'+str(len(choices)+1)] = {'input': port, 'node_id': source, 'output': output}
            # Extraction returns records, not documents. For a grounded answer,
            # the exact document input consumed by that extraction is a valid
            # source; an unrelated earlier reader is not. Do not bypass searches,
            # branches, operation variants or combine ambiguous document inputs.
            if node['tool'] == 'compose_answer':
                for binding in invalid:
                    source = by[binding['node_id']]
                    if (binding['input'] != 'documents' or source['tool'] != 'extract_information'
                            or source['settings'].get('operation')):
                        continue
                    origins = [b for b in source['bindings'] if b['input']=='documents'
                               and actual.get(b['node_id'],{}).get(b['output'])=='documents']
                    if len(origins) == 1:
                        choice = {**origins[0], 'input':'documents'}
                        if choice not in choices.values(): choices['b'+str(len(choices)+1)] = choice
            if not choices:
                return None
            keep = [key for key, value in choices.items() if value in valid]
            properties[node['id']] = {'type': 'array', 'items': {'type': 'string', 'enum': list(choices)},
                                      'minItems': max(1, len(keep)), 'maxItems': len(inputs)}
            pending.append({'node_id': node['id'], 'tool': node['tool'], 'choices': choices,
                            'keep': keep, 'invalid': invalid})
        if node['tool'] in ('ask_user', 'condition'):
            own = 'answer' if node['tool'] == 'ask_user' else 'decision'
            outputs = {key: kind for key, kind in outputs.items() if key == own or key in {b['input'] for b in valid}}
        actual[node['id']] = outputs
    if not pending:
        return None
    return {'schema': {'title': 'WorkflowConnections', 'type': 'object', 'properties': properties,
                       'required': list(properties), 'additionalProperties': False}, 'nodes': pending}


def correct_connections(workflow, resources, llm):
    contract = correction_contract(workflow)
    if not contract:
        return workflow, []
    response = llm.with_structured_output(contract['schema'], method='json_schema', strict=True).invoke([
        {'role': 'system', 'content': '워크플로우의 잘못된 데이터 연결만 수정합니다. 각 노드에 choices의 ID 목록을 반환하세요. '
         'keep의 정상 연결은 모두 유지합니다. 존재하지 않는 documents/text 같은 선택 입력을 억지로 채우지 마세요. '
         '같은 input을 두 번 연결하지 마세요. 도구·업무 조건·처리 순서는 바꾸지 않습니다.'},
        {'role': 'user', 'content': json.dumps(contract['nodes'], ensure_ascii=False)}])
    if hasattr(response, 'model_dump'):
        response = response.model_dump()
    if not isinstance(response, dict) or set(response) != set(contract['schema']['properties']):
        raise ValueError('연결 수정 응답의 노드가 요청 범위와 다릅니다.')
    result = deepcopy(workflow); by = {n['id']: n for n in result['nodes']}; changes = []
    for item in contract['nodes']:
        selected = response[item['node_id']]
        if (not isinstance(selected, list) or not selected or any(not isinstance(key, str) or key not in item['choices'] for key in selected)
                or len(selected) != len(set(selected)) or not set(item['keep']) <= set(selected)):
            raise ValueError('실제 출력과 정상 연결을 보존하는 수정만 허용합니다.')
        bindings = [item['choices'][key] for key in selected]
        if len({b['input'] for b in bindings}) != len(bindings):
            raise ValueError('입력이 중복 연결되었습니다.')
        node = by[item['node_id']]
        changes.append({'node_id': node['id'], 'before': deepcopy(node['bindings']), 'after': deepcopy(bindings),
                        'rule': 'model_selected_existing_typed_ports'})
        node['bindings'] = deepcopy(bindings)
    issues = workflow_errors(result, list(dict.fromkeys(n['tool'] for n in result['nodes'])), resources)
    if issues:
        raise ValueError(' / '.join(issues))
    return result, changes
