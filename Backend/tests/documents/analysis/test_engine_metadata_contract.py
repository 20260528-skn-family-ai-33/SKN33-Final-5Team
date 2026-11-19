import csv
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run
from documents.resources.document_resources import chunks_for_asset,select_table_parts
from workflow_engine.executors.document.table_rows import native_records,TableStructureError
from workflow_engine.executors.output.export_result import execute
from tests.documents.analysis.test_table_quality_boundaries import Replay
from tests.documents.analysis.test_merged_repeats_and_ocr_contract import Response


@pytest.mark.parametrize('route',['direct','remote','local'])
@pytest.mark.parametrize('target',['supplement','table_verification'])
@pytest.mark.parametrize('mode',['normal','unicode','missing_result','version_surrogate','model_surrogate',
    'model_nan','model_inf','nested_negative_inf','nested_surrogate_key','model_null','model_list'])
def test_metadata_is_validated_before_attempt_or_block_storage(tmp_path,monkeypatch,route,target,mode):
    phase=[]
    table=dict(rec_texts=['Item','Count','Milk','1'],rec_boxes=[
        [0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    def response(names):
        data=dict(version='test',model={'name':'model'},outputs=[dict(source=name,result=table
            if phase[-1]=='table_verification' else dict(rec_texts=['BODY'],rec_boxes=[[0,0,30,15]],rec_scores=[.99]))
            for name in names])
        if phase[-1]==target:
            if mode=='unicode':data.update(version='한글😀',model={'name':'모델😀','details':[{'enabled':True,'value':None}]})
            if mode=='missing_result':data['outputs'][0].pop('result')
            if mode=='version_surrogate':data['version']=chr(0xD800)
            if mode=='model_surrogate':data['model']={'nested':[{'name':chr(0xDFFF)}]}
            if mode=='model_nan':data['model']={'score':float('nan')}
            if mode=='model_inf':data['model']={'score':float('inf')}
            if mode=='nested_negative_inf':data['model']={'nested':[{'score':float('-inf')}]}
            if mode=='nested_surrogate_key':data['model']={'nested':{chr(0xD800):'value'}}
            if mode=='model_null':data['model']=None
            if mode=='model_list':data['model']=[]
        return data
    class Engines(LocalEngines):
        def run(self,engine,**kw):
            phase.append(kw.get('phase'))
            if engine in ('layout','docling'):return Replay(table).run(engine,**kw)
            return response([str(p) for p in kw['sources']]) if route=='direct' else super().run(engine,**kw)
    def urlopen(request,timeout):
        return Response(response([s['name'] for s in json.loads(request.data)['sources']]))
    def cpu(command,**kw):
        request=json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        Path(command[-1]).write_text(json.dumps(response(request['sources'])),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.delenv('A4B_PARSER_URL',raising=False)
    if route=='remote':monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','off')
    monkeypatch.setattr('documents.analysis.adapters.urllib.request.urlopen',urlopen)
    monkeypatch.setattr('documents.analysis.adapters.subprocess.run',cpu)
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    engines=Engines(tmp_path/'work');payload=run(source,tmp_path/'work',engines=engines)
    analysis=payload['document_analysis'];block=next(b for b in analysis['blocks'] if b['engine']=='docling')
    invalid=mode not in ('normal','unicode','missing_result')
    assert 'BASE TABLE' in payload['text']
    if target=='table_verification' or mode in ('normal','unicode'):assert 'BODY' in payload['text']
    assert payload['parser_status']==('succeeded' if mode in ('normal','unicode') else 'partial')
    if invalid:
        expected='image_ocr:invalid_engine_metadata' if target=='supplement' else 'table_verification:invalid_engine_metadata'
        assert expected in payload['warnings']
        for attempt in engines.attempts:
            if attempt['phase']==target:
                assert attempt['status']=='failed' and 'version' not in attempt and 'model' not in attempt
        assert all(b.get('parser_version')!='\ud800' for b in analysis['blocks'])
    asset=dict(id='a',name='a.png',kind='document',text=payload['text'],parser_metadata={'document_analysis':analysis})
    selected=select_table_parts(list(chunks_for_asset(asset)),block['id'])
    blocked=invalid or target=='table_verification' and mode=='missing_result'
    if blocked:
        with pytest.raises(TableStructureError):native_records(selected,['Item','Count'],[],10)
    else:
        assert block['data']['table_quality']['issues']==[]
        records=native_records(selected,['Item','Count'],[],10)
        node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
                  bindings=[dict(input='records',node_id='extract',output='records')])
        result=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
        path=tmp_path/'out.csv';path.write_text(result['artifact']['content'],encoding='utf-8')
        with path.open(encoding='utf-8-sig') as stream:assert list(csv.DictReader(stream))==[{'Item':'Milk','Count':'1'}]
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
