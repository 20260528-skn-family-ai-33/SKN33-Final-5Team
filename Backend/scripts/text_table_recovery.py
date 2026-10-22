"""Recover simple annual grids misclassified as text, without OCR or new values."""
import re
from docling_core.types.doc import TableItem, TableData, TableCell

YEAR = re.compile(r'(?:19|20)\d{2}년?')
NUMBER = re.compile(r'(?:[-−–—]|[-+−]?\d[\d,]*(?:\.\d+)?%?)')


def box(item, doc):
    if len(getattr(item, 'prov', [])) != 1:
        return None
    p = item.prov[0]
    b = p.bbox
    if str(b.coord_origin).split('.')[-1] == 'BOTTOMLEFT':
        h = doc.pages[p.page_no].size.height
        return p.page_no, b.l, h - b.t, b.r, h - b.b
    return p.page_no, b.l, b.t, b.r, b.b


def recoveries(doc):
    candidates = []
    for item, _ in doc.iterate_items(traverse_pictures=True):
        if not getattr(item, 'text', '').strip():
            continue
        b = box(item, doc)
        if b:
            candidates.append((item, b))
    lines = []
    for item, b in sorted(candidates, key=lambda v: (v[1][0], v[1][2], v[1][1])):
        cy = (b[2] + b[4]) / 2
        if not lines or lines[-1][0] != b[0] or abs(lines[-1][1] - cy) > 3:
            lines.append([b[0], cy, []])
        lines[-1][2].append((item, b))
    recovered = []
    claimed = set()
    for page, y, entries in lines:
        entries = sorted(entries, key=lambda v: v[1][1])
        if len(entries) < 4:
            continue
        texts = [i.text.strip() for i, _ in entries]
        if YEAR.fullmatch(texts[0]) or not all(YEAR.fullmatch(t) for t in texts[1:]):
            continue
        if len(set(texts[1:])) != len(texts) - 1 or len(entries) > 6:
            continue
        anchors = [(b[1] + b[3]) / 2 for _, b in entries[1:]]
        if any(b - a < 20 for a, b in zip(anchors, anchors[1:])):
            continue
        xmin, xmax = entries[0][1][1] - 2, entries[-1][1][3] + 3
        first_numeric_left = entries[1][1][1]
        rows, consumed = [], list(entries)
        last_y = y
        height = max(b[4] - b[2] for _, b in entries)
        data_rows = 0
        for pg, yy, group in lines:
            if pg != page or yy <= y:
                continue
            row = sorted([(i, b) for i, b in group if b[1] >= xmin and b[3] <= xmax],
                         key=lambda v: v[1][1])
            if not row:
                continue
            if yy - last_y > max(18, 3.5 * height):
                break
            values = [i.text.strip() for i, _ in row]
            if len(row) == 1 and re.fullmatch(r'\[[^\]]+\]', values[0]):
                rows.append(row)
                consumed.extend(row)
                last_y = yy
                continue
            if len(row) != len(anchors) + 2:
                break
            if not all(NUMBER.fullmatch(t) for t in values[2:]):
                break
            if any(abs((b[1] + b[3]) / 2 - a) > 12 for (_, b), a in zip(row[2:], anchors)):
                break
            if row[1][1][3] >= first_numeric_left or NUMBER.fullmatch(values[0]):
                break
            rows.append(row)
            consumed.extend(row)
            last_y = yy
            data_rows += 1
        if data_rows < 3:
            continue
        refs = {i.self_ref for i, _ in consumed}
        if refs & claimed:
            continue
        # Do not reconstruct text lying within an already recognized table.
        region = (xmin, min(b[2] for _, b in consumed), xmax, max(b[4] for _, b in consumed))
        overlap = False
        for table in doc.tables:
            b = box(table, doc)
            if b and b[0] == page:
                if min(region[2], b[3]) > max(region[0], b[1]) and min(region[3], b[4]) > max(region[1], b[2]):
                    overlap = True
        if overlap:
            continue
        width = len(anchors) + 2
        cells = []
        def cell(text, r, c, span=1, header=False):
            cells.append(TableCell(text=text, start_row_offset_idx=r, end_row_offset_idx=r+1,
                                   start_col_offset_idx=c, end_col_offset_idx=c+span,
                                   col_span=span, column_header=header))
        cell(texts[0], 0, 0, 2, True)
        for c, text in enumerate(texts[1:], 2):
            cell(text, 0, c, header=True)
        for r, row in enumerate(rows, 1):
            if len(row) == 1:
                cell(row[0][0].text, r, 0, width)
            else:
                for c, (item, _) in enumerate(row):
                    cell(item.text, r, c)
        table = TableItem(self_ref=f'#/tables/{len(doc.tables)+len(recovered)}',
                          prov=[entries[0][0].prov[0].model_copy(deep=True)],
                          data=TableData(num_rows=len(rows)+1, num_cols=width, table_cells=cells))
        recovered.append((refs, table))
        claimed.update(refs)
    return recovered


def repaired_items(doc):
    """Replace consumed text once in reading order; leave the source immutable."""
    replacements = recoveries(doc)
    by_ref = {ref: index for index, (refs, _) in enumerate(replacements) for ref in refs}
    emitted = set()
    for item, _ in doc.iterate_items(traverse_pictures=True):
        index = by_ref.get(item.self_ref)
        if index is None:
            yield item
        elif index not in emitted:
            emitted.add(index)
            yield replacements[index][1]
