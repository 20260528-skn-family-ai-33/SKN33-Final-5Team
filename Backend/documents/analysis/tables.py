"""Keep recognized HTML cell structure as data; never execute parser HTML."""
from html.parser import HTMLParser
from itertools import groupby


class TableCells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self.row = -1
        self.column = 0
        self.active = None
        self.occupied = set()
        self.in_head = False
        self.has_head = False
        self.unscoped_headers = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'thead':
            self.in_head = True
            self.has_head = True
        elif tag == 'tr':
            self.row += 1
            self.column = 0
        elif tag in {'td', 'th'}:
            if self.active is not None:
                self.handle_endtag('td')
            while (self.row, self.column) in self.occupied:
                self.column += 1
            row_span, col_span = int(attrs.get('rowspan', 1)), int(attrs.get('colspan', 1))
            if (not 1 <= row_span <= 500 or not 1 <= col_span <= 1000 or row_span * col_span > 20000
                    or len(self.cells) >= 20000 or self.row > 5000 or self.column > 1000):
                raise ValueError('표 병합 범위 제한을 넘었습니다.')
            self.active = dict(row=max(0, self.row), column=self.column, row_span=row_span, col_span=col_span, text='')
            scope = (attrs.get('scope') or '').strip().lower()
            if (self.in_head and scope not in ('row', 'rowgroup')
                    or tag == 'th' and scope in ('col', 'colgroup')):
                self.active['column_header'] = True
            elif tag == 'th' and not scope:
                self.unscoped_headers.add((self.active['row'], self.active['column']))
        elif tag == 'br' and self.active is not None:
            self.active['text'] += '\n'

    def handle_data(self, data):
        if self.active is not None:
            self.active['text'] += data

    def handle_endtag(self, tag):
        if tag == 'thead':
            self.in_head = False
        elif tag in {'td', 'th'} and self.active is not None:
            cell = self.active
            self.cells.append(cell)
            for row in range(cell['row'], cell['row'] + cell['row_span']):
                for col in range(cell['column'], cell['column'] + cell['col_span']):
                    self.occupied.add((row, col))
            self.column += cell['col_span']
            self.active = None


def from_html(html):
    parser = TableCells()
    parser.feed(html)
    parser.handle_endtag('td')
    if not parser.has_head:
        leading_headers = []
        for _, cells in groupby(parser.cells, key=lambda cell: cell['row']):
            row = list(cells)
            if all(cell.get('column_header') or (cell['row'], cell['column']) in parser.unscoped_headers for cell in row):
                leading_headers.extend(row)
                continue
            # An unscoped header-only prefix needs a body row to disambiguate its end.
            for cell in leading_headers:
                cell['column_header'] = True
            break
    return dict(html=html, cells=parser.cells)
