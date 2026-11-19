"""Docling response validation before UUID use, persistence or block publication."""
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run
from documents.resources.document_resources import chunks_for_asset, select_table_parts
from workflow_engine.executors.document.table_rows import native_records
from workflow_engine.executors.output.export_result import execute
from tests.documents.analysis.test_table_quality_boundaries import Replay

JOB='12345678-1234-1234-1234-123456789abc'
TABLE=dict(rec_texts=['Item','Count','Milk','1'],rec_boxes=[
    [0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
BAD_VALUES=[None,[],{},'',0,True,'bad-uuid']


def harness(tmp_path,monkeypatch,route,mutate,submission=None,pending=None,two_tables=False):
    calls=[]; submitted=[]
    def result():
        value=Replay(TABLE).run('docling'); value['model']={'device':'cuda'}
        value['source_sha256']=hashlib.sha256(submitted[-1]).hexdigest() if submitted else 'unused'
        return mutate(value)
    def remote(request,**kw):
        method=request.get_method();calls.append(method)
        if method=='POST':
            submitted.append(request.data);data={'job_id':JOB} if submission is None else submission();status=202
        elif method=='DELETE':data={};status=200
        elif pending is not None and calls.count('GET')==1:data=pending();status=202
        else:data=result();status=200
        stream=io.BytesIO(json.dumps(data).encode());stream.status=status
        return stream
    def cpu(command,**kw):
        Path(command[-1]).write_text(json.dumps(result()),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    class Engines(LocalEngines):
        def run(self,engine,**kw):
            if engine!='docling':
                data=Replay(TABLE).run(engine,**kw)
                if engine=='layout' and two_tables:
                    boxes=data['outputs'][0]['result']['boxes']
                    boxes[1]['coordinate']=[0,25,200,95]
                    boxes.append(dict(label='table',score=.99,coordinate=[0,105,200,195]))
                return data
            return result() if route=='direct' else super().run(engine,**kw)
    monkeypatch.delenv('A4B_PARSER_URL',raising=False)
    if route=='remote':monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_API_KEY','test')
    monkeypatch.delenv('A4B_DOCLING_LOCAL_ONLY',raising=False)
    monkeypatch.setattr('documents.analysis.remote_docling.urllib.request.build_opener',lambda *a:SimpleNamespace(open=remote))
    monkeypatch.setattr('documents.analysis.adapters.subprocess.run',cpu)
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    engines=Engines(tmp_path/'work');payload=run(source,tmp_path/'work',engines=engines)
    assert 'BASE BODY' in payload['text']
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
    return payload,engines,calls


@pytest.mark.parametrize('value',BAD_VALUES)
@pytest.mark.parametrize('shape',['top','job_id'])
def test_invalid_acceptance_preserves_body_without_using_invalid_id(tmp_path,monkeypatch,value,shape):
    response=value if shape=='top' else {'job_id':value}
    payload,engines,calls=harness(tmp_path,monkeypatch,'remote',lambda v:v,submission=lambda:response)
    assert calls==['POST']
    assert payload['parser_status']=='partial' and 'image_table:invalid_engine_result' in payload['warnings']
    assert engines.attempts[0]['error_code']=='invalid_engine_result'
    assert not any(b['engine']=='docling' for b in payload['document_analysis']['blocks'])


@pytest.mark.parametrize('value',[None,[],0,True,'running'])
def test_invalid_pending_response_cancels_accepted_job(tmp_path,monkeypatch,value):
    payload,engines,calls=harness(tmp_path,monkeypatch,'remote',lambda v:v,pending=lambda:value)
    assert calls==['POST','GET','DELETE']
    assert 'image_table:invalid_engine_result' in payload['warnings']


MODES=['normal','top_null','top_list','engine','version_null','version_surrogate','model_null','model_nan',
 'blocks_null','blocks_dict','block_null','kind_missing','kind_list','text_null','text_surrogate',
 'location_null','data_null','bbox_number','bbox_short','bbox_nan','bbox_huge','bbox_reverse','space_null',
 'cells_null','cells_dict','cell_null','nested_nan','partial_string','second_bad_block']

def changed(value,mode):
    if mode=='top_null':return None
    if mode=='top_list':return []
    if mode=='engine':value['engine']='wrong'
    if mode=='version_null':value['version']=None
    if mode=='version_surrogate':value['version']=chr(0xD800)
    if mode=='model_null':value['model']=None
    if mode=='model_nan':value['model']['score']=float('nan')
    if mode=='blocks_null':value['blocks']=None
    if mode=='blocks_dict':value['blocks']={}
    if mode=='block_null':value['blocks']=[None]
    if mode=='partial_string':value['partial']='false'
    if mode in ('blocks_null','blocks_dict','block_null'):return value
    b=value['blocks'][0]
    if mode=='kind_missing':b.pop('kind')
    if mode=='kind_list':b['kind']=[]
    if mode=='text_null':b['text']=None
    if mode=='text_surrogate':b['text']=chr(0xD800)
    if mode=='location_null':b['location']=None
    if mode=='data_null':b['data']=None
    if mode.startswith('bbox_'):
        b['bbox']={'bbox_number':1,'bbox_short':[1], 'bbox_nan':[0,0,float('nan'),1],
          'bbox_huge':[0,0,10**400,1],'bbox_reverse':[2,0,1,1]}[mode]
    if mode=='space_null':b['coordinate_system']=None
    if mode=='cells_null':b['data']['cells']=None
    if mode=='cells_dict':b['data']['cells']={}
    if mode=='cell_null':b['data']['cells']=[None]
    if mode=='nested_nan':b['data']['other']={'score':float('nan')}
    if mode=='second_bad_block':value['blocks'].append({'kind':'text','text':None})
    return value


@pytest.mark.parametrize('route',['remote','local','direct'])
@pytest.mark.parametrize('mode',MODES)
def test_result_contract_preserves_body_and_is_atomic(tmp_path,monkeypatch,route,mode):
    payload,engines,calls=harness(tmp_path,monkeypatch,route,lambda v:changed(v,mode))
    blocks=payload['document_analysis']['blocks']
    if mode=='normal':
        assert payload['parser_status']=='succeeded'
        table=next(b for b in blocks if b['engine']=='docling')
        asset=dict(id='a',name='a.png',kind='document',text=payload['text'],parser_metadata={'document_analysis':payload['document_analysis']})
        selected=select_table_parts(list(chunks_for_asset(asset)),table['id'])
        records=native_records(selected,['Item','Count'],[],10)
        node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
                  bindings=[dict(input='records',node_id='extract',output='records')])
        exported=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
        path=tmp_path/'out.csv';path.write_text(exported['artifact']['content'],encoding='utf-8')
        with path.open(encoding='utf-8-sig') as stream:assert list(csv.DictReader(stream))==[{'Item':'Milk','Count':'1'}]
        if route=='remote':assert calls==['POST','GET']
    else:
        assert payload['parser_status']=='partial'
        assert not any(b['engine']=='docling' for b in blocks)
        assert any(w.startswith('image_table:invalid_engine_') for w in payload['warnings'])
        if route!='direct':
            assert engines.attempts[0]['status']=='failed'
            assert 'version' not in engines.attempts[0] and 'model' not in engines.attempts[0]
        if route=='remote':
            assert calls==['POST','GET','DELETE']
            assert not list((tmp_path/'work').rglob('response.json'))


@pytest.mark.parametrize('field,value',[('source_sha256','wrong'),('source_sha256',None),('model',{'device':'cpu'}),('model',{}),('engine',None)])
def test_remote_binding_and_device_are_required(tmp_path,monkeypatch,field,value):
    def mutate(data):data[field]=value;return data
    payload,_,calls=harness(tmp_path,monkeypatch,'remote',mutate)
    assert payload['parser_status']=='partial' and calls==['POST','GET','DELETE']


def test_valid_pending_object_then_success(tmp_path,monkeypatch):
    monkeypatch.setattr('documents.analysis.remote_docling.time.sleep',lambda _:None)
    payload,_,calls=harness(tmp_path,monkeypatch,'remote',lambda v:v,pending=lambda:{})
    assert payload['parser_status']=='succeeded' and calls==['POST','GET','GET']


@pytest.mark.parametrize('route',['remote','local','direct'])
def test_later_bad_response_keeps_prior_verified_table(tmp_path,monkeypatch,route):
    count=[]
    def mutate(value):
        count.append(1)
        return value if len(count)==1 else changed(value,'second_bad_block')
    payload,_,calls=harness(tmp_path,monkeypatch,route,mutate,two_tables=True)
    tables=[b for b in payload['document_analysis']['blocks'] if b['engine']=='docling']
    assert len(count)==2 and len(tables)==1
    assert payload['parser_status']=='partial' and tables[0]['status']=='succeeded'
    assert tables[0]['data']['table_quality']['issues']==[]
    assert 'BASE BODY' in payload['text'] and 'BASE TABLE' in payload['text']
    if route=='remote':assert calls==['POST','GET','POST','GET','DELETE']
