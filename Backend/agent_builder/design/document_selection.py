"""Validate a preview selection by source identity, independently of display labels."""
import json
from documents.resources.file_inspection import inspect_file


def document_selection(value, resources):
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict) or set(payload) != {'document_selection'}:
            raise ValueError
        selected = payload['document_selection']
        if not isinstance(selected, dict) or set(selected) != {'file_id', 'block_id', 'fields'}:
            raise ValueError
        resource = next(resource for resource in resources
                        if resource['id'] == selected['file_id'] and resource.get('kind') == 'document')
        table = next(table for table in inspect_file(resource)['tables']
                     if table.get('block_id') == selected['block_id'])
        fields = selected['fields']
        if (not isinstance(fields, list) or not 1 <= len(fields) <= 40
                or any(not isinstance(field, str) or field not in table['columns'] for field in fields)
                or len(set(fields)) != len(fields)):
            raise ValueError
        return resource['id'], table['block_id'], fields
    except (ValueError, TypeError, KeyError, StopIteration):
        raise ValueError('선택한 원본 문서와 표의 열을 다시 확인해주세요.') from None
