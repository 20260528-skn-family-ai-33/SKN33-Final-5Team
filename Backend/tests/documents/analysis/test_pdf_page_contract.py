"""Validate source PDF page references atomically, without remapping guesses."""
import copy
import json
import pytest
from documents.analysis.pipeline import run
from documents.analysis.contracts import AnalysisError
from tests.documents.analysis.test_table_quality_boundaries import Replay
from tests.documents.analysis.test_docling_response_contract import TABLE,harness
from tests.documents.analysis.test_docling_review_scope import export_selected,asset_for

TEXT='Readable native document text with enough ordinary characters to retain the original body.'
LOCATIONS=[{}, {'page':None},{'page':0},{'page':-1},{'page':999},{'page':'2'},
           {'page':2.0},{'page':True},{'page':False},{'page':[2]},{'page':{}},None,{'page':1},{'page':2}]


def pdf_source(tmp_path):
    import pymupdf as fitz
    source=tmp_path/'input.pdf'
    with fitz.open() as doc:
        for _ in range(2):doc.new_page().insert_text((20,30),TEXT)
        doc.save(source)
    return source


@pytest.mark.parametrize('route',['docling','mineru_fallback','mineru'])
@pytest.mark.parametrize('location',LOCATIONS)
def test_pdf_page_reference_is_checked_before_publishing_any_block(tmp_path,route,location):
    source=pdf_source(tmp_path)
    class Engines(Replay):
        def run(self,engine,**kw):
            if engine=='mineru' and route=='mineru_fallback':raise AnalysisError('engine_error','fallback requested')
            if kw.get('phase','primary') in ('primary','fallback'):
                data=super().run('docling',**kw);data['engine']=engine
                data['blocks'][0]['location']={'page':2 if location=={'page':1} else 1}
                second=copy.deepcopy(data['blocks'][0]);second['location']=location
                data['blocks'].append(second)
                return data
            return super().run(engine,**kw)
    payload=run(source,tmp_path/'work',engines=Engines(TABLE),pdf_primary='docling' if route=='docling' else 'mineru')
    analysis=payload['document_analysis']
    valid=isinstance(location,dict) and type(location.get('page')) is int and location['page'] in (1,2)
    parsed=[b for b in analysis['blocks'] if b['id'].startswith('parsed-')]
    if valid:
        assert len(parsed)==2 and parsed[1]['unit_id']=='page-'+str(location['page'])
        assert parsed[1]['native_location']==location
        if route!='mineru_fallback':
            assert payload['parser_status']=='succeeded'
            export_selected(tmp_path,asset_for(payload),parsed[1])
    else:
        assert payload['parser_status']=='partial' and analysis['fallback_processor']=='native'
        assert not parsed
        assert TEXT in payload['text']
        assert any(w.endswith(':invalid_engine_result') for w in payload['warnings'])
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')


def test_one_page_comparison_maps_local_page_one_to_source_page_two(tmp_path):
    source=pdf_source(tmp_path)
    class Engines(Replay):
        def run(self,engine,**kw):
            data=super().run(engine,**kw)
            if engine=='docling':
                if kw.get('phase')=='comparison':data['blocks'][0]['location']={'page':1}
                else:
                    data['blocks'][0]['location']={'page':1}
                    second=copy.deepcopy(data['blocks'][0]);second['location']={'page':2};second['data']['cells']=[]
                    data['blocks'].append(second)
            return data
    payload=run(source,tmp_path/'work',engines=Engines(TABLE),pdf_primary='docling')
    blocks=payload['document_analysis']['blocks']
    compared=next(b for b in blocks if b.get('phase')=='comparison')
    assert compared['unit_id']=='page-2' and compared['native_location']=={'page':2}
    assert compared['selected'] is False
    good=next(b for b in blocks if b.get('phase')=='primary' and b['native_location']=={'page':1})
    export_selected(tmp_path,asset_for(payload),good)


@pytest.mark.parametrize('page',[1,999])
def test_office_reported_page_is_not_a_physical_pdf_page(tmp_path,page):
    from docx import Document
    source=tmp_path/'input.docx';document=Document();document.add_paragraph(TEXT);document.save(source)
    class Engines(Replay):
        def run(self,engine,**kw):
            data=super().run(engine,**kw)
            if engine=='docling':data['blocks'][0]['location']={'page':page}
            return data
    payload=run(source,tmp_path/'work',engines=Engines(TABLE))
    parsed=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert parsed['native_location']['parser_reported_page']==page and 'page' not in parsed['native_location']
    assert parsed['unit_id'] in {u['id'] for u in payload['document_analysis']['units']}
    assert TEXT in payload['text']
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('route',['remote','local','direct'])
def test_image_crop_reported_page_does_not_replace_original_location(tmp_path,monkeypatch,route):
    def mutate(data):data['blocks'][0]['location']={'page':1};return data
    payload,_,_=harness(tmp_path,monkeypatch,route,mutate)
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert 'page' not in table['native_location']
    assert payload['parser_status']=='succeeded'
    export_selected(tmp_path,asset_for(payload),table)
