"""Prove ordered output columns through the actual data ports of a known path.

A projection establishes record key order. Only understood downstream operations
can preserve that proof; a matching projection somewhere upstream is insufficient.
Unknown transforms, literal replacement data and unresolved branches fail closed.
"""
from typing import NamedTuple

from workflow_engine.catalog.canonical import parse_arguments
from workflow_engine.catalog.registry import node_ports, uses_operation
from workflow_engine.execution_path import artifact_origin, artifact_output_ids, execution_path


def valid_columns(value, maximum=40):
    return (isinstance(value, list) and bool(value) and (maximum is None or len(value) <= maximum)
            and all(isinstance(c, str) and c.strip() for c in value)
            and len(set(value)) == len(value))


class Proof(NamedTuple):
    kind: str
    columns: tuple | None
    preserves_rows: bool


def final_output_proof(draft, resources):
    workflow = draft['workflow']
    if any(edge.get('branch') in ('yes', 'no') for edge in workflow['edges']):
        return None
    path = execution_path(workflow)
    nodes = {node['id']: node for node in path}
    outputs = artifact_output_ids(workflow)
    if not path:
        return None
    end = artifact_origin(workflow, outputs[-1]) if outputs else path[-1]['id']
    cache, visiting = {}, set()

    def bound(node, name):
        bindings = [b for b in node['bindings'] if b['input'] == name]
        if len(bindings) != 1:
            return None
        binding = bindings[0]
        return prove(binding['node_id'], binding['output'])

    def argument(node, args, name):
        value = args.get(name, {'$binding': name})
        if isinstance(value, dict) and name == 'table' and set(value) == {'rows', 'columns'}:
            rows = value.get('rows')
            if isinstance(rows, dict) and set(rows) == {'$binding'}:
                source = bound(node, rows['$binding'])
                if (source and source.kind == 'records' and source.columns is not None
                        and value['columns'] == list(source.columns)):
                    return Proof('table', source.columns, source.preserves_rows)
        if not isinstance(value, dict) or set(value) != {'$binding'}:
            return None
        return bound(node, value['$binding'])

    def prove(identifier, port):
        key = identifier, port
        if key in cache:
            return cache[key]
        if key in visiting or identifier not in nodes:
            return None
        visiting.add(key)
        node = nodes[identifier]
        tool, settings = node['tool'], node['settings']
        result = None
        if not uses_operation(node):
            if tool == 'read_table' and port == 'table':
                tables = {r['id']: r for r in resources if r.get('kind') == 'table'}
                ids = settings.get('file_ids') or [i for i in draft.get('document_ids', []) if i in tables]
                columns = tables.get(ids[0], {}).get('columns') if len(ids) == 1 else None
                result = Proof('table', tuple(columns) if valid_columns(columns, maximum=None) else None, True)
            elif tool == 'write_result' and port == 'artifact':
                source = bound(node, 'table')
                if source and source.kind == 'table':
                    result = source
            elif tool == 'export_result' and port == 'artifact' and settings.get('format') == 'json':
                # Native export consumes records before a confirmation answer;
                # text-only exports cannot prove a record column contract.
                # CSV infers headers from rows and loses them for empty results.
                source = bound(node, 'records')
                if source and source.kind == 'records':
                    result = source
            elif tool in ('filter_sort', 'calculate_columns') and port == 'table':
                source = bound(node, 'table')
                if source and source.kind == 'table':
                    columns = source.columns
                    operation = settings.get('table_operation')
                    if tool == 'filter_sort' or operation == 'parse_dates':
                        result = Proof('table', columns, False)
                    elif columns is not None and operation == 'compute':
                        output = settings.get('output_column')
                        if isinstance(output, str) and output.strip() and output not in columns:
                            result = Proof('table', (*columns, output), False)
                    elif columns is not None and operation == 'rename':
                        old, new = settings.get('rename_from'), settings.get('rename_to')
                        if old in columns and isinstance(new, str) and new.strip() and (new == old or new not in columns):
                            result = Proof('table', tuple(new if c == old else c for c in columns), False)
            elif tool == 'ask_user' and port == 'records':
                result = bound(node, 'records')
        else:
            args = parse_arguments(settings)
            operation = settings.get('operation')
            if tool == 'json_transform' and operation == 'lookup':
                source = argument(node, args, 'value')
                if (source and source.kind == 'table' and args.get('path') == ['rows']
                        and port in ('result', settings.get('output_port'))):
                    result = Proof('records', source.columns, source.preserves_rows)
                elif args.get('path') == [] and port in ('result', settings.get('output_port')):
                    result = argument(node, {'table': args.get('value')}, 'table')
            elif tool == 'filter_sort' and port in ('records', 'result'):
                source = argument(node, args, 'rows')
                if source and source.kind == 'records':
                    if operation == 'select_columns' and valid_columns(args.get('columns'), maximum=None):
                        # Runtime constructs each record in this exact order.
                        columns = tuple(args['columns'])
                        if source.columns is None or set(columns) <= set(source.columns):
                            result = Proof('records', columns, source.preserves_rows)
                    elif operation in ('filter', 'sort'):
                        result = Proof('records', source.columns, False)
            elif tool == 'calculate_columns' and operation == 'calculate_records' and port in ('records', 'result'):
                source = argument(node, args, 'rows')
                output = args.get('output_column')
                if source and source.kind == 'records' and source.columns is not None and isinstance(output, str) and output.strip():
                    if output not in source.columns:
                        result = Proof('records', (*source.columns, output), False)
                    elif args.get('overwrite') is True:
                        result = Proof('records', source.columns, False)
            elif tool == 'calculate_columns' and operation == 'rename_columns' and port == 'table':
                source = argument(node, args, 'table')
                mapping = args.get('mapping')
                if source and source.kind == 'table' and source.columns is not None and isinstance(mapping, dict):
                    columns = [mapping.get(c, c) for c in source.columns]
                    if not set(mapping) - set(source.columns) and valid_columns(columns, maximum=None):
                        result = Proof('table', tuple(columns), False)
            elif tool == 'join_tables' and operation in ('join', 'join_records'):
                kind = 'table' if operation == 'join' else 'records'
                left, right = argument(node, args, 'left'), argument(node, args, 'right')
                if port == kind and left and right and left.kind == right.kind == kind:
                    # Right-side columns depend on whether right has rows.
                    # A later projection can still establish an exact schema.
                    result = Proof(kind, None, False)
            elif tool == 'export_result' and port == 'artifact':
                name = 'rows' if operation == 'write_csv' else 'value'
                source = argument(node, args, name)
                if source and source.kind == 'records':
                    if operation == 'write_json' or operation == 'serialize' and args.get('format') == 'json':
                        result = source
                    elif operation == 'write_csv' and valid_columns(args.get('columns')):
                        columns = tuple(args['columns'])
                        if source.columns is not None and set(columns) == set(source.columns):
                            result = Proof('records', columns, source.preserves_rows)
        visiting.remove(key)
        cache[key] = result
        return result

    if outputs:
        return prove(end, 'artifact')
    ports = node_ports(nodes[end])[1]
    results = [prove(end, port) for port in ('table', 'records') if port in ports]
    return results[0] if len(results) == 1 else None
