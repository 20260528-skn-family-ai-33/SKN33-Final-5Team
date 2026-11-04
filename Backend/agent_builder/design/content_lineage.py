"""Trace the inputs supplying the exported data, excluding names and settings."""
from workflow_engine.catalog.canonical import parse_arguments
from workflow_engine.catalog.registry import uses_operation


def _sole_binding(value):
    # A JSON envelope may wrap the entire payload. Multiple fields could mix raw
    # rows with processed metadata, so that shape is not proof of content lineage.
    while isinstance(value, (dict, list)) and len(value) == 1:
        if isinstance(value, dict) and '$binding' in value:
            name = value['$binding']
            return name if isinstance(name, str) else None
        value = next(iter(value.values())) if isinstance(value, dict) else value[0]
    return None


def _operation_inputs(node):
    tool = node['tool']
    operation = node['settings'].get('operation')
    args = parse_arguments(node['settings'])
    if tool == 'create_spreadsheet':
        # A bound workbook is verified separately against its sheet contract.
        sheets = args.get('sheets')
        if not isinstance(sheets, list) or not all(isinstance(sheet,dict) for sheet in sheets):
            return set()
        data = [sheet.get('table') for sheet in sheets]
    else:
        if tool == 'export_result':
            ports = {'write_json': ('value',), 'serialize': ('value',), 'write_csv': ('rows',)}.get(operation, ())
        elif tool == 'json_transform':
            ports = ('text',) if operation == 'parse' else ('value',)
        elif tool == 'list_transform':
            ports = ('items',)
        elif tool == 'join_tables':
            ports = ('left', 'right')
        elif tool in ('filter_sort', 'calculate_columns', 'aggregate_table'):
            ports = ('rows', 'table')
        else:
            # An unknown operation's bound path, key or policy proves no data dependency.
            return set()
        # The executor substitutes explicit references, then fills omitted arguments
        # from bindings of the same name. A literal does not consume that binding.
        data = [args.get(port, {'$binding': port}) for port in ports]
    return {name for value in data if (name := _sole_binding(value)) is not None}


def _content_bindings(node, output):
    tool = node['tool']
    bindings = node.get('bindings', [])
    if tool in ('read_table', 'read_document'):
        return []  # A file locator is not the contents of the file.
    if uses_operation(node):
        try:
            inputs = _operation_inputs(node)
        except (ValueError, TypeError):
            return []  # Malformed arguments are reported by workflow validation.
    elif tool == 'export_result':
        supplied = {b['input'] for b in bindings}
        inputs = next(({p} for p in ('records', 'text', 'answer') if p in supplied), set())
    elif tool in ('ask_user', 'condition', 'finish', 'saved_workflow'):
        # A prompt answer or a saved child output cannot prove it forwards its inputs.
        inputs = {output} if tool != 'saved_workflow' and output not in ('answer', 'decision') else set()
    else:
        return bindings  # Native processing nodes take their data via input ports.
    return [b for b in bindings if b['input'] in inputs]


def content_ancestors(nodes, identifier):
    """Keep the source port while following pass-through nodes such as ask_user."""
    visited = set()
    pending = [(identifier, None)]
    ancestors = set()
    while pending:
        current, output = pending.pop()
        if (current, output) in visited or current not in nodes:
            continue
        visited.add((current, output))
        for binding in _content_bindings(nodes[current], output):
            parent = binding['node_id']
            ancestors.add(parent)
            pending.append((parent, binding['output']))
    return ancestors
