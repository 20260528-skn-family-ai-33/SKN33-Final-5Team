"""Conservative OCR reconciliation; geometry is learned only from exact unique anchors.

All boxes belong to the same OCR crop. No Docling point/pixel assumption, fuzzy
text matching, arithmetic repair, or inference of blank/merged cells is allowed.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
from .contracts import AnalysisError


def normalized(value):
    return re.sub(r'\s+', '', str(value or ''))


def position(cell):
    return (cell.get('row', cell.get('start_row_offset_idx')),
            cell.get('column', cell.get('start_col_offset_idx')))


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_prediction(prediction):
    """Validate optional engine evidence before persisting any of it."""
    if not isinstance(prediction, dict):
        raise AnalysisError('invalid_engine_result', '표 OCR 결과가 객체가 아닙니다.')
    arrays = [prediction.get(k) for k in ('rec_texts', 'rec_boxes', 'rec_scores')]
    if not all(isinstance(v, list) for v in arrays) or len({len(v) for v in arrays}) != 1:
        raise AnalysisError('invalid_engine_result', '표 OCR 배열 형식 또는 길이가 올바르지 않습니다.')
    texts, boxes, scores = arrays
    if len(texts) > 2000:
        raise AnalysisError('quality_input_limit', '표 OCR 검증 입력 제한을 넘었습니다.')
    for text, box, score in zip(texts, boxes, scores):
        if (not isinstance(text, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in text)
                or not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(finite_number(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]
                or not finite_number(score) or not 0 <= score <= 1):
            raise AnalysisError('invalid_engine_result', '표 OCR 문자·좌표·점수 형식이 올바르지 않습니다.')
    return prediction


def row_corridors(rows, cells):
    """Bound a row using independent anchors, not a candidate being verified.

    A real gap between consecutive logical rows is split at its midpoint.
    Missing neighbours and table edges stop at the known row extent. Overlapping
    or reversed row evidence and vertical spans cannot define safe repair rows.
    """
    extents = {r: (min(b[1] for b in boxes), max(b[3] for b in boxes)) for r, boxes in rows.items()}
    merged_rows = {r for cell in cells if cell.get('row_span', 1) > 1
                   for r in range(position(cell)[0], position(cell)[0] + cell['row_span'])}
    ordered = sorted(extents)
    unsafe = set(merged_rows)
    for first, second in zip(ordered, ordered[1:]):
        if extents[first][1] > extents[second][0]:
            unsafe.update((first, second))
    result = {}
    for row, (top, bottom) in extents.items():
        if row in unsafe:
            continue
        before, after = extents.get(row-1), extents.get(row+1)
        lo = (before[1] + top) / 2 if before and row-1 not in unsafe else top
        hi = (bottom + after[0]) / 2 if after and row+1 not in unsafe else bottom
        result[row] = lo, hi
    return result


def anchor_geometry_issues(matches):
    """Exact text matches are candidates until their spatial order agrees.

    For each axis, disjoint logical spans must have disjoint, ordered OCR
    extents. A sweep compares against independent cells, never against the
    candidate itself. Spanning headers/cells are compared only outside their
    logical spans. Any contradiction invalidates the anchor set as a whole.
    """
    issues = []
    for axis, span_key, label in ((1, 'row_span', 'row'), (0, 'col_span', 'column')):
        intervals = []
        for cell, piece in matches:
            start = position(cell)[1-axis]
            intervals.append((start, start + cell.get(span_key, 1), piece['box'][axis],
                              piece['box'][axis+2], piece['index']))
        ended = sorted(intervals, key=lambda v: v[1])
        cursor = 0
        furthest = None
        for current in sorted(intervals):
            while cursor < len(ended) and ended[cursor][1] <= current[0]:
                previous = ended[cursor]
                if furthest is None or previous[3] > furthest[3]:
                    furthest = previous
                cursor += 1
            if furthest is not None and furthest[3] > current[2]:
                issues.append(dict(kind='anchor_geometry_conflict', axis=label,
                                   ocr_indices=[furthest[4], current[4]]))
                break
    return issues


def row_alignment_supported(peers, *, references=None):
    """Shared alignment policy for unique anchors and repeated cell confirmation.

    Multiline support must come from an independent prevalidated reference;
    a candidate cannot supply the spanning box that certifies itself.
    """
    def agreement(a, b, *, spanning=False):
        intersection = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        heights = a[3]-a[1], b[3]-b[1]
        return intersection >= .65 * (min(heights) if spanning else max(heights))
    aligned = all(agreement(a['box'], b['box'])
                  for i, (_, a) in enumerate(peers) for _, b in peers[i+1:])
    if aligned:
        return True
    references = peers if references is None else references
    return any(('\n' in str(cell.get('text', '')).strip() or '\n' in anchor['text'].strip())
               and all(anchor['box'][1] <= other['box'][1] < other['box'][3] <= anchor['box'][3]
                       and agreement(anchor['box'], other['box'], spanning=True)
                       for _, other in peers if other is not anchor)
               for cell, anchor in references)


def candidate_set_issues(matches, candidates, row_references):
    """Check all tentative confirmations/repairs against fixed references."""
    issues = anchor_geometry_issues([*matches, *candidates])
    candidate_rows = defaultdict(list)
    for cell, piece in candidates:
        if cell.get('row_span', 1) == 1:
            candidate_rows[position(cell)[0]].append((cell, piece))
    for row, peers in candidate_rows.items():
        references = row_references[row]
        if not row_alignment_supported([*references, *peers], references=references):
            issues.append(dict(kind='candidate_row_alignment_conflict', row=row))
    return issues


def independent_anchor_issues(matches, cells, declared_cols=None):
    """Do not let a candidate's own box manufacture its position evidence.

    Ordinary peers require substantial symmetric vertical overlap. Explicitly
    multiline anchors may span differently aligned peers. Missing internal
    column boundaries require evidence, including the outer edge of a merge.
    """
    rows = defaultdict(list)
    starts, ends = set(), set()
    for cell, piece in matches:
        row, col = position(cell)
        if cell.get('row_span', 1) == 1:
            rows[row].append((cell, piece))
        starts.add(col)
        ends.add(col + cell.get('col_span', 1))
    issues = []
    for row, peers in rows.items():
        if not row_alignment_supported(peers):
            issues.append(dict(kind='anchor_row_alignment_conflict', row=row))
    logical_cols = max((position(c)[1] + c.get('col_span', 1) for c in cells), default=0)
    if type(declared_cols) is int:
        logical_cols = max(logical_cols, declared_cols)
    for cell, piece in matches:
        col = position(cell)[1]
        end = col + cell.get('col_span', 1)
        # A merged neighbour provides its outer edge, never an invented
        # boundary inside the merge. Global ordering checks validate that edge.
        if col > 0 and col not in ends or end < logical_cols and end not in starts:
            issue = dict(kind='anchor_column_evidence_missing', column=col)
            if issue not in issues:
                issues.append(issue)
    return issues


def reconcile(data, prediction):
    cells = deepcopy(data.get('cells') or [])
    report = {'repairs': [], 'issues': [], 'method': 'unique_text_ocr_geometry_v1'}
    try:
        validate_prediction(prediction)
    except AnalysisError as exc:
        report['issues'].append(exc.code)
        return cells, report
    texts = prediction.get('rec_texts', [])
    boxes = prediction.get('rec_boxes', [])
    scores = prediction.get('rec_scores', [])
    if not texts or len(texts) != len(boxes) or len(texts) != len(scores):
        report['issues'].append('ocr_geometry_unavailable')
        return cells, report
    if len(cells) > 2000 or len(texts) > 2000:
        report['issues'].append('quality_input_limit')
        return cells, report
    pieces = []
    for i, (text, box, score) in enumerate(zip(texts, boxes, scores)):
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]
                or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1):
            report['issues'].append('invalid_ocr_geometry')
            return cells, report
        if normalized(text):
            pieces.append(dict(text=text, box=list(box), score=score, index=i))
    occupied = {}
    for cell in cells:
        if not isinstance(cell, dict):
            report['issues'].append('invalid_cell_structure')
            return cells, report
        row, col = position(cell)
        rs, cs = cell.get('row_span', 1), cell.get('col_span', 1)
        if any(type(v) is not int or v < 0 for v in (row, col)) or any(type(v) is not int or v < 1 for v in (rs, cs)) or rs * cs > 20000:
            report['issues'].append('invalid_cell_structure')
            return cells, report
        for r in range(row, row + rs):
            for c in range(col, col + cs):
                if (r, c) in occupied:
                    report['issues'].append('overlapping_cells')
                    return cells, report
                occupied[r, c] = cell
                if len(occupied) > 20000:
                    report['issues'].append('quality_input_limit')
                    return cells, report
    # Validate declared dimensions against every occupied slot, including
    # merged ranges, before any candidate can become a confirmation or repair.
    row_count = data.get('num_rows', max((r for r, _ in occupied), default=-1) + 1)
    col_count = data.get('num_cols', max((c for _, c in occupied), default=-1) + 1)
    if (type(row_count) is not int or type(col_count) is not int or row_count <= 0 or col_count <= 0
            or row_count * col_count > 20000
            or any(r >= row_count or c >= col_count for r, c in occupied)):
        report['issues'].append('invalid_table_dimensions')
        return cells, report
    pc = Counter(normalized(p['text']) for p in pieces)
    cc = Counter(normalized(c.get('text')) for c in cells)
    by_text = {normalized(c.get('text')): c for c in cells}
    rows, columns = defaultdict(list), defaultdict(list)
    anchors = set()
    confirmed = set()
    matches = [(by_text[normalized(p['text'])], p) for p in pieces
               if pc[normalized(p['text'])] == cc[normalized(p['text'])] == 1 and p['score'] >= .98]
    report['issues'].extend(anchor_geometry_issues(matches))
    report['issues'].extend(independent_anchor_issues(matches, cells, data.get('num_cols')))
    if report['issues']:
        # Preserve original values; conflicting anchors must not confirm cells
        # or support repairs elsewhere in the table.
        return cells, report
    for cell, p in matches:
        confirmed.add(position(cell))
        anchors.add(p['index'])
        if cell.get('row_span', 1) == 1:
            row, col = position(cell)
            rows[row].append(p['box'])
            if cell.get('col_span', 1) == 1:
                columns[col].append((row, p['box']))
    def overlap(a, b, axis):
        return max(0, min(a[axis+2], b[axis+2]) - max(a[axis], b[axis])) / min(a[axis+2]-a[axis], b[axis+2]-b[axis])
    vertical_corridors = row_corridors(rows, cells)
    # Freeze row anchors: a repeated-value candidate cannot widen its own row.
    row_evidence = {r: list(boxes) for r, boxes in rows.items()}
    row_references = defaultdict(list)
    for cell, piece in matches:
        if cell.get('row_span', 1) == 1:
            row_references[position(cell)[0]].append((cell, piece))
    def safe_rows(piece, *, repair=True):
        box = piece['box']
        touched = [r for r, boxes in row_evidence.items() if any(overlap(box, b, 1) > 0 for b in boxes)]
        if len(touched) != 1:
            return []
        row = touched[0]
        lo, hi = vertical_corridors.get(row, (math.inf, -math.inf))
        if repair and not lo <= box[1] < box[3] <= hi:
            return []
        # Repair already requires full containment in the independently fixed
        # row corridor; retain its existing bounded overlap policy.
        if repair and all(overlap(box, b, 1) >= .65 for b in row_evidence[row]):
            return [row]
        references = row_references[row]
        peers = [*references, ({'text': piece['text']}, piece)]
        return [row] if row_alignment_supported(peers, references=references) else []

    # Column corridors allow header/body alignment differences. Boundaries
    # come from gaps between existing column evidence, never the candidate.
    ordered = sorted(columns)
    logical_cols = data.get('num_cols', max((c for _, c in occupied), default=-1) + 1)
    if type(logical_cols) is not int or logical_cols <= 0:
        report['issues'].append('invalid_table_dimensions')
        return cells, report
    corridors = {}
    for col in ordered:
        left = min(b[0] for _, b in columns[col])
        right = max(b[2] for _, b in columns[col])
        # Never bridge an unanchored logical column or extrapolate past evidence.
        before, after = columns.get(col-1), columns.get(col+1)
        lo = (max(b[2] for _, b in before) + left) / 2 if before else (-math.inf if col == 0 else left)
        hi = (right + min(b[0] for _, b in after)) / 2 if after else (math.inf if col == logical_cols-1 else right)
        corridors[col] = lo, hi
    def existing_bounds(cell):
        row, col = position(cell)
        if cell.get('col_span', 1) == 1:
            return corridors.get(col, (math.inf, -math.inf))
        end = col + cell.get('col_span', 1)
        left = [p['box'][2] for c, p in matches if position(c)[1] + c.get('col_span', 1) == col]
        right = [p['box'][0] for c, p in matches if position(c)[1] == end]
        return (max(left) if left else -math.inf if col == 0 else math.inf,
                min(right) if right else math.inf if end == logical_cols else -math.inf)

    logical_row_end = max((r for r, _ in occupied), default=-1) + 1
    declared_rows = data.get('num_rows')
    if type(declared_rows) is int:
        logical_row_end = max(logical_row_end, declared_rows)

    def existing_row_matches(cell, piece, single_row):
        row, _ = position(cell)
        span = cell.get('row_span', 1)
        if span == 1:
            return single_row == [row]
        # Existing merged text may be slightly taller than adjacent glyphs.
        # Bound internal expansion by independent neighbouring row gaps, not
        # the candidate. Missing internal neighbours do not open that edge.
        # This confirms existing values only; it never creates repair anchors.
        support = [row_evidence.get(r, []) for r in range(row, row+span)]
        if not all(support):
            return False
        boxes = [box for group in support for box in group]
        box = piece['box']
        top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
        before, after = row_evidence.get(row-1), row_evidence.get(row+span)
        lo = (max(b[3] for b in before) + top) / 2 if before else (-math.inf if row == 0 else top)
        hi = (bottom + min(b[1] for b in after)) / 2 if after else (math.inf if row+span == logical_row_end else bottom)
        intersection = max(0, min(bottom, box[3]) - max(top, box[1]))
        aligned = (top <= box[1] < box[3] <= bottom
                   or intersection >= .65 * max(bottom-top, box[3]-box[1]))
        return (aligned and lo <= box[1] < box[3] <= hi
                and not any(overlap(box, b, 1) > 0 for r, group in row_evidence.items()
                            if not row <= r < row+span for b in group))

    pending = []
    reserved = set(confirmed)
    duplicate_candidates = False
    for p in pieces:
        if p['index'] in anchors or p['score'] < .98:
            continue
        single_row = safe_rows(p, repair=False)
        candidates = [c for c in cells if normalized(c.get('text')) == normalized(p['text'])
                      and existing_row_matches(c, p, single_row)
                      and existing_bounds(c)[0] <= p['box'][0] < p['box'][2] <= existing_bounds(c)[1]]
        if len(candidates) != 1:
            continue
        cell = candidates[0]
        row, col = position(cell)
        if (row, col) in reserved:
            report['issues'].append({'kind': 'duplicate_ocr_for_cell', 'ocr_index': p['index']})
            duplicate_candidates = True
            continue
        reserved.add((row, col))
        pending.append((cell, p))
    # Validate the entire tentative set before granting it evidence status.
    # Individual agreement with a reference does not imply peer agreement.
    candidate_issues = candidate_set_issues(matches, pending, row_references)
    report['issues'].extend(candidate_issues)
    candidates_rejected = duplicate_candidates or bool(candidate_issues)
    if not candidates_rejected:
        for cell, p in pending:
            row, col = position(cell)
            confirmed.add((row, col))
            if cell.get('row_span', 1) == cell.get('col_span', 1) == 1:
                columns[col].append((row, p['box']))
            anchors.add(p['index'])
    for cell in cells:
        if normalized(cell.get('text')) and position(cell) not in confirmed:
            report['issues'].append({'kind': 'cell_not_confirmed_by_ocr', 'slot': list(position(cell))})
    if candidates_rejected:
        return cells, report
    proposals = defaultdict(list)
    for p in pieces:
        if p['index'] in anchors:
            continue
        rr = safe_rows(p)
        cols = [c for c, bs in columns.items() if any(overlap(p['box'], b, 0) > 0 for _, b in bs)]
        if len(rr) != 1 or len(cols) != 1 or p['score'] < .98:
            report['issues'].append({'kind': 'unmapped_ocr', 'ocr_index': p['index']})
            continue
        col = cols[0]
        if (len({r for r, _ in columns[col]}) < 2
                or not all(overlap(p['box'], b, 0) >= .65 for _, b in columns[col])):
            report['issues'].append({'kind': 'insufficient_column_evidence', 'ocr_index': p['index']})
            continue
        lo, hi = corridors.get(col, (math.inf, -math.inf))
        if not lo <= p['box'][0] < p['box'][2] <= hi:
            report['issues'].append({'kind': 'cross_column_ocr', 'ocr_index': p['index']})
            continue
        slot = rr[0], col
        existing = occupied.get(slot)
        if existing is not None:
            if normalized(existing.get('text')) != normalized(p['text']):
                report['issues'].append({'kind': 'cell_text_conflict', 'ocr_index': p['index'], 'slot': list(slot)})
            continue
        proposals[slot].append(p)
    repairs = []
    for (row, col), candidates in proposals.items():
        if len(candidates) != 1:
            report['issues'].append({'kind': 'ambiguous_missing_cell', 'slot': [row, col]})
            continue
        p = candidates[0]
        repairs.append((dict(row=row, column=col, row_span=1, col_span=1, text=p['text']), p))
    # Slot-wise eligibility is not sufficient: validate the final combined set
    # before changing cells, repairs or covered slots. Repairs never become
    # references for themselves or for one another.
    final_issues = candidate_set_issues(matches, [*pending, *repairs], row_references)
    if final_issues:
        report['issues'].extend(final_issues)
        return cells, report
    covered = set(occupied) | {position(cell) for cell, _ in repairs}
    if any(not (0 <= r < row_count and 0 <= c < col_count) for r, c in covered):
        report['issues'].append('invalid_table_dimensions')
        return cells, report
    for cell, p in repairs:
        evidence = dict(engine='paddleocr', ocr_index=p['index'], bbox=p['box'], score=p['score'],
                        coordinate_system='ocr_crop_pixels')
        cells.append({**cell, 'recovery': evidence})
        report['repairs'].append(dict(row=cell['row'], column=cell['column'], text=p['text'], evidence=evidence))
    # Missing objects are unknown, not verified blanks. Spans explicitly cover
    # their slots; no synthetic empty cells are introduced for sparse summaries.
    gaps = [[r, c] for r in range(row_count) for c in range(col_count) if (r, c) not in covered]
    if gaps:
        report['issues'].append({'kind': 'unknown_cell_slots', 'slots': gaps})
    return cells, report


def render_cells(cells):
    """Cells are authoritative; do not invent blank HTML cells for sparse rows."""
    rows = defaultdict(list)
    for cell in cells:
        rows[position(cell)[0]].append(cell)
    text = []
    for _, row in sorted(rows.items()):
        row.sort(key=lambda c: position(c)[1])
        text.append(' | '.join(str(c.get('text', '')) for c in row))
    return '\n'.join(text), ''
