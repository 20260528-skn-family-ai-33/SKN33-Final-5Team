"""Regression checks without OCR weights or a private PDF fixture."""
import unittest
from docling_core.types.doc import DoclingDocument, DocItemLabel, Size, BoundingBox, ProvenanceItem, TableData, TableCell
from document_postprocessing import build_document_payload


class DocumentPostprocessingTests(unittest.TestCase):
    def test_picture_child_text_keeps_page_and_is_not_duplicated(self):
        doc = DoclingDocument(name="ocr-regression")
        for page in (1, 2):
            doc.add_page(page_no=page, size=Size(width=100, height=100))
        prov = ProvenanceItem(page_no=2, bbox=BoundingBox(l=0, t=10, r=50, b=0), charspan=(0, 5))
        doc.add_text(label=DocItemLabel.TEXT, text="본문", prov=prov)
        picture = doc.add_picture(prov=prov)
        doc.add_text(label=DocItemLabel.TEXT, text="12345", parent=picture, prov=prov)
        payload = build_document_payload(doc)
        self.assertEqual(payload["pages"], ["", "본문\n12345"])
        self.assertEqual(payload["sections"][-1], {"text": "12345", "pages": [2]})
        self.assertEqual(payload["text"].count("12345"), 1)
        self.assertEqual(payload["page_count"], 2)

    def test_table_stays_between_surrounding_paragraphs(self):
        doc = DoclingDocument(name="table-order-regression")
        doc.add_page(page_no=1, size=Size(width=100, height=100))
        prov = ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=10, r=50, b=0), charspan=(0, 2))
        doc.add_text(label=DocItemLabel.TEXT, text="앞 문단", prov=prov)
        doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[TableCell(
            text="표 내용", start_row_offset_idx=0, end_row_offset_idx=1,
            start_col_offset_idx=0, end_col_offset_idx=1)]), prov=prov)
        doc.add_text(label=DocItemLabel.TEXT, text="뒤 문단", prov=prov)
        payload = build_document_payload(doc)
        self.assertLess(payload["text"].index("앞 문단"), payload["text"].index("표 내용"))
        self.assertLess(payload["text"].index("표 내용"), payload["text"].index("뒤 문단"))
        self.assertEqual(payload["text"].count("표 내용"), 1)
        self.assertEqual(len(payload["tables"]), 1)


if __name__ == "__main__":
    unittest.main()
