"""Reject internal implementation questions for native local table workflows.

Do not invent answers or discard business questions. Rejected questions become
repair errors and cannot turn an incomplete draft into a saveable workflow.
"""
import re


def internal_table_question(question, state, draft):
    selected = set(draft.get('tool_ids', [])) | set((state.get('capability_assessment') or {}).get('tool_ids', []))
    local_tools = {'read_table', 'filter_sort', 'write_result', 'finish'}
    if not selected or selected - local_tools:
        return False
    users = '\n'.join(m['content'] for m in state.get('messages', []) if m.get('role') == 'user')
    if not (any(r.get('kind') == 'table' for r in state.get('resources', [])) or
            re.search(r'CSV|JSON|표\s*파일', users, re.I)):
        return False
    for resource in state.get('resources', []):
        for column in resource.get('columns', []):
            if re.search(r'(?<!\w)' + re.escape(column) + r'`?\s*(?:열|컬럼)', question, re.I):
                return False
    if re.search(r'(?:파일|자료)\s*(?:의\s*)?(?:실제\s*)?(?:자료\s*)?ID|file[_ ]?id', question, re.I):
        return True
    # Explicit custom destinations are not silently replaced with local reports.
    if re.search(r'폴더|경로|S3|드라이브|저장소|https?://', users, re.I):
        return False
    return bool(re.search(r'저장\s*(?:위치|경로|폴더)|(?:JSON|CSV).*폴더', question, re.I))
