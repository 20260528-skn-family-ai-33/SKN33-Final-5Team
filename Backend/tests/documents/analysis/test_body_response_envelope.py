import copy
import csv
import json
import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run
from documents.resources.document_resources import chunks_for_asset,select_table_parts
from workflow_engine.executors.document.table_rows import native_records,TableStructureError
from workflow_engine.executors.output.export_result import execute
from tests.documents.analysis.test_table_quality_boundaries import Replay
from tests.documents.analysis.test_merged_repeats_and_ocr_contract import Response


LOCAL_FAILURES = {'missing_result','null_result','list_result','bad_array','duplicate','missing_source_item'}
OUTER_FAILURES = {'none','scalar','no_outputs','null_outputs','string_outputs','bad_version'}
UNKNOWN = {'nonobject_item','no_source','null_source','unknown_source','list_source','extra_unknown'}
MODES = ['normal', *sorted(LOCAL_FAILURES|OUTER_FAILURES|UNKNOWN)]


def mutate(data,mode):
    data=copy.deepcopy(data)
    if mode=='none': return None
    if mode=='scalar': return 'bad'
    if mode=='no_outputs': data.pop('outputs');return data
    if mode=='null_outputs': data['outputs']=None;return data
    if mode=='string_outputs': data['outputs']='bad';return data
    if mode=='bad_version': data['version']=None;return data
    item=data['outputs'][0]
    if mode=='missing_result': item.pop('result')
    if mode=='null_result': item['result']=None
    if mode=='list_result': item['result']=[]
    if mode=='bad_array': item['result']['rec_texts']=None
    if mode=='duplicate': data['outputs'].append(copy.deepcopy(item))
    if mode=='missing_source_item': data['outputs'].pop(0)
    if mode=='nonobject_item': data['outputs'][0]=None
    if mode=='no_source': item.pop('source')
    if mode=='null_source': item['source']=None
    if mode=='unknown_source': item['source']='never-requested.png'
    if mode=='list_source': item['source']=[]
    if mode=='extra_unknown': data['outputs'].append(dict(source='never-requested.png',result=item['result']))
    return data


@pytest.mark.parametrize('target',['body','table'])
@pytest.mark.parametrize('remote',[False,True])
@pytest.mark.parametrize('mode',MODES)
def test_body_envelope_preserves_attributable_items_and_table_csv(tmp_path,monkeypatch,remote,mode,target):
    table=dict(rec_texts=['Item','Count','Milk','1'],rec_boxes=[
        [0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    phase=[]
    def response(names):
        data=dict(version='test',outputs=[dict(source=name,result=(table if phase[-1]=='table_verification'
             else dict(rec_texts=['BODY'+str(i)],rec_boxes=[[0,0,30,15]],rec_scores=[.99])))
             for i,name in enumerate(names)])
        corrupt = (phase[-1]=='table_verification') == (target=='table')
        return mutate(data,mode) if corrupt else data
    class Engines(LocalEngines):
        def run(self,engine,**kw):
            phase.append(kw.get('phase'))
            if engine in ('layout','docling'):
                data=Replay(table).run(engine,**kw)
                if engine=='layout':
                    data['outputs'][0]['result']['boxes']=[
                        dict(label='text',score=.99,coordinate=[0,0,200,20]),
                        dict(label='text',score=.99,coordinate=[0,25,200,45]),
                        dict(label='table',score=.99,coordinate=[0,50,200,195])]
                return data
            return super().run(engine,**kw) if remote else response([str(p) for p in kw['sources']])
    def urlopen(request,timeout):
        return Response(response([s['name'] for s in json.loads(request.data)['sources']]))
    monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','off')
    monkeypatch.setattr('documents.analysis.adapters.urllib.request.urlopen',urlopen)
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Engines(tmp_path/'work'))
    analysis=payload['document_analysis'];block=next(b for b in analysis['blocks'] if b['engine']=='docling')
    assert 'BASE TABLE' in payload['text']
    if target=='table' and mode!='normal':
        assert 'BODY0' in payload['text'] and 'BODY1' in payload['text']
        assert payload['parser_status']=='partial' and block['status']=='review_required'
        asset=dict(id='a',name='a.png',kind='document',text=payload['text'],parser_metadata={'document_analysis':analysis})
        selected=select_table_parts(list(chunks_for_asset(asset)),block['id'])
        with pytest.raises(TableStructureError):native_records(selected,['Item','Count'],[],10)
        json.dumps(payload,allow_nan=False,ensure_ascii=False).encode('utf-8')
        return
    assert block['status']=='succeeded' and block['data']['table_quality']['issues']==[]
    if mode not in OUTER_FAILURES: assert 'BODY1' in payload['text']
    if mode in LOCAL_FAILURES or mode in UNKNOWN-{'extra_unknown'}: assert 'BODY0' not in payload['text']
    if mode!='normal': assert payload['parser_status']=='partial' and payload['warnings']
    asset=dict(id='a',name='a.png',kind='document',text=payload['text'],parser_metadata={'document_analysis':analysis})
    parts=list(chunks_for_asset(asset)); selected=select_table_parts(parts,block['id'])
    if mode in OUTER_FAILURES|UNKNOWN:
        with pytest.raises(TableStructureError):native_records(selected,['Item','Count'],[],10)
    else:
        records=native_records(selected,['Item','Count'],[],10)
        node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
                  bindings=[dict(input='records',node_id='extract',output='records')])
        result=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
        path=tmp_path/'out.csv';path.write_text(result['artifact']['content'],encoding='utf-8')
        with path.open(encoding='utf-8-sig') as stream: assert list(csv.DictReader(stream))==[{'Item':'Milk','Count':'1'}]
    if mode in LOCAL_FAILURES:
        assert all(isinstance(scope,list) and scope for scope in analysis['review_scopes'].values())
        with pytest.raises(TableStructureError):native_records(parts,['Item','Count'],[],10)
    json.dumps(payload,allow_nan=False,ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('bad_batch',[0,1])
def test_remote_batches_do_not_lose_unknown_source_marker(monkeypatch,bad_batch):
    from documents.analysis.adapters import remote_engine
    names=['first.png','second.png']
    replies=[]
    for i,name in enumerate(names):
        data=dict(version='test',outputs=[dict(source=name,result={})])
        if i==bad_batch: data['outputs'].append(dict(source='unknown.png',result={}))
        replies.append(data)
    monkeypatch.setattr('documents.analysis.adapters.remote_request_batches',
                        lambda unused: [( [name], b'{}') for name in names])
    monkeypatch.setattr('documents.analysis.adapters.urllib.request.urlopen',
                        lambda request,timeout: Response(replies.pop(0)))
    result=remote_engine('https://parser.invalid','paddleocr',names)
    assert result['invalid_output_sources'] is True
    assert [o['source'] for o in result['outputs']]==names
