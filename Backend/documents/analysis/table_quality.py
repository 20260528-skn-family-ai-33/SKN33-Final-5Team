"""Conservative OCR reconciliation; geometry is learned only from exact unique anchors.

All boxes belong to the same OCR crop. No Docling point/pixel assumption, fuzzy
text matching, arithmetic repair, or inference of blank/merged cells is allowed.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re


def normalized(value):
    return re.sub(r'\s+', '', str(value or ''))


def position(cell):
    return (cell.get('row', cell.get('start_row_offset_idx')),
            cell.get('column', cell.get('start_col_offset_idx')))


def reconcile(data, prediction):
    cells = deepcopy(data.get('cells') or [])
    report = {'repairs': [], 'issues': [], 'method': 'unique_text_ocr_geometry_v1'}
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
    for text, count in cc.items():
        if text and pc[text] < count:
            report['issues'].append({'kind': 'cell_not_confirmed_by_ocr', 'text': text})
    rows, columns = defaultdict(list), defaultdict(list)
    anchors = set()
    for p in pieces:
        key = normalized(p['text'])
        if pc[key] == cc[key] == 1 and p['score'] >= .98:
            cell = by_text[key]
            if cell.get('row_span', 1) == cell.get('col_span', 1) == 1:
                row, col = position(cell)
                rows[row].append(p['box'])
                columns[col].append((row, p['box']))
                anchors.add(p['index'])
    def overlap(a, b, axis):
        return max(0, min(a[axis+2], b[axis+2]) - max(a[axis], b[axis])) / min(a[axis+2]-a[axis], b[axis+2]-b[axis])
    # Repeated numbers become anchors only after both their row and column
    # agree with unique anchors and the existing cell has the exact same text.
    for p in pieces:
        if p['index'] in anchors or p['score'] < .98:
            continue
        rr = [r for r, bs in rows.items() if all(overlap(p['box'], b, 1) >= .65 for b in bs)]
        cols = [c for c, bs in columns.items() if all(overlap(p['box'], b, 0) >= .65 for _, b in bs)]
        if len(rr) == len(cols) == 1:
            cell = occupied.get((rr[0], cols[0]))
            if (cell is not None and normalized(cell.get('text')) == normalized(p['text'])
                    and cell.get('row_span', 1) == cell.get('col_span', 1) == 1):
                rows[rr[0]].append(p['box'])
                columns[cols[0]].append((rr[0], p['box']))
                anchors.add(p['index'])
    proposals = defaultdict(list)
    for p in pieces:
        if p['index'] in anchors:
            continue
        rr = [r for r, bs in rows.items() if all(overlap(p['box'], b, 1) >= .65 for b in bs)]
        cols = [c for c, bs in columns.items() if len({r for r, _ in bs}) >= 2
                and all(overlap(p['box'], b, 0) >= .65 for _, b in bs)]
        if len(rr) != 1 or len(cols) != 1 or p['score'] < .98:
            report['issues'].append({'kind': 'unmapped_ocr', 'ocr_index': p['index']})
            continue
        slot = rr[0], cols[0]
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
