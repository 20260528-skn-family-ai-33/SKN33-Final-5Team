import copy
import json
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from documents.analysis.pipeline import run, body_ocr_prediction, ocr_in_bands
from documents.analysis.table_quality import validate_prediction
from documents.analysis.contracts import AnalysisError
from tests.documents.analysis.test_pipeline import Engines


def prediction(score):
    p = dict(rec_texts=['ALREADY READ STORE','DATE 2024-01-04','TOTAL 12,000'],
             rec_boxes=[[24,27,120,48],[24,462,300,490],[24,915,300,945]])
    if score != 'missing':
        p['rec_scores'] = {'normal':[.99,.98,.98], 'empty':[], 'null':None,
                          'nan':[.99,float('nan'),.98], 'short':[.99],
                          'partial':[.99,.98,.1]}[score]
    return p


@pytest.mark.parametrize('score', ['normal','missing','empty','null','nan','short','partial'])
@pytest.mark.parametrize('geometry', ['normal','nan','inf','mismatch'])
def test_uncovered_body_keeps_unscored_text_with_valid_geometry(tmp_path,score,geometry):
    source = tmp_path/'receipt.png'
    photo = Image.new('RGB',(250,384),'white'); draw = ImageDraw.Draw(photo)
    for y,text in [(9,'STORE'),(50,'ADDRESS'),(115,'RETURNS'),(154,'DATE 2024-01-04'),
                   (201,'1 ITEM 12,000'),(305,'TOTAL 12,000'),(330,'THANK YOU')]:
        draw.text((8,y),text,fill='black')
    photo.save(source)
    raw = prediction(score)
    if geometry in ('nan','inf'): raw['rec_boxes'][1][2] = float(geometry)
    if geometry == 'mismatch': raw['rec_boxes'].pop()
    class Receipt(Engines):
        def run(self,engine,**kw):
            result = super().run(engine,**kw)
            if engine == 'layout':
                result['outputs'][0]['result']['boxes'] = [dict(label='text',score=.98,coordinate=b)
                    for b in [[5,46,232,96],[13,4,239,39],[4,328,210,356],[5,109,162,137]]]
            if engine == 'paddleocr':
                for output in result['outputs']:
                    if Path(output['source']).name.startswith('ocr-'):
                        output['result'] = copy.deepcopy(raw)
            return result
    payload = run(source,tmp_path/'work',engines=Receipt())
    gap, = [b for b in payload['document_analysis']['blocks'] if b['data'].get('upright_y_bands')]
    assert 'ALREADY READ STORE' not in payload['text']
    assert '정산금엑 000017' in payload['text']
    if geometry == 'normal':
        assert gap['text'] == 'DATE 2024-01-04\nTOTAL 12,000'
    else:
        assert gap['text'] == ''
    unscored = score in ('missing','empty','null','nan','short')
    if geometry != 'normal' or unscored:
        assert payload['parser_status'] == 'partial'
        assert any(w.startswith('low_confidence_uncovered_ocr:') for w in payload['warnings'])
    else:
        assert payload['parser_status'] == 'succeeded'
    json.dumps(payload,allow_nan=False,ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('score', ['normal','missing','empty','null','nan','short','partial'])
def test_score_independent_geometry_and_multiple_band_coverage(score):
    raw = prediction(score)
    safe, invalid = body_ocr_prediction(raw)
    assert safe['rec_boxes'] == raw['rec_boxes'] and safe['rec_texts'] == raw['rec_texts']
    assert invalid is (score in ('null','nan','short'))
    selected, unread = ocr_in_bands(safe,[(138,200),(300,328)],3)
    assert selected['rec_texts'] == ['DATE 2024-01-04','TOTAL 12,000']
    expected = [] if score == 'normal' else [(300,328)] if score == 'partial' else [(138,200),(300,328)]
    assert unread == expected
    if score != 'normal' and score != 'partial':
        with pytest.raises(AnalysisError): validate_prediction(raw)


@pytest.mark.parametrize('score', ['normal','missing','empty','null','nan','short','partial'])
def test_body_without_bands_preserves_text_and_normal_table(tmp_path,score):
    from tests.documents.analysis.test_table_quality_boundaries import Replay
    table = dict(rec_texts=['Item','Count','Milk','1'],
                 rec_boxes=[[0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    class Body(Replay):
        def run(self,engine,**kw):
            result = super().run(engine,**kw)
            if engine == 'paddleocr' and kw.get('phase') != 'table_verification':
                for output in result['outputs']: output['result'] = prediction(score)
            return result
    source = tmp_path/'body.png'; Image.new('RGB',(200,200),'white').save(source)
    payload = run(source,tmp_path/'work',engines=Body(table))
    for text in ['BASE TABLE','ALREADY READ STORE','DATE 2024-01-04','TOTAL 12,000']:
        assert text in payload['text']
    assert payload['parser_status'] == ('partial' if score in ('null','nan','short') else 'succeeded')
    json.dumps(payload,allow_nan=False,ensure_ascii=False).encode('utf-8')
