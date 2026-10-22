import tempfile, unittest
from pathlib import Path
import pymupdf
from docling_core.types.doc import DoclingDocument, DocItemLabel, Size, BoundingBox, ProvenanceItem, CoordOrigin
from lighton_supplement import supplement_picture_text, eligible, agreement, repetitive_suffix, numeric_ocr_replacement

class RepetitionTests(unittest.TestCase):
    def test_exact_cycles_stop(self):
        self.assertTrue(repetitive_suffix([99,98]+[1,2,3,4,5,6]*4))
        self.assertTrue(repetitive_suffix([1]*24))
    def test_short_or_nonrepeating_text_keeps_budget(self):
        self.assertFalse(repetitive_suffix([1,2,3]*4))
        self.assertFalse(repetitive_suffix(list(range(128))))
        self.assertFalse(repetitive_suffix([1,2,3,4,5,6]*3+[1,2,3,4,5,7]))

class Provider:
    def __init__(self, candidate='Monday', local='Monday', complete=True):
        self.candidate=candidate; self.local=local; self.complete=complete
    def detect(self, image):
        return [([[39,39],[159,39],[159,69],[39,69]],'Mondav',.7)]
    def read(self, image, max_tokens):
        return (self.candidate if max_tokens>128 else self.local,self.complete,.01)

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.source=Path(self.temp.name)/'test.pdf'
        pdf=pymupdf.open();pdf.new_page(width=200,height=100);pdf.save(self.source);pdf.close()
        self.doc=DoclingDocument(name='test');self.doc.add_page(page_no=1,size=Size(width=200,height=100))
        def prov(l,t,r,b):return ProvenanceItem(page_no=1,bbox=BoundingBox(l=l,t=t,r=r,b=b,coord_origin=CoordOrigin.TOPLEFT),charspan=(0,6))
        pic=self.doc.add_picture(prov=prov(10,10,190,90))
        for text,box in [('Mondav',(20,20,60,30)),('Alpha',(20,40,60,50)),('Beta',(20,60,60,70))]:
            self.doc.add_text(label=DocItemLabel.TEXT,text=text,parent=pic,prov=prov(*box))
    def tearDown(self):self.temp.cleanup()
    def test_sparse_picture_can_be_repaired(self):
        self.doc.pictures[0].children=self.doc.pictures[0].children[:1]
        class Credible(Provider):
            def detect(self,image):return super().detect(image)+[([[39,99],[159,99],[159,129],[39,129]],'Alpha',.99)]
        result,report=supplement_picture_text(self.source,self.doc,Credible())
        self.assertEqual(report['errors'],[])
        self.assertEqual(result.texts[0].text,'Monday')
        self.assertEqual(len(report['accepted']),1)
    def test_sparse_noise_does_not_call_vlm(self):
        self.doc.pictures[0].children=[]
        class Noise(Provider):
            def detect(self,image):return [([[39,39],[159,39],[159,69],[39,69]],'Noisy text',.1)]
            def read(self,*args):raise AssertionError('unreliable picture')
        result,report=supplement_picture_text(self.source,self.doc,Noise())
        self.assertEqual(report['errors'],[])
        self.assertEqual(report['accepted'],[])
    def test_numeric_image_ocr_requires_strong_independent_agreement(self):
        self.doc.texts[0].text='978'
        class Strong(Provider):
            def detect(self,image):return [([[39,39],[159,39],[159,69],[39,69]],'Monday',.99)]
        result,report=supplement_picture_text(self.source,self.doc,Strong())
        self.assertEqual(result.texts[0].text,'Monday')
        self.assertEqual(report['accepted'][0]['previous'],'978')
        self.assertFalse(numeric_ocr_replacement('978','Monday','Monday',.7))
        self.assertFalse(numeric_ocr_replacement('978','Sunday','Monday',.99))
        self.assertFalse(numeric_ocr_replacement('Invoice 978','Monday','Monday',.99))
        self.assertFalse(numeric_ocr_replacement('978','123','123',.99))
    def test_empty_picture_without_detected_text_never_calls_model(self):
        self.doc.pictures[0].children=[]
        class Empty(Provider):
            def detect(self,image):return []
            def read(self,*args):raise AssertionError('No text detected')
        result,report=supplement_picture_text(self.source,self.doc,Empty())
        self.assertEqual(report['errors'],[])
        self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_replace_without_mutating_input_or_order(self):
        result,report=supplement_picture_text(self.source,self.doc,Provider())
        self.assertEqual(report['errors'],[]);self.assertEqual(len(report['accepted']),1)
        self.assertEqual([x.text for x in result.texts],['Monday','Alpha','Beta'])
        self.assertEqual(self.doc.texts[0].text,'Mondav')
        self.assertEqual(result.pictures[0].children,self.doc.pictures[0].children)
        again,second=supplement_picture_text(self.source,result,Provider())
        self.assertEqual(second['accepted'],[]);self.assertEqual(again.model_dump(),result.model_dump())
    def test_disagreement_keeps_original(self):
        result,report=supplement_picture_text(self.source,self.doc,Provider(local='Tuesday'))
        self.assertEqual(report['accepted'],[]);self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_incomplete_keeps_original(self):
        result,report=supplement_picture_text(self.source,self.doc,Provider(complete=False))
        self.assertEqual(report['accepted'],[]);self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_truncated_verification_can_retry_once(self):
        class Retry(Provider):
            def __init__(self):self.calls=0
            def read(self,image,max_tokens):
                self.calls+=1
                if self.calls==2:return 'Monday '*20,False,.2
                return 'Monday',True,.01
        provider=Retry()
        result,report=supplement_picture_text(self.source,self.doc,provider)
        self.assertEqual(provider.calls,3)
        self.assertEqual(result.texts[0].text,'Monday')
        self.assertTrue(report['accepted'][0]['retry']['complete'])
    def test_completed_disagreement_does_not_retry(self):
        class Disagree(Provider):
            def __init__(self):self.calls=0
            def read(self,image,max_tokens):
                self.calls+=1
                return ('Monday' if self.calls==1 else 'Tuesday'),True,.01
        provider=Disagree()
        result,report=supplement_picture_text(self.source,self.doc,provider)
        self.assertEqual(provider.calls,2)
        self.assertEqual(report['accepted'],[])
        self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_error_rolls_back(self):
        class Broken(Provider):
            def read(self,*args):raise RuntimeError('test failure')
        result,report=supplement_picture_text(self.source,self.doc,Broken())
        self.assertTrue(report['errors']);self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_numbers_and_repetition_rejected(self):
        for s in ['24:00','Monday 2','aaaaaa']:self.assertFalse(eligible(s))
        self.assertFalse(agreement('Monday','Monday Tuesday',True))
    def test_confident_agreement_does_not_call_lighton(self):
        class Certain(Provider):
            def detect(self,image):
                return [([[39,39],[159,39],[159,69],[39,69]],'Mondav',.99)]
            def read(self,*args):raise AssertionError('unnecessary model call')
        result,report=supplement_picture_text(self.source,self.doc,Certain())
        self.assertEqual(report['errors'],[])
        self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_native_text_is_protected(self):
        pdf=pymupdf.open(self.source);pdf[0].insert_text((20,28),'Native',fontsize=8)
        pdf.saveIncr();pdf.close()
        result,report=supplement_picture_text(self.source,self.doc,Provider())
        self.assertEqual(report['accepted'],[]);self.assertEqual(result.model_dump(),self.doc.model_dump())
    def test_insertion_does_not_reorder_existing_children(self):
        # Move the existing item elsewhere: the recognized word is now genuinely missing.
        self.doc.texts[0].text='Gamma'
        self.doc.texts[0].prov[0].bbox=BoundingBox(l=100,t=70,r=150,b=80,coord_origin=CoordOrigin.TOPLEFT)
        result,report=supplement_picture_text(self.source,self.doc,Provider())
        self.assertEqual(report['errors'],[]);self.assertEqual(report['accepted'][0]['action'],'insert')
        original=[x.cref for x in self.doc.pictures[0].children]
        self.assertEqual([x.cref for x in result.pictures[0].children if x.cref in original],original)
        self.assertEqual(sum(x.text=='Monday' for x in result.texts),1)
    def test_insertion_does_not_split_existing_line(self):
        self.doc.texts[0].text='First'
        self.doc.texts[0].prov[0].bbox=BoundingBox(l=100,t=20,r=120,b=30,coord_origin=CoordOrigin.TOPLEFT)
        self.doc.texts[1].text='Second'
        self.doc.texts[1].prov[0].bbox=BoundingBox(l=130,t=23,r=150,b=32,coord_origin=CoordOrigin.TOPLEFT)
        result,report=supplement_picture_text(self.source,self.doc,Provider())
        self.assertEqual(report['errors'],[])
        texts=[r.resolve(result).text for r in result.pictures[0].children]
        self.assertEqual(texts[:3],['First','Second','Monday'])

if __name__=='__main__':unittest.main()
