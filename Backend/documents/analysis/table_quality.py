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
        if (not isinstance(text, str) or not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]
                or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
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
    pc = Counter(normalized(p['text']) for p in pieces)
    cc = Counter(normalized(c.get('text')) for c in cells)
    by_text = {normalized(c.get('text')): c for c in cells}
    rows, columns = defaultdict(list), defaultdict(list)
    anchors = set()
    confirmed = set()
    for p in pieces:
        key = normalized(p['text'])
        if pc[key] == cc[key] == 1 and p['score'] >= .98:
            cell = by_text[key]
            confirmed.add(position(cell))
            anchors.add(p['index'])
            if cell.get('row_span', 1) == cell.get('col_span', 1) == 1:
                row, col = position(cell)
                rows[row].append(p['box'])
                columns[col].append((row, p['box']))
                anchors.add(p['index'])
    def overlap(a, b, axis):
        return max(0, min(a[axis+2], b[axis+2]) - max(a[axis], b[axis])) / min(a[axis+2]-a[axis], b[axis+2]-b[axis])
    vertical_corridors = row_corridors(rows, cells)
    # Freeze row anchors: a repeated-value candidate cannot widen its own row.
    row_evidence = {r: list(boxes) for r, boxes in rows.items()}
    def safe_rows(box, *, repair=True):
        touched = [r for r, boxes in row_evidence.items() if any(overlap(box, b, 1) > 0 for b in boxes)]
        if len(touched) != 1:
            return []
        row = touched[0]
        lo, hi = vertical_corridors.get(row, (math.inf, -math.inf))
        if repair and not lo <= box[1] < box[3] <= hi:
            return []
        return [row] if all(overlap(box, b, 1) >= .65 for b in row_evidence[row]) else []

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
    for p in pieces:
        if p['index'] in anchors or p['score'] < .98:
            continue
        rr = safe_rows(p['box'], repair=False)
        if len(rr) != 1:
            continue
        candidates = [c for c in cells if position(c)[0] == rr[0]
                      and normalized(c.get('text')) == normalized(p['text'])
                      and c.get('row_span', 1) == c.get('col_span', 1) == 1
                      and corridors.get(position(c)[1], (math.inf, -math.inf))[0] <= p['box'][0]
                      and p['box'][2] <= corridors.get(position(c)[1], (math.inf, -math.inf))[1]]
        if len(candidates) != 1:
            continue
        cell = candidates[0]
        row, col = position(cell)
        lo, hi = corridors.get(col, (math.inf, -math.inf))
        if not lo <= p['box'][0] < p['box'][2] <= hi:
            continue
        if (row, col) in confirmed:
            report['issues'].append({'kind': 'duplicate_ocr_for_cell', 'ocr_index': p['index']})
            anchors.add(p['index'])
            continue
        confirmed.add((row, col))
        rows[row].append(p['box'])
        columns[col].append((row, p['box']))
        anchors.add(p['index'])
    for cell in cells:
        if normalized(cell.get('text')) and position(cell) not in confirmed:
            report['issues'].append({'kind': 'cell_not_confirmed_by_ocr', 'slot': list(position(cell))})
    proposals = defaultdict(list)
    for p in pieces:
        if p['index'] in anchors:
            continue
        rr = safe_rows(p['box'])
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
    for (row, col), candidates in proposals.items():
        if len(candidates) != 1:
            report['issues'].append({'kind': 'ambiguous_missing_cell', 'slot': [row, col]})
            continue
        p = candidates[0]
        evidence = dict(engine='paddleocr', ocr_index=p['index'], bbox=p['box'], score=p['score'],
                        coordinate_system='ocr_crop_pixels')
        cells.append(dict(row=row, column=col, row_span=1, col_span=1, text=p['text'], recovery=evidence))
        report['repairs'].append(dict(row=row, column=col, text=p['text'], evidence=evidence))
    # Missing objects are unknown, not verified blanks. Spans explicitly cover
    # their slots; no synthetic empty cells are introduced for sparse summaries.
    covered = set(occupied) | {slot for slot, ps in proposals.items() if len(ps) == 1}
    row_count = data.get('num_rows', max((r for r, _ in covered), default=-1) + 1)
    col_count = data.get('num_cols', max((c for _, c in covered), default=-1) + 1)
    if (type(row_count) is not int or type(col_count) is not int or row_count <= 0 or col_count <= 0
            or row_count * col_count > 20000 or any(r >= row_count or c >= col_count for r, c in covered)):
        report['issues'].append('invalid_table_dimensions')
    else:
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
