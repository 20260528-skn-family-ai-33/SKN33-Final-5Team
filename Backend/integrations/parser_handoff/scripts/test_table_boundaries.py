import unittest
from docling_core.types.doc import TableData, TableCell, TableItem, BoundingBox
from table_boundaries import logical_tables, split_plan


def fixture():
    rows = [["Revenue", "", "2022년", "2023년"], ["Sales", "USD", "1.25", "-2.5"], ["Profit", "USD", "3", "4"], ["1) note", "", "", ""], ["Region", "", "2022년", "2023년"], ["Asia", "USD", "5", "6"]]
    cells = [TableCell(text=v,start_row_offset_idx=r,end_row_offset_idx=r+1,start_col_offset_idx=c,end_col_offset_idx=c+1) for r,row in enumerate(rows) for c,v in enumerate(row) if v]
    return TableItem(self_ref="#/tables/0", data=TableData(num_rows=6,num_cols=4,table_cells=cells))

class Boundaries(unittest.TestCase):
    def test_preserves_text_columns_notes_and_source(self):
        table=fixture(); original=table.model_dump();parts=list(logical_tables(table))
        self.assertEqual(len(parts),2)
        self.assertEqual(parts[0][1],["1) note"])
        self.assertEqual(table.model_dump(),original)
        self.assertEqual([(c.text,c.start_col_offset_idx) for p,_ in parts for c in p.data.table_cells],[(c.text,c.start_col_offset_idx) for c in table.data.table_cells if c.text!="1) note"])
        self.assertEqual(parts[0][0].data.num_rows,3)
    def test_repeated_same_title_not_split(self):
        t=fixture()
        for c in t.data.table_cells:
            if c.text=="Region":c.text="Revenue"
        self.assertEqual(split_plan(t.data.model_dump()),[])
    def test_no_note_not_split(self):
        t=fixture()
        for c in t.data.table_cells:
            if c.text=="1) note":c.text="Subtotal"
        self.assertEqual(split_plan(t.data.model_dump()),[])
    def test_crossing_span_not_split(self):
        t=fixture();c=t.data.table_cells[4];c.row_span=4;c.end_row_offset_idx=5
        self.assertEqual(split_plan(t.data.model_dump()),[])
    def test_changed_year_columns_not_split(self):
        t=fixture()
        for c in t.data.table_cells:
            if c.start_row_offset_idx==4 and c.text=="2023년":c.text="2024년"
        self.assertEqual(split_plan(t.data.model_dump()),[])
    def test_spatial_gap_allows_distinct_header_without_note(self):
        t=fixture()
        for c in t.data.table_cells:
            r=c.start_row_offset_idx
            c.bbox=BoundingBox(l=c.start_col_offset_idx*60,r=c.start_col_offset_idx*60+30,
                    t=r*10+(30 if r>=4 else 0),b=r*10+6+(30 if r>=4 else 0),coord_origin='TOPLEFT')
            if c.text=='1) note':c.text='Subtotal'
        self.assertEqual(len(split_plan(t.data.model_dump())),2)

if __name__=="__main__":unittest.main()

