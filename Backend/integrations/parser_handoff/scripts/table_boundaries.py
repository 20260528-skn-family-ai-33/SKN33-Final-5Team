"""Split concatenated annual tables without changing cell text or year order.

Require different titles plus footnote/identical columns or a large spatial gap.
Only wholly unused columns are compacted. Crossing rowspans retain the original.
The source Docling document is never mutated.
"""
import copy
import re
import statistics
from annual_headers import repair_annual_header


def spatial_boundary(rows, row):
    """Require a real vertical gap; never infer a boundary from text alone."""
    previous, current = rows.get(row - 1, []), rows.get(row, [])
    boxes = [c.get('bbox') for c in previous + current]
    if not boxes or any(not b or b.get('coord_origin') != 'TOPLEFT' for b in boxes):
        return False
    height = statistics.median(b['b'] - b['t'] for b in boxes)
    gap = min(c['bbox']['t'] for c in current) - max(c['bbox']['b'] for c in previous)
    title = next((c for c in current if c['start_col_offset_idx'] == 0), None)
    years = [c for c in current if re.fullmatch(r'(?:19|20)\d{2}년?', c['text'].strip())]
    return (height > 0 and gap > 2.5 * height and title is not None
            and all(c['bbox']['l'] > title['bbox']['r'] for c in years)
            and max(c['bbox']['t'] for c in current) - min(c['bbox']['t'] for c in current) < height)


def split_plan(data):
    cells = data['table_cells']
    rows = {}
    for cell in cells:
        if cell['text'].strip():
            rows.setdefault(cell['start_row_offset_idx'], []).append(cell)

    def header(row):
        items = rows.get(row, [])
        years = sorted((c['start_col_offset_idx'], c['text'].strip()) for c in items
                       if re.fullmatch(r'(?:19|20)\d{2}년?', c['text'].strip()))
        titles = [c for c in items if c['start_col_offset_idx'] == 0
                  and not re.fullmatch(r'[\d\s년]+', c['text'])]
        if len(years) < 2 or len(titles) != 1:
            return None
        if len(items) != len(years) + 1 or any(c['row_span'] != 1 for c in items):
            return None
        return titles[0]['text'].strip(), years

    def footnote(row):
        items = rows.get(row, [])
        return (len(items) == 1 and items[0]['start_col_offset_idx'] == 0
                and items[0]['row_span'] == 1
                and bool(re.match(r'^\s*\d+\)\s*\S', items[0]['text'])))

    first = header(0)
    if first is None:
        return []
    starts = [0]
    titles = {first[0]}
    for row in sorted(rows):
        current = header(row)
        old_rule = current and current[1] == first[1] and footnote(row - 1) and row > starts[-1] + 2
        spatial_rule = (current and [v for _, v in current[1]] == [v for _, v in first[1]]
                        and row > starts[-1] + 1 and spatial_boundary(rows, row))
        if (current and current[0] not in titles and (old_rule or spatial_rule)):
            starts.append(row)
            titles.add(current[0])
    if len(starts) == 1:
        return []
    plan = []
    for start, end in zip(starts, starts[1:] + [data['num_rows']]):
        stop = end
        while stop > start + 1 and footnote(stop - 1):
            stop -= 1
        plan.append((start, stop, end))
    boundaries = {n for part in plan for n in part}
    if any(c['start_row_offset_idx'] < b < c['end_row_offset_idx']
           for c in cells for b in boundaries):
        return []
    return plan


def logical_tables(table):
    """Yield (TableItem, trailing notes); preserve all cells exactly once."""
    data = table.data.model_dump()
    plan = split_plan(data)
    if not plan:
        yield repair_annual_header(table), []
        return
    for start, stop, end in plan:
        selected = []
        notes = []
        for original in data['table_cells']:
            row = original['start_row_offset_idx']
            if start <= row < stop:
                cell = copy.deepcopy(original)
                cell['start_row_offset_idx'] -= start
                cell['end_row_offset_idx'] -= start
                cell['column_header'] = cell.get('column_header', False) or cell['start_row_offset_idx'] == 0
                selected.append(cell)
            elif stop <= row < end and original['text'].strip():
                notes.append(original['text'])
        part = table.model_copy(deep=True)
        # A concatenated grid can contain wholly unused columns for one subtable.
        # Compact only those columns; retain every occupied span and its ordering.
        used = sorted({col for c in selected if c['text'].strip()
                       for col in range(c['start_col_offset_idx'], c['end_col_offset_idx'])})
        if used:
            mapped = []
            for cell in selected:
                columns = [c for c in used if cell['start_col_offset_idx'] <= c < cell['end_col_offset_idx']]
                if not columns:
                    continue
                cell['start_col_offset_idx'] = used.index(columns[0])
                cell['end_col_offset_idx'] = used.index(columns[-1]) + 1
                cell['col_span'] = len(columns)
                mapped.append(cell)
            selected = mapped
        part.data = type(table.data)(num_rows=stop-start, num_cols=len(used) or data['num_cols'],
                                     table_cells=selected, orientation=data.get('orientation', 0))
        yield repair_annual_header(part), notes
