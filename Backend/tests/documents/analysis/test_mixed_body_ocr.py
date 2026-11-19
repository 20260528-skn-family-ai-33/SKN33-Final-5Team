import copy
import json
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from documents.analysis.pipeline import run, body_ocr_prediction, ocr_in_bands
from tests.documents.analysis.test_unscored_body_bands import prediction
from tests.documents.analysis.test_pipeline import Engines


@pytest.mark.parametrize('bad_index', [0,1,2])
@pytest.mark.parametrize('kind', ['nan','inf','negative_inf','reverse','text','surrogate','score'])
@pytest.mark.parametrize('reverse', [False,True])
def test_mixed_pieces_preserve_other_bands_without_reassigning_evidence(tmp_path,bad_index,kind,reverse):
    source = tmp_path/'receipt.png'; photo = Image.new('RGB',(250,384),'white')
    draw = ImageDraw.Draw(photo)
    for y,text in [(9,'STORE'),(50,'ADDRESS'),(115,'RETURNS'),(154,'DATE 2024-01-04'),
                   (201,'1 ITEM 12,000'),(305,'TOTAL 12,000'),(330,'THANK YOU')]:
        draw.text((8,y),text,fill='black')
    photo.save(source)
    raw = prediction('normal')
    if kind in ('nan','inf','negative_inf'):
        raw['rec_boxes'][bad_index][2] = float('-inf' if kind=='negative_inf' else kind)
    if kind == 'reverse':
        b = raw['rec_boxes'][bad_index]; b[0],b[2] = b[2],b[0]
    if kind == 'text': raw['rec_texts'][bad_index] = None
    if kind == 'surrogate': raw['rec_texts'][bad_index] = chr(0xD800)
    if kind == 'score': raw['rec_scores'][bad_index] = float('nan')
    expected = [text for i,text in enumerate(prediction('normal')['rec_texts'])
                if i > 0 and (i != bad_index or kind == 'score')]
    if reverse: raw = {key:list(reversed(values)) for key,values in raw.items()}
    class Receipt(Engines):
        def run(self,engine,**kw):
            result = super().run(engine,**kw)
            if engine == 'layout':
                result['outputs'][0]['result']['boxes'] = [dict(label='text',score=.98,coordinate=b)
                    for b in [[5,46,232,96],[13,4,239,39],[4,328,210,356],[5,109,162,137],[0,210,249,235]]]
            if engine == 'paddleocr':
                for output in result['outputs']:
                    if Path(output['source']).name.startswith('ocr-'): output['result'] = copy.deepcopy(raw)
            return result
    result = run(source,tmp_path/'work',engines=Receipt())
    analysis = result['document_analysis']
    gap, = [b for b in analysis['blocks'] if b['data'].get('upright_y_bands')]
    assert gap['text'] == '\n'.join(expected)
    assert 'ALREADY READ STORE' not in result['text']
    assert '정산금엑 000017' in result['text']
    assert result['parser_status'] == 'partial'
    assert gap['data']['unverified_y_bands'] == gap['data']['upright_y_bands']
    assert gap['id'] in analysis['review_scopes']['body_ocr_details:invalid_engine_result']
    assert 'ocr_boxes' not in gap['data']
    json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('score', ['normal','missing','empty','short','nan'])
def test_filtering_keeps_original_index_scores_and_never_zips_mismatched_arrays(score):
    raw = prediction(score)
    raw['rec_texts'][0] = None
    raw['rec_scores'] = [.01,.83,.97] if score=='normal' else raw.get('rec_scores',[])
    safe, invalid = body_ocr_prediction(raw,require_geometry=True)
    assert invalid
    assert safe['rec_texts'] == ['DATE 2024-01-04','TOTAL 12,000']
    assert safe['rec_boxes'] == raw['rec_boxes'][1:]
    assert safe['rec_scores'] == ([.83,.97] if score=='normal' else [])
    raw['rec_boxes'].pop()
    safe, invalid = body_ocr_prediction(raw,require_geometry=True)
    selected, unread = ocr_in_bands(safe,[(138,210),(236,328)],3)
    assert invalid and not selected['rec_texts'] and len(unread)==2
