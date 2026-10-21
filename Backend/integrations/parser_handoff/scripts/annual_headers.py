"""Repair two-level annual headers using repeated child labels and body geometry."""
import copy
import re
import statistics

YEAR = re.compile(r'(?:19|20)\d{2}년?')
NUMBER = re.compile(r'[-+−]?\d[\d,]*(?:\.\d+)?%?')


def repair_annual_header(table):
    """Change header spans only; reject ambiguous groups and preserve all values."""
    data = table.data.model_dump()
    cells = data['table_cells']
    if any(c['start_row_offset_idx'] < 2 < c['end_row_offset_idx'] for c in cells):
        return table
    top = sorted([c for c in cells if c['start_row_offset_idx'] == 0 and c['text'].strip()],
                 key=lambda c:c['start_col_offset_idx'])
    children = sorted([c for c in cells if c['start_row_offset_idx'] == 1 and c['text'].strip()],
                      key=lambda c:c['start_col_offset_idx'])
    years = [c for c in top if YEAR.fullmatch(c['text'].strip())]
    titles = [c for c in top if not YEAR.fullmatch(c['text'].strip())]
    if (len(years) < 2 or len(titles) != 1 or titles[0]['start_col_offset_idx'] != 0
            or len(children) < 4 or len({c['text'].strip() for c in years}) != len(years)):
        return table
    if any(NUMBER.fullmatch(c['text'].strip()) or c['row_span'] != 1 or c['col_span'] != 1 for c in children):
        return table
    # Repeated ordered labels define complete groups, not a guess from year text.
    labels = [re.sub(r'\s+', '', c['text']) for c in children]
    starts = [i for i, text in enumerate(labels) if text == labels[0]]
    if len(starts) < 2:
        return table
    ends = starts[1:] + [len(labels)]
    pattern = labels[:ends[0]]
    if len(pattern) < 2 or len(set(pattern)) != len(pattern):
        return table
    groups = []
    for start, end in zip(starts, ends):
        group = children[start:end]
        columns = [c['start_col_offset_idx'] for c in group]
        if labels[start:end] != pattern or columns != list(range(columns[0], columns[-1]+1)):
            return table
        groups.append(columns)
    # Numeric body columns must independently support every leaf column.
    body = {}
    for c in cells:
        b = c.get('bbox')
        if (c['start_row_offset_idx'] >= 2 and c['row_span'] == c['col_span'] == 1
                and NUMBER.fullmatch(c['text'].strip()) and b
                and b.get('coord_origin') == 'TOPLEFT'):
            body.setdefault(c['start_col_offset_idx'], []).append((b['l']+b['r'])/2)
    columns = sorted(col for col, xs in body.items() if len(xs) >= 3)
    if not columns or columns != list(range(columns[0], columns[-1]+1)):
        return table
    covered = {col for group in groups for col in group}
    groups += [[col] for col in columns if col not in covered]
    groups.sort(key=lambda g:g[0])
    if len(groups) != len(years) or sorted(col for g in groups for col in g) != columns:
        return table
    xs = [statistics.median(body[col]) for col in columns]
    if any(b-a < 10 for a,b in zip(xs,xs[1:])):
        return table
    edges = [xs[0]-(xs[1]-xs[0])/2] + [(a+b)/2 for a,b in zip(xs,xs[1:])] + [xs[-1]+(xs[-1]-xs[-2])/2]
    for year, group in zip(years, groups):
        b = year.get('bbox')
        if not b or b.get('coord_origin') != 'TOPLEFT':
            return table
        center = (b['l']+b['r'])/2
        left, right = edges[columns.index(group[0])], edges[columns.index(group[-1])+1]
        if not left < center < right:
            return table
    # Child headers must lie over their own body columns (right or center aligned).
    for child in children:
        b = child.get('bbox')
        col = child['start_col_offset_idx']
        if not b or col not in columns or b.get('coord_origin') != 'TOPLEFT':
            return table
        center = (b['l']+b['r'])/2
        if not edges[columns.index(col)] < center < edges[columns.index(col)+1]:
            return table
    if columns[0] <= 0 or titles[0]['end_row_offset_idx'] > 2:
        return table
    new_cells = [copy.deepcopy(c) for c in cells if c['start_row_offset_idx'] >= 2]
    def header(original, start, stop, rowspan):
        c = copy.deepcopy(original)
        c.update(start_col_offset_idx=start, end_col_offset_idx=stop, col_span=stop-start,
                 end_row_offset_idx=c['start_row_offset_idx']+rowspan, row_span=rowspan,
                 column_header=True)
        new_cells.append(c)
    header(titles[0], 0, columns[0], 2)
    for year, group in zip(years, groups):
        header(year, group[0], group[-1]+1, 2 if len(group)==1 else 1)
    for child in children:
        header(child, child['start_col_offset_idx'], child['end_col_offset_idx'], 1)
    part = table.model_copy(deep=True)
    part.data = type(table.data)(num_rows=data['num_rows'], num_cols=data['num_cols'],
                                table_cells=new_cells, orientation=data.get('orientation', 0))
    return part
