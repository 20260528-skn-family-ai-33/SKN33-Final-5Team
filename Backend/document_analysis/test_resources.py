import unittest
from Backend.agent_builder.document_resources import chunks_for_asset, supports, locator


class DocumentResourceTests(unittest.TestCase):
    def test_mixed_table_exposes_document_chunks_with_native_cell_or_image_locations(self):
        asset=dict(id='file-1',name='mixed.xlsx',kind='table',text='000017 영수증',format='xlsx',
                   parser_metadata={'document_analysis':dict(contract_version='a4b.document_analysis.v1',review_required=True,
                   blocks=[dict(id='cell-1',selected=True,text='000017',native_location={'sheet':'정산','cell':'A2'},engine='native'),
                           dict(id='ocr-1',selected=True,text='영수증',native_location={'sheet':'정산','relationship_id':'rId1'},engine='paddleocr'),
                           dict(id='candidate',selected=False,text='000019',native_location={},engine='docling')])})
        self.assertTrue(supports(asset,'table'))
        self.assertTrue(supports(asset,'document'))
        chunks=list(chunks_for_asset(asset))
        self.assertEqual([c['text'] for c in chunks],['000017','영수증'])
        self.assertIsNone(chunks[0]['page'])
        self.assertEqual(chunks[0]['native_location'],{'sheet':'정산','cell':'A2'})
        self.assertIn('A2',locator(chunks[0]))
        self.assertTrue(chunks[1]['review_required'])

    def test_legacy_documents_keep_existing_source_ids_and_page_offsets(self):
        chunks=list(chunks_for_asset(dict(id='old',name='old.pdf',kind='document',format='pdf',pages=['원문'])))
        self.assertEqual(chunks[0]['source_id'],'old:1:0')
        self.assertEqual(chunks[0]['page'],1)
        self.assertFalse(supports(dict(kind='table'),'document'))


if __name__ == '__main__':
    unittest.main()
