"""Reject a known degenerate repair, without treating suggested IDs as a whitelist.

This checks only the loss of all processing. It cannot prove that a remaining
processing node implements every requested operation, condition or output.
"""
import re

from agent_builder.capabilities.capability_judgement import active_request_messages
from .requirement_coverage import coverage_enabled
from .request_meaning import sealed_meaning

IO_ONLY = frozenset({'read_table', 'read_document', 'write_result', 'export_result', 'finish'})
# These registered operations only retrieve mail. An operation-bearing source is
# still a source, and cannot stand in for the requested summary/answer processing.
GMAIL_READ_OPERATIONS = {'email.list': frozenset({'execute', 'read_messages'}),
                         'email.get': frozenset({'execute'})}
IO_OPERATIONS = {**GMAIL_READ_OPERATIONS,
                 'read_document': {'extract_content', 'native_read_document', 'read_text'},
                 'read_table': {'parse', 'native_read_table', 'read_csv'},
                 'export_result': {'native_write_result', 'native_export_result', 'write_json', 'write_csv', 'serialize'}}

# Older checkpoints may have no typed requirements. In that path accept only
# explicit action verbs, never a column name, filename or capability recommendation.
_ACTIONS = re.compile(
    r'(?P<summary>요약)|(?P<aggregate>집계|합산)|(?P<compute>계산)'
    r'|(?P<filter>선별|필터링)|(?P<sort>정렬)'
    r'|(?P<join>결합|병합)|(?P<extract>추출)|(?P<compare>비교|대조)')
_INFLECTED_ACTIONS = re.compile(r'(?P<compute>곱한|곱해|곱하|더한|더해|나눈|나누어)|(?P<filter>행만\s*(?:남겨|남기|골라|추려))')
_VERB = re.compile(r'\s*(?:해|하|합니|을\s*해|를\s*해|을\s*하|를\s*하)')
_NEGATED = re.compile(r'\s*(?:은|는|을|를|만)?\s*(?:(?:하)?지(?:는)?\s*(?:마|말|않)|없이|말고|취소|안\s*해)')
_LITERALS = re.compile(r'''"[^"\n]*"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|@?[\w.-]+\.(?:csv|json|xlsx|pdf|txt)\b''', re.I)


def _legacy_actions(state):
    actions = set()
    for message in active_request_messages(state):
        if message.get('role') != 'user':
            continue
        text = _LITERALS.sub(' ', message['content'])
        mentions = sorted([*_ACTIONS.finditer(text), *_INFLECTED_ACTIONS.finditer(text)], key=lambda m: m.start())
        for match in mentions:
            kind = match.lastgroup
            tail = text[match.end():]
            if _NEGATED.match(tail):
                actions.discard(kind)
            elif match.re is _INFLECTED_ACTIONS or _VERB.match(tail):
                actions.add(kind)
    return actions


_GROUP_SUM = re.compile(
    r'(?<![\w.-])(?P<group>[\w.-]+?)\s*별(?:로)?\s+'
    r'(?P<measure>[\w.-]+?)(?:의)?\s+(?:합계|총합)(?:를|로)?\s*(?:집계|합산)')
_CHANGE = re.compile(r'대신|바꿔|바꾸|변경|수정')


def validate_group_aggregations(plan, state):
    """Cross-check explicit group and measure columns independently of the suggested tools."""
    if plan['unresolved']:
        return
    tables = [set(asset.get('columns') or []) for asset in state.get('resources', [])
              if asset.get('kind') == 'table']
    expected = set()
    for message in active_request_messages(state):
        if message.get('role') != 'user':
            continue
        text = _LITERALS.sub(' ', message['content'])
        # Let the extractor resolve an elliptic correction instead of validating it
        # against a superseded column. A new full instruction is checked below.
        if _CHANGE.search(text) and re.search(r'별|집계|합산', text):
            expected.clear()
        if any(_NEGATED.match(text[m.end():]) for m in re.finditer('집계|합산', text)):
            expected.clear()
        for match in _GROUP_SUM.finditer(text):
            group, measure = match.group('group', 'measure')
            tail = text[match.end():]
            if (_VERB.match(tail) and not _NEGATED.match(tail)
                    and any({group, measure} <= columns for columns in tables)):
                expected.add((group, measure))
    actual = {(field, task['spec']['sum_field']) for task in plan['tasks']
              if task['spec']['kind'] == 'aggregate' and task['spec']['aggregation'] == 'sum'
              for field in task['spec']['fields']}
    for group, measure in sorted(expected - actual):
        raise ValueError(f'원문에서 요청한 {group}별 {measure} 합계 집계가 업무 목록에서 누락되거나 변경됐습니다.')


def _io_only(node):
    tool = node['tool']
    operation = node.get('settings', {}).get('operation')
    if operation:
        return operation in IO_OPERATIONS.get(tool, ())
    return tool in IO_ONLY


def processing_issues(state, draft):
    from agent_builder.capabilities.capability_gate import capability_enforced, blocking_issue

    workflow = draft.get('workflow')
    if not workflow or draft.get('questions') or draft.get('unsupported'):
        return []
    if not capability_enforced(state) or blocking_issue(state):
        return []
    # Both validation and save check the ledger's coverage, node settings and
    # data lineage. Only the legacy path needs an independent I/O-only guard.
    if coverage_enabled(state):
        return []
    nodes = workflow.get('nodes', [])
    if not nodes:
        return []
    meaning = sealed_meaning(state)
    if meaning:
        actions = {action for task in meaning['tasks'] for action in task['actions']}
        processing = actions - {'read', 'save'}
        requires_mail_summary = any(t['source'] == 'gmail' and 'summarize' in t['actions'] for t in meaning['tasks'])
    else:
        processing = _legacy_actions(state)
        requires_mail_summary = 'summary' in processing
    # The registered Gmail read -> grounded-answer contract has a distinct
    # processing stage. Another unrelated processor does not cover its absence.
    if (requires_mail_summary and
            any(n['tool'] in GMAIL_READ_OPERATIONS for n in nodes) and
            not any(n['tool']=='compose_answer' for n in nodes)):
        return ['사용자가 요청한 Gmail 요약 처리 단계가 빠졌습니다. 조회한 메일을 요약 단계에 연결하세요.']
    # An operation may have a different contract from the native tool sharing
    # its ID. Leave those nodes to operation validation and semantic review.
    if not processing or any(not _io_only(n) for n in nodes):
        return []
    return ['사용자가 요청한 처리 단계가 빠졌습니다. 읽기·저장 사이에 요청한 업무를 연결하세요.']
