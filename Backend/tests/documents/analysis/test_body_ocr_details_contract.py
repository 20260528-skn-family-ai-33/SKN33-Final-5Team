import copy
import json
import pytest
from PIL import Image
from documents.analysis.pipeline import run
from tests.documents.analysis.test_table_quality_boundaries import Replay


@pytest.mark.parametrize('kind', ['normal', 'nan', 'inf', 'negative_inf', 'huge', 'null_boxes',
    'string_boxes', 'short_box', 'reverse_box', 'bool_coordinate', 'missing_box', 'extra_box',
    'null_scores', 'nan_score', 'huge_score', 'string_score', 'extra_score', 'bad_text',
    'surrogate', 'missing_geometry', 'null_result'])
@pytest.mark.parametrize('with_table_verification', [False, True])
def test_body_details_fail_locally_without_losing_document(tmp_path, kind, with_table_verification):
    body = dict(rec_texts=['BASE BODY'], rec_boxes=[[0,0,100,15]], rec_scores=[.999])
    if kind in ('nan','inf','negative_inf','huge'):
        body['rec_boxes'][0][2] = {'nan':float('nan'),'inf':float('inf'),
            'negative_inf':float('-inf'),'huge':10**400}[kind]
    if kind == 'null_boxes': body['rec_boxes'] = None
    if kind == 'string_boxes': body['rec_boxes'] = 'bad'
    if kind == 'short_box': body['rec_boxes'] = [[0,0,100]]
    if kind == 'reverse_box': body['rec_boxes'] = [[100,0,0,15]]
    if kind == 'bool_coordinate': body['rec_boxes'][0][0] = False
    if kind == 'missing_box': body['rec_boxes'] = []
    if kind == 'extra_box': body['rec_boxes'].append([0,0,100,15])
    if kind == 'null_scores': body['rec_scores'] = None
    if kind == 'nan_score': body['rec_scores'] = [float('nan')]
    if kind == 'huge_score': body['rec_scores'] = [10**400]
    if kind == 'string_score': body['rec_scores'] = ['.999']
    if kind == 'extra_score': body['rec_scores'].append(.999)
    if kind == 'bad_text': body['rec_texts'].append(None)
    if kind == 'surrogate': body['rec_texts'].append(chr(0xD800))
    if kind == 'missing_geometry': del body['rec_boxes']
    if kind == 'null_result': body = None
    table_ocr = dict(rec_texts=['Item','Count','Milk','1'],
        rec_boxes=[[0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    class Body(Replay):
        def run(self, engine, **kw):
            result = super().run(engine, **kw)
            if engine == 'paddleocr' and kw.get('phase') != 'table_verification':
                for output in result['outputs']: output['result'] = copy.deepcopy(body)
            if engine == 'docling' and not with_table_verification:
                # A text-only base result exercises final serialization without
                # the intermediate table-verification size check.
                result['blocks'][0].update(kind='text', data={})
            return result
    source = tmp_path/'test.png'; Image.new('RGB',(200,200),'white').save(source)
    payload = run(source,tmp_path/'work',engines=Body(table_ocr))
    analysis = payload['document_analysis']
    assert 'BASE TABLE' in payload['text']
    if kind != 'null_result': assert 'BASE BODY' in payload['text']
    item = next(b for b in analysis['blocks'] if b['engine'] == 'paddleocr')
    invalid = kind not in ('normal','missing_geometry')
    warning = 'body_ocr_details:invalid_engine_result'
    assert (warning in payload['warnings']) is invalid
    if invalid:
        assert payload['parser_status'] == 'partial'
        assert item['id'] in analysis['review_scopes'][warning]
        assert 'ocr_boxes' not in item['data'] and 'ocr_texts' not in item['data']
    elif kind == 'normal':
        assert item['data']['ocr_boxes'] == body['rec_boxes']
        assert item['data']['ocr_texts'] == body['rec_texts']
    if with_table_verification:
        table = next(b for b in analysis['blocks'] if b['engine'] == 'docling')
        assert len(table['data']['cells']) == 4 and table['data']['table_quality']['issues'] == []
        if not invalid: assert payload['parser_status'] == 'succeeded'
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
