"""Shared, model-free row feasibility check for design and execution."""
import re

from documents.resources.document_resources import chunks_for_asset

from .table_rows import _check_source_key, label, native_header_labels, native_records, row_collection_intent

NO_COORDINATES = ('전체 행을 확인할 표 셀 좌표가 없습니다. 표 인식이 가능한 파일을 연결하거나 '
                  '문서별 단일 값 추출로 요청을 바꿔 주세요. 현재 자료로는 전체 행 CSV/JSON을 저장할 수 없습니다.')

_ITEM = r'(?<!\w)(첫(?:\s*번째)?|마지막)\s*(?:품목|항목|행|값)'
_SELECTION_REFERENCE = re.compile(_ITEM + r'|(?<!\w)선택(?:을|은|만)?(?!\w)')
_SINGLE_ITEM = re.compile(
    _ITEM + r'(?:\s*(?:하나|한\s*개|1\s*개))?\s*만'
    r'(?=$|\W|말고|빼|제외|취소|아니|않|뽑|추출|저장|내보내)')
_REJECT_SELECTION = re.compile(
    r'(?<!\w)(?:말고|빼(?:고|서|거나|\s*줘|\s*주세요)|제외(?:하고|해서|해\s*(?:줘|주세요)|할래(?:요)?)'
    r'|취소(?:하고|할래(?:요)?|하(?:겠|려)\w*|해(?:\s*(?:줘|주세요|달라\w*))?)|아니(?:고|야|에요|요)'
    r'|않(?:고|아요|습니다)|(?:안\s+|그만\s*)할래(?:요)?)(?!\w)|(?:^|\s)(?:취소|제외|빼|그만)[.!?\s]*$')


def native_rows(parts, fields, evidence_keys, max_rows, *, storage=None):
    """Replicate only explicitly labelled, unique document facts beside native rows.

    An absent table column is not automatically a document fact. It must have a
    unique labelled value in a non-table block, with its own original evidence.
    """
    header_labels = native_header_labels(parts)
    row_fields = [field for field in fields if label(field) in header_labels]
    missing = [field for field in fields if field not in row_fields]
    if not row_fields or not missing:
        return native_records(parts, fields, evidence_keys, max_rows)
    complete = complete_document_parts(parts, storage)
    facts = common_facts(complete, missing, evidence_keys) if complete else None
    if facts is None:
        # Preserve strict partial-header rejection; do not guess a value or scope.
        return native_records(parts, fields, evidence_keys, max_rows)
    records = native_records(parts, row_fields, evidence_keys, max_rows)
    if records is None:
        return None
    for record in records:
        for field, (value, proof) in facts.items():
            record[field] = value
            record['evidence'][field] = proof
    return records


def complete_document_parts(parts, storage):
    """Require all original chunks before establishing a file-wide fact."""
    source_ids = {part['source_id'] for part in parts}
    if len(source_ids) != len(parts) or len({part['file_id'] for part in parts}) != 1:
        return None
    if all(part.get('document_chunk_count') == len(parts) for part in parts):
        return parts
    if storage is None or any('document_chunk_count' in part for part in parts):
        return None
    # Checkpoints created before coverage metadata must match the original full
    # document. Recovery must never fill in a search result's omitted chunks.
    try:
        originals = list(chunks_for_asset(storage.asset(parts[0]['file_id'])))
    except ValueError:
        return None
    if len(originals) != len(parts):
        return None
    by_source = {part['source_id']: part for part in originals}
    if set(by_source) != source_ids:
        return None
    for part in parts:
        original = by_source[part['source_id']]
        if part != {key: value for key, value in original.items()
                    if key not in ('document_chunk_count', 'source_text_length')}:
            return None
    return originals


def common_facts(parts, fields, evidence_keys):
    facts = {}
    table_blocks = {(part['file_id'], part.get('block_id')) for part in parts if 'table_cells' in part}
    sources = {}
    for part in parts:
        if 'table_cells' in part or (part.get('block_id') is not None and
                                   (part['file_id'], part['block_id']) in table_blocks):
            continue
        sources.setdefault(_check_source_key(part), []).append(part)
    ordered_sources = []
    for chunks in sources.values():
        if any(type(chunk.get('offset')) is not int for chunk in chunks):
            return None
        ordered = sorted(chunks, key=lambda chunk: chunk['offset'])
        lines = source_lines(ordered)
        if lines is None:
            return None
        ordered_sources.append((ordered, lines))
    for field in fields:
        matches = {}
        pattern = re.compile(re.escape(field) + r'(?:\s*[:：]\s*(.*)|[ \t]+([^:：].*))')
        for ordered, lines in ordered_sources:
            for start, line in lines:
                match = pattern.fullmatch(line)
                value = (match[1] if match and match[1] is not None else match[2] if match else '').strip()
                if value and value.strip(':：').strip():
                    # A split line still participates in the uniqueness check even
                    # when no single original chunk can support its full quote.
                    if matches.get(value) is None:
                        matches[value] = line_proof(ordered, start, line, evidence_keys)
        if len(matches) != 1:
            return None
        value, proof = next(iter(matches.items()))
        if proof is None:
            return None
        facts[field] = value, proof
    return facts


def line_proof(chunks, start, line, evidence_keys):
    for chunk in chunks:
        relative = start - chunk['offset']
        if relative >= 0 and chunk['text'][relative:relative + len(line)] == line:
            return {**{key: chunk[key] for key in evidence_keys if key in chunk},
                    'quote': line, 'quote_offset': start}
    return None


def scalar_selection(*requests, reject_cancelled=False):
    """Only an explicit first/last single item can bypass the repetition guard."""
    for request in reversed(requests):
        if not request:
            continue
        intent = row_collection_intent(request)
        matches = list(_SINGLE_ITEM.finditer(request))
        if len(matches) > 1:
            return None
        if matches and _REJECT_SELECTION.match(request[matches[0].end():].lstrip(' \t\r\n,，')):
            cancelled = True
        elif reference := _SELECTION_REFERENCE.search(request):
            cancelled = not matches and bool(_REJECT_SELECTION.search(request[reference.end():]))
        else:
            cancelled = bool(_REJECT_SELECTION.fullmatch(request.strip(' .!?')))
        if cancelled:
            if reject_cancelled:
                raise ValueError('저장된 첫·마지막 품목 선택을 취소했습니다. 설정에서 추출 범위를 다시 지정해 주세요.')
            return None
        if intent is True:
            return None
        if matches:
            return -1 if matches[0][1] == '마지막' else 0
        if intent is False:
            return None
    return None


def source_lines(chunks):
    """Return positioned original lines only when the block's full text is available."""
    source_length = chunks[0].get('source_text_length')
    if type(source_length) is not int:
        return None
    text = ''
    for chunk in chunks:
        start = chunk['offset']
        if not 0 <= start <= len(text) or chunk.get('source_text_length') != source_length:
            return None
        excerpt = chunk['text']
        overlap = min(len(text) - start, len(excerpt))
        if text[start:start + overlap] != excerpt[:overlap]:
            return None
        text += excerpt[overlap:]
    if len(text) != source_length:
        return None
    offset = 0
    lines = []
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        if line:
            lines.append((offset + len(raw_line) - len(raw_line.lstrip()), line))
        offset += len(raw_line)
    return lines
