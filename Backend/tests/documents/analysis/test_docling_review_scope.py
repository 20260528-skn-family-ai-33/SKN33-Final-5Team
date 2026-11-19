"""Warnings retain document review while allowing independent selected tables."""
import copy
import csv
import pytest
from documents.analysis.pipeline import run
from documents.analysis.contracts import AnalysisError
from documents.resources.document_resources import chunks_for_asset,select_table_parts
from workflow_engine.executors.document.table_rows import native_records,TableStructureError
from workflow_engine.executors.output.export_result import execute
from tests.documents.analysis.test_docling_response_contract import harness,changed,TABLE
from tests.documents.analysis.test_table_quality_boundaries import Replay


def asset_for(payload):
    return dict(id='a',name='a.png',kind='document',text=payload['text'],
                parser_metadata={'document_analysis':payload['document_analysis']})


def export_selected(tmp_path,asset,table):
    selected=select_table_parts(list(chunks_for_asset(asset)),table['id'])
    records=native_records(selected,['Item','Count'],[],10)
    node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
              bindings=[dict(input='records',node_id='extract',output='records')])
    exported=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path=tmp_path/'out.csv';path.write_text(exported['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:
        assert list(csv.DictReader(stream))==[{'Item':'Milk','Count':'1'}]


@pytest.mark.parametrize('route',['remote','local','direct'])
@pytest.mark.parametrize('bad_index',[0,1])
@pytest.mark.parametrize('mode',['second_bad_block','missing','partial','comparison_bad','comparison_partial'])
def test_crop_failure_does_not_block_independent_selected_csv(tmp_path,monkeypatch,route,bad_index,mode):
    calls=[]
    def mutate(value):
        index=len(calls);calls.append(index)
        if index==bad_index:
            if mode=='second_bad_block':return changed(value,mode)
            if mode=='partial':value['partial']=True
            else:value['blocks'][0]['data']['cells']=[]
        elif index>=2:
            if mode=='comparison_bad':return changed(value,'second_bad_block')
            if mode=='comparison_partial':value['partial']=True
            else:value['blocks'][0]['data']['cells']=[]
        return value
    payload,_,_=harness(tmp_path,monkeypatch,route,mutate,two_tables=True)
    analysis=payload['document_analysis'];asset=asset_for(payload)
    regions=[b for b in analysis['blocks'] if b['engine']=='layout' and 'table' in b['content_types']]
    good=next(b for b in analysis['blocks'] if b['engine']=='docling' and b['parent_object_id']==regions[1-bad_index]['id'])
    assert good['status']=='succeeded' and good['data']['table_quality']['issues']==[]
    assert payload['parser_status']=='partial' and analysis['review_required'] and payload['warnings']
    export_selected(tmp_path,asset,good)
    parts=list(chunks_for_asset(asset))
    with pytest.raises(TableStructureError):native_records(parts,['Item','Count'],[],10)
    bad_parts=[p for p in parts if p.get('block_id')!=good['id'] and p.get('table_cells') is not None]
    assert bad_parts
    for part in bad_parts:
        with pytest.raises(TableStructureError):
            native_records(select_table_parts(parts,part['block_id']),['Item','Count'],[],10)
    assert all(scope for scope in analysis['review_scopes'].values())


@pytest.mark.parametrize('failure',['global','unknown','parent','table'])
def test_other_table_failure_never_erases_real_blockers(tmp_path,monkeypatch,failure):
    count=[]
    def mutate(value):count.append(1);return value if len(count)==1 else changed(value,'second_bad_block')
    payload,_,_=harness(tmp_path,monkeypatch,'direct',mutate,two_tables=True)
    analysis=payload['document_analysis'];good=next(b for b in analysis['blocks'] if b['engine']=='docling')
    analysis['warnings'].append('extra_error');analysis['review_reasons'].append('extra_error')
    if failure!='unknown':analysis['review_scopes']['extra_error']=None if failure=='global' else [good['id'] if failure=='table' else good['parent_object_id']]
    with pytest.raises(TableStructureError):
        native_records(select_table_parts(list(chunks_for_asset(asset_for(payload))),good['id']),['Item','Count'],[],10)


def test_same_error_collects_all_failed_regions(tmp_path,monkeypatch):
    payload,_,_=harness(tmp_path,monkeypatch,'direct',lambda v:changed(v,'second_bad_block'),two_tables=True)
    analysis=payload['document_analysis']
    regions={b['id'] for b in analysis['blocks'] if b['engine']=='layout' and 'table' in b['content_types']}
    assert set(analysis['review_scopes']['image_table:invalid_engine_result'])==regions


@pytest.mark.parametrize('mode',['error','missing','partial','limit'])
def test_pdf_comparison_scopes_are_limited_to_incomplete_tables(tmp_path,mode):
    import fitz
    source=tmp_path/'input.pdf'
    with fitz.open() as doc:
        for _ in range(2):doc.new_page().insert_text((20,30),'Readable document body with enough ordinary text to avoid scan fallback.')
        doc.save(source)
    class Engines(Replay):
        def run(self,engine,**kw):
            if kw.get('phase','primary')=='primary':
                result=super().run('docling',**kw)
                good=result['blocks'][0];good['location']={'page':1}
                bad=copy.deepcopy(good);bad['location']={'page':2};bad['data']['cells']=[]
                result['blocks'].append(bad)
                return result
            if kw.get('phase')=='comparison':
                if mode=='error':raise AnalysisError('invalid_engine_result','bad response')
                result=super().run('docling',**kw)
                if mode=='missing':result['blocks'][0]['data']['cells']=[]
                if mode=='partial':result['partial']=True
                return result
            return super().run(engine,**kw)
    payload=run(source,tmp_path/'work',engines=Engines(TABLE),pdf_primary='docling',max_supplements=0 if mode=='limit' else 16)
    analysis=payload['document_analysis']
    good=next(b for b in analysis['blocks'] if b['engine']=='docling' and b['native_location'].get('page')==1)
    bad=next(b for b in analysis['blocks'] if b['engine']=='docling' and b['native_location'].get('page')==2)
    assert analysis['review_scopes']['primary_table_structure_incomplete']==[bad['id']]
    export_selected(tmp_path,asset_for(payload),good)
    with pytest.raises(TableStructureError):native_records(select_table_parts(list(chunks_for_asset(asset_for(payload))),bad['id']),['Item','Count'],[],10)


@pytest.mark.parametrize('budget',[2,3])
def test_region_and_comparison_limits_do_not_block_good_table(tmp_path,monkeypatch,budget):
    from tests.documents.analysis import test_docling_response_contract as fixture
    original=fixture.run
    monkeypatch.setattr(fixture,'run',lambda *a,**kw:original(*a,max_supplements=budget,**kw))
    calls=[]
    def mutate(value):
        calls.append(1)
        if len(calls)>1:value['blocks'][0]['data']['cells']=[]
        return value
    payload,_,_=harness(tmp_path,monkeypatch,'direct',mutate,two_tables=True)
    analysis=payload['document_analysis'];good=next(b for b in analysis['blocks'] if b['engine']=='docling')
    assert analysis['review_scopes']['supplement_limit_reached']
    export_selected(tmp_path,asset_for(payload),good)


def test_existing_global_code_is_not_narrowed_by_crop_failure(tmp_path,monkeypatch):
    from documents.analysis.readers import Inspector
    original=Inspector.run
    def inspect(self,*a,**kw):
        value=original(self,*a,**kw)
        value['warnings'].append('image_table:invalid_engine_result')
        return value
    monkeypatch.setattr(Inspector,'run',inspect)
    calls=[]
    def mutate(value):calls.append(1);return value if len(calls)==1 else changed(value,'second_bad_block')
    payload,_,_=harness(tmp_path,monkeypatch,'direct',mutate,two_tables=True)
    analysis=payload['document_analysis']
    assert analysis['review_scopes']['image_table:invalid_engine_result'] is None
    good=next(b for b in analysis['blocks'] if b['engine']=='docling')
    with pytest.raises(TableStructureError):export_selected(tmp_path,asset_for(payload),good)
