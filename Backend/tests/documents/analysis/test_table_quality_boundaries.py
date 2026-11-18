"""Second review reproductions and adjacent malformed/capacity boundaries."""
import copy
import json

import pytest
from PIL import Image
from documents.analysis.contracts import MAX_RESULT, validate
from documents.analysis.pipeline import run
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case


@pytest.mark.parametrize('right', [190.001, 215, 230])
def test_repair_box_cannot_cross_column_corridor_without_touching_next_text(right):
    data, prediction = review_case([['Item','Count','Price'],['Bread','1',''],['Milk',None,'']], [
        ('Item',[0,0,50,20]), ('Count',[110,0,150,20]), ('Price',[230,0,280,20]),
        ('Bread',[0,30,50,50]), ('1',[110,30,130,50]), ('Milk',[0,60,50,80]),
        ('2 1200',[110,60,right,80])])
    cells, quality = reconcile(data, prediction)
    assert quality['issues'] and not quality['repairs']
    assert cells == data['cells']
    from workflow_engine.executors.document.table_rows import native_records, TableStructureError
    with pytest.raises(TableStructureError):
        native_records([dict(file_id='synthetic',file_name='test',source_id='table',table_cells=cells)],
                       ['Item','Count','Price'], [], 10)


class Replay:
    def __init__(self, prediction, cells=None):
        self.attempts = []
        self.prediction = prediction
        self.cells = cells or [dict(row=r,column=c,text=t) for r,row in enumerate(
            [['Item','Count'],['Milk','1']]) for c,t in enumerate(row)]
    def run(self, engine, **kwargs):
        if engine == 'layout':
            return dict(version='test',model={},outputs=[dict(source=str(p),result={'boxes':[
                dict(label='text',score=.99,coordinate=[0,0,200,20]),
                dict(label='table',score=.99,coordinate=[0,25,200,195])]}) for p in kwargs['sources']])
        if engine == 'docling':
            return dict(engine=engine,version='test',blocks=[dict(kind='table',text='BASE TABLE',data={
                'cells':copy.deepcopy(self.cells)})])
        prediction = self.prediction if kwargs.get('phase') == 'table_verification' else dict(
            rec_texts=['BASE BODY'],rec_boxes=[[0,0,100,15]],rec_scores=[.999])
        return dict(version='test',outputs=[dict(source=str(p),result=copy.deepcopy(prediction)) for p in kwargs['sources']])


BAD = [None, [], 'text', 7, {'rec_texts':[], 'rec_boxes':[], 'rec_scores':None},
       {'rec_texts':['x'], 'rec_boxes':[], 'rec_scores':[.99]},
       {'rec_texts':[None], 'rec_boxes':[[0,0,10,10]], 'rec_scores':[.99]},
       {'rec_texts':['x'], 'rec_boxes':[[0,0,10,10]], 'rec_scores':[float('nan')]},
       {'rec_texts':['x'], 'rec_boxes':[[0,0,float('inf'),10]], 'rec_scores':[.99]},
       {'rec_texts':['x'], 'rec_boxes':[[0,0,10,10]], 'rec_scores':['.99']}]


@pytest.mark.parametrize('prediction', BAD)
def test_invalid_optional_ocr_preserves_body_and_table(tmp_path, prediction):
    source = tmp_path / 'receipt.png'
    Image.new('RGB',(200,200),'white').save(source)
    payload = run(source,tmp_path/'work',engines=Replay(prediction))
    analysis = payload['document_analysis']
    assert payload['parser_status'] == 'partial'
    assert 'BASE BODY' in payload['text'] and 'BASE TABLE' in payload['text']
    assert 'table_verification:invalid_engine_result' in payload['warnings']
    assert all('table_ocr' not in b['data'] for b in analysis['blocks'])
    assert all('original_cells' not in b['data'] for b in analysis['blocks'])
    json.dumps(payload, allow_nan=False)


def large_table():
    cells, texts, boxes = [], [], []
    for r in range(35):
        for c in range(21):
            text = f'R{r:02}C{c:02}'
            box = [c*8,r*4,c*8+5,r*4+3]
            texts.append(text); boxes.append(box)
            if (r,c) != (1,1):
                cells.append(dict(start_row_offset_idx=r,end_row_offset_idx=r+1,
                    start_col_offset_idx=c,end_col_offset_idx=c+1,row_span=1,col_span=1,
                    column_header=r==0,row_header=False,row_section=False,text=text,
                    bbox=dict(l=box[0],t=box[1],r=box[2],b=box[3],coord_origin='TOPLEFT')))
    return cells,dict(rec_texts=texts,rec_boxes=boxes,rec_scores=[.999]*len(texts))


def test_sixteen_frame_tiff_keeps_all_base_tables_when_enrichment_exceeds_limit(tmp_path):
    cells,prediction = large_table()
    source = tmp_path/'sixteen.tiff'
    images = [Image.new('RGB',(200,200),(255-i,255,255)) for i in range(16)]
    images[0].save(source,save_all=True,append_images=images[1:],compression='tiff_deflate')
    # Tables only, to keep the existing supplement budget at 16 regions.
    class Tables(Replay):
        def run(self, engine, **kwargs):
            result = super().run(engine,**kwargs)
            if engine == 'layout':
                for out in result['outputs']:out['result']['boxes'] = out['result']['boxes'][1:]
            return result
    payload = run(source,tmp_path/'work',engines=Tables(prediction,cells))
    analysis=payload['document_analysis']
    tables=[b for b in analysis['blocks'] if b['engine']=='docling']
    assert len(tables)==16
    assert 'table_verification:result_limit' in payload['warnings']
    assert payload['parser_status']=='partial'
    reverted=[b for b in tables if 'original_cells' not in b['data']]
    assert reverted and all(b['data']['cells']==cells and b['text']=='BASE TABLE' for b in reverted)
    assert len(json.dumps(analysis,ensure_ascii=False,allow_nan=False).encode())<=MAX_RESULT
    assert len(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode())<=12*1024*1024
    validate(analysis)


def test_pipeline_scopes_bad_table_without_poisoning_selected_good_table(tmp_path):
    from documents.resources.document_resources import chunks_for_asset, select_table_parts
    from workflow_engine.executors.document.table_rows import native_records, TableStructureError
    prediction=dict(rec_texts=['Item','Count','Milk','1'],rec_boxes=[
        [0,0,40,10],[100,0,140,10],[0,20,40,30],[100,20,140,30]],rec_scores=[.999]*4)
    class TwoTables(Replay):
        count=0
        def run(self,engine,**kwargs):
            if engine=='layout':
                return dict(version='test',model={},outputs=[dict(source=str(p),result={'boxes':[
                    dict(label='table',score=.99,coordinate=[0,0,200,95]),
                    dict(label='table',score=.99,coordinate=[0,100,200,199])]}) for p in kwargs['sources']])
            result=super().run(engine,**kwargs)
            if engine=='paddleocr' and kwargs.get('phase')=='table_verification':
                self.count+=1
                if self.count==2:result['outputs'][0]['result']['rec_scores'][-1]=.5
            return result
    source=tmp_path/'two.png';Image.new('RGB',(200,200),'white').save(source)
    result=run(source,tmp_path/'work',engines=TwoTables(prediction))
    analysis=result['document_analysis']
    assert analysis['review_required']
    tables=[b for b in analysis['blocks'] if b['engine']=='docling']
    assert tables[0]['status']=='succeeded' and tables[1]['status']=='review_required'
    asset=dict(id='test',name='test.png',kind='document',text=result['text'],parser_metadata={'document_analysis':analysis})
    parts=list(chunks_for_asset(asset))
    selected=select_table_parts(parts,tables[0]['id'])
    assert native_records(selected,['Item','Count'],[],10)[0]['Count']=='1'
    with pytest.raises(TableStructureError):native_records(parts,['Item','Count'],[],10)
    assert analysis['warnings']


@pytest.mark.parametrize('reply',[None,{}, {'outputs':None}, {'outputs':[None]},
    {'outputs':[{'source':'wrong','result':{}}]}])
def test_malformed_outer_optional_response_preserves_base(tmp_path,reply):
    class Broken(Replay):
        def run(self,engine,**kwargs):
            if kwargs.get('phase')=='table_verification':return reply
            return super().run(engine,**kwargs)
    source=tmp_path/'test.png';Image.new('RGB',(200,200),'white').save(source)
    result=run(source,tmp_path/'work',engines=Broken(None))
    assert result['parser_status']=='partial'
    assert 'BASE BODY' in result['text'] and 'BASE TABLE' in result['text']


def test_final_projection_limit_rolls_back_optional_repair_atomically(tmp_path,monkeypatch):
    from documents.analysis import pipeline
    from documents.analysis.contracts import AnalysisError
    from tests.documents.analysis.test_table_quality import simple
    data,prediction=simple();data['cells'].pop()
    real_project=pipeline.project
    def final_limit(result):
        if 'coverage' in result and any('table_ocr' in b['data'] for b in result['blocks']):
            raise AnalysisError('result_limit','final compatibility view limit')
        return real_project(result)
    monkeypatch.setattr(pipeline,'project',final_limit)
    source=tmp_path/'rollback.png';Image.new('RGB',(200,200),'white').save(source)
    result=run(source,tmp_path/'work',engines=Replay(prediction,data['cells']))
    table=next(b for b in result['document_analysis']['blocks'] if b['engine']=='docling')
    assert table['text']=='BASE TABLE' and table['data']['cells']==data['cells']
    assert 'original_cells' not in table['data']
    assert all('table_ocr' not in b['data'] for b in result['document_analysis']['blocks'])
    assert result['parser_status']=='partial' and 'table_verification:result_limit' in result['warnings']
