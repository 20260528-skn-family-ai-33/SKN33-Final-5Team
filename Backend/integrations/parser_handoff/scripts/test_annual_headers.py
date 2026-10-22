import unittest
from docling_core.types.doc import TableCell, TableData, TableItem, BoundingBox
from annual_headers import repair_annual_header


def fixture():
    cells=[]
    def add(text,r,c,x,header=False):
        cells.append(TableCell(text=text,start_row_offset_idx=r,end_row_offset_idx=r+1,
                               start_col_offset_idx=c,end_col_offset_idx=c+1,column_header=header,
                               bbox=BoundingBox(l=x-8,r=x+8,t=r*15,b=r*15+7,coord_origin='TOPLEFT')))
    add('Compliance',0,0,30)
    for c,x,y in [(2,230,'2022년'),(4,320,'2023년'),(6,410,'2024년')]:add(y,0,c,x,True)
    for c,x,label in [(3,275,'Tier 1'),(4,320,'Tier 2'),(5,365,'Tier 1'),(6,410,'Tier 2')]:add(label,1,c,x)
    for r in range(2,5):
        add(f'Row {r}',r,0,30);add('%',r,1,160)
        for c,x in enumerate([230,275,320,365,410],2):add(str(10*r+c),r,c,x)
    return TableItem(self_ref='#/tables/0',data=TableData(num_rows=5,num_cols=7,table_cells=cells))


class AnnualHeaders(unittest.TestCase):
    def test_spans_values_source_and_idempotence(self):
        original=fixture();snapshot=original.model_dump()
        fixed=repair_annual_header(original)
        headers={c.text:c for c in fixed.data.table_cells if c.start_row_offset_idx==0}
        self.assertEqual((headers['2022년'].start_col_offset_idx,headers['2022년'].col_span,headers['2022년'].row_span),(2,1,2))
        self.assertEqual((headers['2023년'].start_col_offset_idx,headers['2023년'].col_span),(3,2))
        self.assertEqual((headers['2024년'].start_col_offset_idx,headers['2024년'].col_span),(5,2))
        self.assertEqual([c.model_dump() for c in original.data.table_cells if c.start_row_offset_idx>=2],
                         [c.model_dump() for c in fixed.data.table_cells if c.start_row_offset_idx>=2])
        self.assertEqual(original.model_dump(),snapshot)
        self.assertEqual(repair_annual_header(fixed).model_dump(),fixed.model_dump())
        self.assertTrue(all(c.column_header for c in fixed.data.table_cells if c.start_row_offset_idx<2))
    def test_inconsistent_child_pattern_reject(self):
        t=fixture();t.data.table_cells[7].text='Tier 3'
        self.assertIs(repair_annual_header(t),t)
    def test_geometry_disagrees_reject(self):
        t=fixture()
        c=next(c for c in t.data.table_cells if c.text=='2023년')
        c.bbox.l=900;c.bbox.r=920
        self.assertIs(repair_annual_header(t),t)
    def test_span_crosses_into_body_reject(self):
        t=fixture();t.data.table_cells[1].row_span=3;t.data.table_cells[1].end_row_offset_idx=3
        self.assertIs(repair_annual_header(t),t)
    def test_missing_body_evidence_reject(self):
        t=fixture()
        for c in t.data.table_cells:
            if c.start_row_offset_idx==4:c.bbox=None
        self.assertIs(repair_annual_header(t),t)


if __name__=='__main__':unittest.main()
