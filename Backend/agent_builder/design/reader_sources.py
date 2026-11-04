"""Identify which attached files a workflow reader opens."""
from documents.resources.document_resources import supports
from workflow_engine.catalog.canonical import parse_arguments


FILE_ARGUMENT = {
    ('read_table', 'read_csv'): 'path',
    ('read_table', 'parse'): 'source',
    ('read_document', 'read_text'): 'path',
    ('read_document', 'extract_content'): 'asset',
}


def reader_sources(node, resources, document_ids):
    tool, settings = node['tool'], node['settings']
    operation = settings.get('operation')
    if operation:
        key = FILE_ARGUMENT.get((tool, operation))
        locator = parse_arguments(settings).get(key)
        # resolve_assets rewrites the path only for the locator's own asset_id.
        # Metadata, path strings and values returned by earlier nodes do not
        # establish which uploaded file the operation will open.
        asset_id = locator.get('asset_id') if isinstance(locator, dict) else None
        return {asset_id} if isinstance(asset_id, str) and asset_id else set()

    kind = 'table' if tool == 'read_table' else 'document'
    compatible = {r['id'] for r in resources if supports(r, kind)}
    selected = set(settings.get('file_ids') or document_ids) & compatible
    return set() if kind == 'table' and len(selected) != 1 else selected
