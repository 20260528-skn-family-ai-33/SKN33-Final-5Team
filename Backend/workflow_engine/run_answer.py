"""Present run answers without serializing the file reference served by the download button."""
import json


def gmail_slack_summary_texts(config, outputs, texts):
    """Hide the delivery receipt from the displayed fixed Gmail-to-Slack summary.

    The typed Slack result remains in outputs and the execution steps.  Only the
    exact generated recipe qualifies, including copies saved before its input
    mode and summary format were updated.
    """
    from agent_builder.conditions.input_requirements import _legacy_fixed_gmail_slack_summary

    if not _legacy_fixed_gmail_slack_summary({**config, 'request_input_mode': None}):
        return texts
    _, summary, slack = config['workflow']['nodes']
    summary_text = outputs.get(summary['id'], {}).get('text')
    delivery = outputs.get(slack['id'], {})
    receipt = delivery.get('result')
    receipt_text = delivery.get('text')
    if (not isinstance(summary_text, str) or summary_text not in texts or
            not isinstance(receipt, dict) or receipt.get('status') != 'accepted_by_provider' or
            not isinstance(receipt_text, str) or receipt_text not in texts):
        return texts
    try:
        if json.loads(receipt_text) != receipt:
            return texts
    except ValueError:
        return texts
    return [text for text in texts if text != receipt_text]


def summarized_text_sources(workflow, outputs):
    nodes = {n['id']: n for n in workflow['nodes']}
    sources = {b['node_id'] for n in nodes.values()
               if n['tool'] == 'compose_answer' and n['id'] in outputs and not n['settings'].get('operation')
               for b in n['bindings'] if b['input'] == b['output'] == 'text'}
    pending = list(sources)
    while pending:
        node = nodes[pending.pop()]
        # Native ask_user forwards its preview unchanged after the answer.
        if node['tool'] != 'ask_user' or node['settings'].get('operation'):
            continue
        for binding in node['bindings']:
            source = binding['node_id']
            if binding['input'] == binding['output'] == 'text' and source not in sources:
                sources.add(source)
                pending.append(source)
    return sources


def empty_table_notice(workflow, outputs):
    """Describe the table actually bound to the final file, never an unrelated preview."""
    from workflow_engine.execution_path import artifact_output_ids, artifact_origin
    identifiers = artifact_output_ids(workflow, outputs)
    if not identifiers:
        return ''
    identifier = artifact_origin(workflow, identifiers[-1])
    node = next((n for n in workflow['nodes'] if n['id'] == identifier), None)
    if not node or node['tool'] not in ('write_result', 'export_result'):
        return ''
    if node['settings'].get('operation'):
        # Canonical CSV writers are used after column projection. Resolve the
        # actual writer argument; an unrelated empty binding proves nothing.
        from workflow_engine.catalog.canonical import parse_arguments
        from workflow_engine.executors.canonical_operation import substitute
        operation = node['settings']['operation']
        try:
            arguments = parse_arguments(node['settings'])
            if operation == 'write_csv':
                port = 'rows'
            elif operation == 'serialize' and arguments.get('format') == 'csv':
                port = 'value'
            else:
                return ''
            bound = {b['input']:outputs[b['node_id']][b['output']] for b in node['bindings']}
            rows = substitute(arguments[port], bound) if port in arguments else bound.get(port)
        except (KeyError, ValueError, TypeError):
            return ''
        return _empty_notice() if rows == [] else ''
    port = 'table' if node['tool'] == 'write_result' else 'records'
    binding = next((b for b in node['bindings'] if b['input'] == port), None)
    source = outputs.get(binding['node_id'], {}).get(binding['output']) if binding else None
    rows = source.get('rows') if isinstance(source, dict) and port == 'table' else source
    if rows != []:
        return ''
    return _empty_notice()


def _empty_notice():
    return ('결과가 0행입니다. 저장된 파일에는 데이터 행이 없습니다. '
            '실행 단계의 입력·출력 행 수와 적용된 필터·빈값 처리 조건을 확인하세요.')


def public_run_answer(answer, outputs):
    # Canonical operations expose their result as text as well as typed ports.
    # That text is needed by downstream nodes; change only the displayed answer.
    if not isinstance(answer, str) or '"path"' not in answer:
        return answer
    sections = answer.split('\n\n')
    replacements = {}
    for output in outputs.values():
        asset = output.get('asset')
        text = output.get('text')
        result = output.get('result')
        if (not output.get('artifact') or not isinstance(asset, dict) or 'path' not in asset
                or not isinstance(result, dict) or not isinstance(text, str) or text not in sections):
            continue
        if result != asset and result.get('artifact') != asset:
            continue
        try:
            # A document or an explicit answer can override the operation's default text port.
            if json.loads(text) != result:
                continue
        except ValueError:
            continue
        details = {key:value for key,value in asset.items() if key not in ('path','version','bytes')}
        if result == asset:
            visible = details
        else:
            visible = {key:value for key,value in result.items() if key != 'artifact'}
            if details:
                visible['artifact'] = details
        replacements[text] = json.dumps(visible, ensure_ascii=False, indent=2) if visible else None
    parts = (replacements.get(section, section) for section in sections)
    return '\n\n'.join(section for section in parts if section is not None)
