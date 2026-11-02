"""Explicit service hints, separate from the frozen 56-task SVM scores.

These rules add no execution authority and never turn task-family IDs into
executable aliases. Ambiguous contracts stay with the full-catalogue planner.
"""
import re
from ml.advice.request_context import instruction_text

TABLE_PROCESSORS = frozenset(('filter_sort', 'aggregate_table', 'calculate_columns'))
TABLE_TOOLS = TABLE_PROCESSORS | {'read_table', 'write_result'}
RECORD_OR_TEXT_PRODUCERS = frozenset(('extract_information', 'compose_answer', 'agent_node', 'text_statistics'))
DOCUMENT_TOOLS = frozenset(('read_document', 'search_document', 'extract_information', 'compose_answer'))
REQUIRES = {'filter_sort': 'read_table', 'aggregate_table': 'read_table',
            'calculate_columns': 'read_table', 'write_result': 'read_table',
            'search_document': 'read_document', 'extract_information': 'read_document',
            'compose_answer': 'read_document', 'reply_review': 'read_document'}
_SAVE = re.compile(r'(?:저장|내보내|다운로드)(?=\s|[.!?]|$|해|하|할|을|를|은|는|이|가|도|만|줘|주|고|후|기)|파일.{0,8}만들|(?<![A-Za-z0-9_])(?:csv|xlsx|pdf|docx|markdown|txt)(?![A-Za-z0-9_]).{0,12}만들|\b(?:export|save)\b', re.IGNORECASE)
_REVIEW_OBJECT = (r'(?:응대\s*(?:초안|문안|답변)|고객\s*(?:답변|응대)|'
                  r'상담원\s*(?:문안|답변)|답변\s*초안|회신\s*초안|답장)')
_REVIEW_ACTION = r'(?:(?:검수|검토|대조)(?:해|하)|(?:위반|어긋|맞).{0,30}(?:확인|알려|찾아))'
_REVIEW = re.compile(_REVIEW_OBJECT + r'.{0,80}' + _REVIEW_ACTION + r'|'
                     r'(?:검수|검토)(?:해|하).{0,40}' + _REVIEW_OBJECT)
_REPORT = re.compile(r'검수\s*결과.*(?:보고서|표로|표\s*정리)|(?:보고서|표로).*검수\s*결과')
_REPORT_SAVE = re.compile(r'(?:보고서|검수\s*결과\s*표).{0,40}(?:저장|내보내|다운로드)', re.IGNORECASE)


def align_candidates(query, context, candidates, registry):
    ids = {r['id'] for r in registry}
    kinds = set(context['resource_kinds'])
    rows = {c['tool_id']: dict(c) for c in candidates}
    changes = []
    intent = instruction_text(query)
    save_requested = bool(_SAVE.search(intent))

    def remove(tool, reason):
        if tool in rows:
            del rows[tool]
            changes.append({'tool_id': tool, 'action': 'withheld', 'reason': reason})

    def suggest(tool, reason):
        if tool in ids and tool not in rows:
            rows[tool] = {'tool_id': tool, 'source': 'service_contract', 'reason': reason}
            changes.append({'tool_id': tool, 'action': 'suggested', 'reason': reason})

    if _REPORT.search(intent):
        suggest('review_summary', 'explicit_review_report_request')
    if _REVIEW.search(intent):
        suggest('reply_review', 'explicit_reply_review_request')

    # Attachment capabilities constrain readers, not all downstream processors.
    # The catalogue can also supply intermediate values (review tables, review
    # answers, saved workflows). Plausible bindings stay with the planner.
    def produces(port, excluding=()):
        return any(t['id'] in rows and t['id'] not in excluding
                   and port in t.get('outputs', {}).values() for t in registry)

    if kinds == {'table'} and not produces('documents', {'read_document', 'search_document'}):
        for tool in sorted(DOCUMENT_TOOLS):
            if tool == 'compose_answer' and (produces('reviews') or produces('text', {'compose_answer'})):
                continue
            remove(tool, 'table_resource_has_no_documents_output')
    elif kinds == {'document'}:
        blocked = {'read_table'} if produces('table', TABLE_TOOLS) else TABLE_TOOLS
        for tool in sorted(blocked):
            remove(tool, 'document_resource_is_not_an_executable_table')

    # A dual-capability table is still a table producer. Resolve a known table
    # output even when its source also supports document reading. Mixed result
    # producers need the planner to choose bindings rather than a global alias.
    if ('table' in kinds and save_requested and set(rows) & (TABLE_PROCESSORS | {'read_table'})
            and not set(rows) & RECORD_OR_TEXT_PRODUCERS):
        remove('export_result', 'table_output_requires_write_result')
        suggest('write_result', 'table_processors_output_table_not_records_or_text')

    # No file-writing hint is inferred from the mere presence of an attachment.
    if not save_requested:
        remove('export_result', 'no_explicit_save_request')
        remove('write_result', 'no_explicit_save_request')

    # review_summary produces a table even when the original attachment was a
    # document. A reader for an original CSV is not its upstream producer.
    if 'review_summary' in rows and _REPORT_SAVE.search(intent):
        between = _REPORT_SAVE.search(intent).group()
        # A separate request to save source text/records needs a separate writer;
        # do not collapse mixed outputs based only on the word "report".
        if not re.search(r'원문|답변|초안|요약|각각|둘\s*다', between):
            remove('export_result', 'review_report_output_is_table')
            suggest('write_result', 'review_summary_outputs_table')
    for tool in list(rows):
        producer = REQUIRES.get(tool)
        if producer == 'read_table' and tool == 'write_result' and 'review_summary' in rows:
            continue
        if producer == 'read_table' and 'table' in kinds:
            suggest(producer, 'selected_tool_requires_table_input')
        elif producer == 'read_document' and 'document' in kinds:
            suggest(producer, 'selected_tool_requires_documents_input')
    # A records export cannot consume the original table. Without a known
    # producer of records/text, offer the full catalogue rather than a bad hint.
    if (kinds == {'table'} and 'export_result' in rows
            and not any(produces(port, {'export_result', 'finish'}) for port in ('records', 'text', 'documents'))
            and not set(rows) & {'reply_review', 'review_summary'}):
        remove('export_result', 'no_records_or_text_producer_identified')
    return list(rows.values()), changes
