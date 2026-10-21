import unittest
from docling_core.types.doc import DoclingDocument, DocItemLabel, Size, BoundingBox, ProvenanceItem
from text_table_recovery import recoveries
from document_postprocessing import build_document_payload


def fixture():
    doc=DoclingDocument(name='annual-text')
    doc.add_page(page_no=1,size=Size(width=500,height=500))
    rows=[['Water','2022년','2023년','2024년'],
          ['Intake','ton','172,811','177,361','188,540'],
          ['Ground','ton','698','786','902'],
          ['Reuse','ton','116,590','122,891','125,463']]
    for r,row in enumerate(rows):
        xs=[10,210,290,370] if r==0 else [10,130,210,290,370]
        for text,x in zip(row,xs):
            prov=ProvenanceItem(page_no=1,bbox=BoundingBox(l=x,r=x+30,t=10+r*14,b=17+r*14,coord_origin='TOPLEFT'),charspan=(0,len(text)))
            doc.add_text(label=DocItemLabel.TEXT,text=text,prov=prov)
    return doc


class Recovery(unittest.TestCase):
    def test_values_preserved_once_source_unchanged(self):
        doc=fixture();snapshot=doc.model_dump()
        result=build_document_payload(doc)
        self.assertEqual(len(result['tables']),1)
        for value in ['172,811','177,361','188,540']:
            self.assertEqual(result['text'].count(value),1)
        self.assertEqual(doc.model_dump(),snapshot)
        self.assertEqual(recoveries(doc)[0][1].data.num_cols,5)
    def test_misaligned_numbers_reject(self):
        doc=fixture()
        for item in doc.texts:
            if item.text=='177,361':
                item.prov[0].bbox.l+=35;item.prov[0].bbox.r+=35
        self.assertEqual(recoveries(doc),[])
    def test_prose_in_value_column_reject(self):
        doc=fixture()
        for item in doc.texts:
            if item.text=='177,361':item.text='narrative prose'
        self.assertEqual(recoveries(doc),[])
    def test_existing_table_overlap_reject(self):
        from docling_core.types.doc import TableData, TableCell
        doc=fixture()
        doc.add_table(data=TableData(num_rows=1,num_cols=1,table_cells=[
            TableCell(text='Existing',start_row_offset_idx=0,end_row_offset_idx=1,start_col_offset_idx=0,end_col_offset_idx=1)]),
            prov=doc.texts[0].prov[0])
        self.assertEqual(recoveries(doc),[])


if __name__=='__main__':unittest.main()
