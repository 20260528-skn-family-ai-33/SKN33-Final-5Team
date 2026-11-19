import csv
import json
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from documents.analysis.pipeline import run
from documents.resources.document_resources import chunks_for_asset,select_table_parts
from workflow_engine.executors.document.table_rows import native_records,TableStructureError
from workflow_engine.executors.output.export_result import execute
from tests.documents.analysis.test_table_quality_boundaries import Replay


def parsed(tmp_path, mode, bad_table=False):
    source=tmp_path/'receipt.png'; photo=Image.new('RGB',(200,200),'white'); draw=ImageDraw.Draw(photo)
    for y in (160,170,180): draw.text((5,y),'TOTAL 12000 ABCDEFG',fill='black')
    photo.save(source)
    table=dict(rec_texts=['Item','Count','Milk','1'],
               rec_boxes=[[0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    if bad_table: table['rec_scores'][-1]=.1
    class Body(Replay):
        def run(self,engine,**kw):
            result=super().run(engine,**kw)
            if engine=='layout':
                result['outputs'][0]['result']['boxes'][1]['coordinate']=[0,25,200,140]
                if mode=='partial_bands':
                    result['outputs'][0]['result']['boxes'].append(dict(label='text',score=.99,coordinate=[0,166,200,171]))
            if engine=='paddleocr' and kw.get('phase')!='table_verification':
                for output in result['outputs']:
                    if Path(output['source']).name.startswith('ocr-'):
                        body=dict(rec_texts=['OUTSIDE','TOTAL 12000'],
                                  rec_boxes=[[0,0,60,30],[0,480,300,495]],rec_scores=[.99,.99])
                        if mode=='nan': body['rec_boxes'][0][2]=float('nan')
                        if mode=='missing_score': body.pop('rec_scores')
                        if mode=='empty_score': body['rec_scores']=[]
                        if mode=='bad_score': body['rec_scores'][1]=float('inf')
                        if mode=='empty': body['rec_boxes']=[[0,0,float('nan'),30],[0,480,float('nan'),495]]
                        output['result']=body
            return result
    payload=run(source,tmp_path/'work',engines=Body(table))
    analysis=payload['document_analysis']; block=next(b for b in analysis['blocks'] if b['engine']=='docling')
    asset=dict(id='a',name='a.png',kind='document',text=payload['text'],parser_metadata={'document_analysis':analysis})
    return payload,asset,block


@pytest.mark.parametrize('mode',['normal','nan','missing_score','empty_score','bad_score','empty','partial_bands'])
@pytest.mark.parametrize('bad_table',[False,True])
def test_real_body_warning_does_not_block_independent_selected_table(tmp_path,mode,bad_table):
    payload,asset,table=parsed(tmp_path,mode,bad_table)
    analysis=payload['document_analysis']; parts=list(chunks_for_asset(asset))
    selected=select_table_parts(parts,table['id'])
    path=tmp_path/'result.csv'
    if bad_table:
        assert table['data']['table_quality']['issues']
        with pytest.raises(TableStructureError): native_records(selected,['Item','Count'],[],10)
        assert not path.exists()
    else:
        assert table['status']=='succeeded' and table['data']['table_quality']['issues']==[]
        assert not any(p['review_required'] for p in selected)
        records=native_records(selected,['Item','Count'],[],10)
        node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
                  bindings=[dict(input='records',node_id='extract',output='records')])
        exported=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
        path.write_text(exported['artifact']['content'],encoding='utf-8')
        with path.open(encoding='utf-8-sig') as stream:
            assert list(csv.DictReader(stream))==[{'Item':'Milk','Count':'1'}]
    if mode!='normal':
        assert payload['parser_status']=='partial' and analysis['review_required'] and payload['warnings']
        with pytest.raises(TableStructureError): native_records(parts,['Item','Count'],[],10)
        body_ids={b['id'] for b in analysis['blocks'] if b['engine']=='paddleocr'}
        for reason,scope in analysis['review_scopes'].items():
            if reason.startswith(('body_ocr_details:','low_confidence_uncovered_ocr:','empty_ocr_region:')):
                assert scope and set(scope)<=body_ids
    assert 'OUTSIDE' not in payload['text']
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('scope',['global','unknown','parent','table'])
def test_selected_table_still_obeys_global_unknown_and_table_parent_failures(tmp_path,scope):
    payload,asset,table=parsed(tmp_path,'nan')
    analysis=asset['parser_metadata']['document_analysis']
    analysis['warnings'].append('independent_failure');analysis['review_reasons'].append('independent_failure')
    if scope!='unknown':
        analysis['review_scopes']['independent_failure']=(None if scope=='global' else
            [table['parent_object_id']] if scope=='parent' else [table['id']])
    selected=select_table_parts(list(chunks_for_asset(asset)),table['id'])
    with pytest.raises(TableStructureError): native_records(selected,['Item','Count'],[],10)
