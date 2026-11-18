"""Shared, model-free row feasibility check for design and execution."""
import re

from .table_rows import native_records, label, row_collection_intent, _check_source_key, _join_source_chunks

NO_COORDINATES = ('전체 행을 확인할 표 셀 좌표가 없습니다. 표 인식이 가능한 파일을 연결하거나 '
                  '문서별 단일 값 추출로 요청을 바꿔 주세요. 현재 자료로는 전체 행 CSV/JSON을 저장할 수 없습니다.')


def native_rows(parts, fields, evidence_keys, max_rows):
    """Replicate only explicitly labelled, unique document facts beside native rows.

    An absent table column is not automatically a document fact. It must have a
    unique labelled value in a non-table block, with its own original evidence.
    """
    header_labels = {label(cell.get('text')) for part in parts for cell in part.get('table_cells', [])}
    row_fields = [field for field in fields if label(field) in header_labels]
    missing = [field for field in fields if field not in row_fields]
    if not row_fields or not missing:
        return native_records(parts, fields, evidence_keys, max_rows)
    facts = {}
    table_blocks = {(part['file_id'], part.get('block_id')) for part in parts if 'table_cells' in part}
    sources = {}
    for part in parts:
        if 'table_cells' in part or (part.get('block_id') is not None and
                                   (part['file_id'], part['block_id']) in table_blocks):
            continue
        sources.setdefault(_check_source_key(part), []).append(part)
    for field in missing:
        matches = {}
        pattern = re.compile(r'^\s*' + re.escape(field) + r'(?:\s*[:：]\s*(.*)|[ \t]+([^:：].*))$')
        for chunks in sources.values():
            ordered = sorted(chunks, key=lambda chunk: chunk.get('offset', 0))
            for line in source_lines(ordered):
                match = pattern.fullmatch(line)
                value = (match[1] if match and match[1] is not None else match[2] if match else '').strip()
                if value and value.strip(':：').strip():
                    proof_part = next((part for part in ordered if line in part['text']), None)
                    if proof_part is not None:
                        matches[value] = {**{key: proof_part[key] for key in evidence_keys if key in proof_part}, 'quote': line}
        if len(matches) != 1:
            # Preserve strict partial-header rejection; do not guess a value or scope.
            return native_records(parts, fields, evidence_keys, max_rows)
        facts[field] = next(iter(matches.items()))
    records = native_records(parts, row_fields, evidence_keys, max_rows)
    if records is None:
        return None
    for record in records:
        for field, (value, proof) in facts.items():
            record[field] = value
            record['evidence'][field] = proof
    return records


def scalar_selection(*requests):
    """Only an explicit first/last single item can bypass the repetition guard."""
    for request in reversed(requests):
        if not request:
            continue
        if row_collection_intent(request) is True:
            return None
        matches = list(re.finditer(r'(첫(?:\s*번째)?|마지막)\s*(?:품목|항목|행|값)(?:\s*(?:하나|한\s*개|1\s*개))?\s*만', request))
        if matches:
            if len(matches) != 1 or re.search(r'말고|제외|아니|않|취소|빼|빼고|빼줘|빼주세요', request):
                return None
            return -1 if matches[0][1] == '마지막' else 0
        if row_collection_intent(request) is False:
            return None
    return None


def source_lines(chunks):
    """Only contiguous source intervals establish complete original lines."""
    groups = []
    end = -1
    for chunk in chunks:
        start = chunk.get('offset', 0)
        if start > end:
            groups.append([])
        groups[-1].append(chunk)
        end = max(end, start + len(chunk['text']))
    for index, group in enumerate(groups):
        text = _join_source_chunks(group)
        if group[0].get('offset', 0) > 0:
            text = text.partition('\n')[2]
        if index + 1 < len(groups) and not text.endswith(('\n', '\r')):
            text = text.rpartition('\n')[0]
        yield from text.splitlines()
