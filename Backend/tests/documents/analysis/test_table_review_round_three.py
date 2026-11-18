import pytest
from PIL import Image
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case, repeated_counts
from tests.documents.analysis.test_table_quality_boundaries import Replay
from tests.documents.analysis.test_table_row_boundaries import export


@pytest.mark.parametrize('mirror',[False,True])
@pytest.mark.parametrize('right',[150.001,215,280])
def test_unanchored_neighbor_cannot_be_treated_as_table_edge(tmp_path,mirror,right):
    data,prediction=review_case([['Item','Count',''],['Bread','1',''],['Milk',None,'']], [
        ('Item',[0,0,50,20]),('Count',[110,0,150,20]),('Bread',[0,30,50,50]),
        ('1',[110,30,130,50]),('Milk',[0,60,50,80]),('2 1200',[110,60,right,80])])
    if mirror:
        for cell in data['cells']:cell['column']=2-cell['column']
        for box in prediction['rec_boxes']:box[0],box[2]=300-box[2],300-box[0]
    cells,quality=reconcile(data,prediction)
    assert not quality['repairs'] and quality['issues'] and cells==data['cells']
    from workflow_engine.executors.document.table_rows import TableStructureError
    with pytest.raises(TableStructureError):export(data,prediction,tmp_path/'bad.csv')
    assert not (tmp_path/'bad.csv').exists()


@pytest.mark.parametrize('delta',[0,1,5])
def test_existing_repeated_count_allows_small_extent_difference(tmp_path,delta):
    data,prediction=repeated_counts([('1',[100,30,120,50]),('1',[100,60-delta,120,80+delta])])
    cells,quality=reconcile(data,prediction)
    assert not quality['issues'] and cells==data['cells']
    assert export(data,prediction,tmp_path/'normal.csv')==[
        {'Item':'Milk','Count':'1'},{'Item':'Bread','Count':'1'}]
    # The same box may verify an existing value but cannot invent a new one.
    if delta:
        data['cells'].pop()
        assert not reconcile(data,prediction)[1]['repairs']


def test_repeated_count_overlapping_another_row_remains_unconfirmed():
    data,prediction=repeated_counts([('1',[100,30,120,50]),('1',[100,49,120,80])])
    assert reconcile(data,prediction)[1]['issues']


@pytest.mark.parametrize('boundary',['analysis','compatibility'])
def test_body_details_roll_back_at_both_result_limits(tmp_path,monkeypatch,boundary):
    from documents.analysis import pipeline
    from documents.analysis.contracts import AnalysisError
    original=getattr(pipeline,'validate' if boundary=='analysis' else 'project')
    checks=[]
    def bounded(result):
        if 'coverage' in result:
            details=any('ocr_boxes' in b['data'] for b in result['blocks'])
            checks.append(details)
            if details:raise AnalysisError('result_limit','optional body details exceed limit')
        return original(result)
    monkeypatch.setattr(pipeline,'validate' if boundary=='analysis' else 'project',bounded)
    source=tmp_path/'body.png';Image.new('RGB',(200,200),'white').save(source)
    result=pipeline.run(source,tmp_path/'work',engines=Replay(None))
    blocks=result['document_analysis']['blocks']
    assert checks[0] and checks[-1] is False
    assert 'BASE BODY' in result['text'] and 'BASE TABLE' in result['text']
    assert 'body_ocr_details:result_limit' in result['warnings']
    body=next(b for b in blocks if b['engine']=='paddleocr')
    assert 'ocr_texts' not in body['data'] and 'ocr_boxes' not in body['data']
    assert 'ocr_scores' in body['data'] and 'crop_origin' in body['data']


def test_real_twelve_mib_limit_preserves_base_when_body_copies_overflow(tmp_path,monkeypatch):
    import json
    from documents.analysis import pipeline
    from documents.analysis.contracts import MAX_RESULT, validate
    real_project=pipeline.project
    padded=[]
    def at_limit(result):
        if 'coverage' in result and not padded:
            payload=real_project(result)
            size=len(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode())
            table=next(b for b in result['blocks'] if b['engine']=='docling')
            # Table data appears in both analysis and compatibility tables.
            table['data']['model_padding']='x'*((12*1024*1024-size)//2+100)
            assert len(json.dumps(result,ensure_ascii=False).encode()) < MAX_RESULT
            padded.append(True)
        return real_project(result)
    class ManyWords(Replay):
        layout_seen = 0
        def run(self,engine,**kwargs):
            out=super().run(engine,**kwargs)
            if engine=='docling':out['blocks'][0]['text']='BASE TABLE ' + 'x'*20000
            if engine=='layout':
                for item in out['outputs']:
                    if self.layout_seen:item['result']['boxes']=item['result']['boxes'][1:]
                    self.layout_seen+=1
            if engine=='paddleocr' and kwargs.get('phase')!='table_verification':
                for item in out['outputs']:
                    item['result']=dict(rec_texts=['body text']*450,rec_boxes=[[0,0,100,15]]*450,rec_scores=[.999]*450)
            return out
    monkeypatch.setattr(pipeline,'project',at_limit)
    source=tmp_path/'capacity.tiff'
    frames=[Image.new('RGB',(200,200),(255-i,255,255)) for i in range(15)]
    frames[0].save(source,save_all=True,append_images=frames[1:],compression='tiff_deflate')
    payload=pipeline.run(source,tmp_path/'work',engines=ManyWords(None))
    assert 'body_ocr_details:result_limit' in payload['warnings']
    assert 'body text' in payload['text'] and 'BASE TABLE' in payload['text']
    assert len(payload['tables'])==15
    validate(payload['document_analysis'])
    assert len(json.dumps(payload,ensure_ascii=False).encode())<=12*1024*1024
