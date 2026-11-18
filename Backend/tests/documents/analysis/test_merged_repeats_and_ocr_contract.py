import json
import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run
from tests.documents.analysis.test_row_overlap_and_merge_evidence import pipeline_csv
from tests.documents.analysis.test_table_quality_boundaries import Replay


def merged_case(vertical=False,repeated=True):
    cells=[dict(row=0,column=0,col_span=2,text='Description'),dict(row=0,column=2,text='Count')]
    texts=['Description','Count'];boxes=[[0,0,120,20],[140,0,180,20]]
    for i in range(2):
        row=1+i*(2 if vertical else 1);name='Milk' if repeated or i==0 else 'Tea'
        cells.append(dict(row=row,column=0,col_span=2,row_span=2 if vertical else 1,text=name))
        texts.append(name);boxes.append([0,row*30,120,(row+1)*30+20 if vertical else row*30+20])
        for r in range(row,row+(2 if vertical else 1)):
            cells.append(dict(row=r,column=2,text=str(r)))
            texts.append(str(r));boxes.append([140,r*30,180,r*30+20])
    return dict(cells=cells),dict(rec_texts=texts,rec_boxes=boxes,rec_scores=[.999]*len(texts))


@pytest.mark.parametrize('vertical',[False,True])
@pytest.mark.parametrize('repeated',[False,True])
@pytest.mark.parametrize('reverse',[False,True])
def test_normal_merged_text_is_confirmed_without_changing_independent_export(tmp_path,vertical,repeated,reverse):
    data,ocr=merged_case(vertical,repeated)
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    assert pipeline_csv(tmp_path,data,ocr,['Count'],False)==[
        {'Count':str(r)} for r in range(1,5 if vertical else 3)]


@pytest.mark.parametrize('vertical',[False,True])
@pytest.mark.parametrize('failure',['duplicate','missing','wrong_position'])
def test_merged_repeat_needs_one_to_one_location_match(tmp_path,vertical,failure):
    data,ocr=merged_case(vertical)
    indices=[i for i,t in enumerate(ocr['rec_texts']) if t=='Milk']
    if failure=='duplicate':ocr['rec_boxes'][indices[1]]=ocr['rec_boxes'][indices[0]][:]
    elif failure=='missing':
        for values in ocr.values():values.pop(indices[1])
    else:ocr['rec_boxes'][indices[1]]=[0,170,120,190]
    pipeline_csv(tmp_path,data,ocr,['Count'],True)


class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return json.dumps(self.data).encode('utf-8')


@pytest.mark.parametrize('kind',['high_surrogate','low_surrogate','huge_score','huge_x','huge_negative_x',
                                  'nan','inf','null_score','normal_korean','normal_astral'])
def test_remote_optional_ocr_contract_preserves_base_and_safe_raw_evidence(tmp_path,monkeypatch,kind):
    prediction=dict(rec_texts=['한글😀' if kind=='normal_astral' else '한글'],
                    rec_boxes=[[0,0,20,15]],rec_scores=[.999])
    if kind=='high_surrogate':prediction['rec_texts']=[chr(0xD800)]
    if kind=='low_surrogate':prediction['rec_texts']=[chr(0xDFFF)]
    if kind=='huge_score':prediction['rec_scores']=[10**400]
    if kind=='huge_x':prediction['rec_boxes'][0][2]=10**400
    if kind=='huge_negative_x':prediction['rec_boxes'][0][0]=-10**400
    if kind=='nan':prediction['rec_scores']=[float('nan')]
    if kind=='inf':prediction['rec_boxes'][0][2]=float('inf')
    if kind=='null_score':prediction['rec_scores']=None
    events=[]
    class Engines(LocalEngines):
        def run(self,engine,**kw):
            events.append((engine,kw.get('phase')))
            if engine in ['layout','docling']:return Replay(None).run(engine,**kw)
            return super().run(engine,**kw)
    def urlopen(request,timeout):
        p=prediction if events[-1][1]=='table_verification' else dict(
            rec_texts=['BASE BODY'],rec_boxes=[[0,0,20,15]],rec_scores=[.999])
        return Response(dict(version='test',outputs=[dict(source=s['name'],result=p)
            for s in json.loads(request.data)['sources']]))
    monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','off')
    monkeypatch.setattr('documents.analysis.adapters.urllib.request.urlopen',urlopen)
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    result=run(source,tmp_path/'work',engines=Engines(tmp_path/'work'))
    assert 'BASE BODY' in result['text'] and 'BASE TABLE' in result['text']
    assert ('docling','supplement') in events and result['parser_status']=='partial'
    analysis=result['document_analysis']
    invalid=not kind.startswith('normal')
    warning='table_verification:invalid_engine_result'
    assert (warning in result['warnings']) is invalid
    if invalid:
        assert analysis['review_scopes'][warning]
        assert all('table_ocr' not in b['data'] for b in analysis['blocks'])
    else:
        evidence=next(b['data']['table_ocr'] for b in analysis['blocks'] if 'table_ocr' in b['data'])
        assert evidence['rec_texts']==prediction['rec_texts']
    raw=[json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path/'work').rglob('raw.json')]
    assert any(output[0]['result']['rec_texts']==prediction['rec_texts'] for output in raw)
    json.dumps(result,ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('vertical',[False,True])
def test_confirming_merged_text_does_not_allow_exporting_merged_cells(vertical):
    from documents.analysis.table_quality import reconcile
    from workflow_engine.executors.document.table_rows import native_records,TableStructureError
    data,ocr=merged_case(vertical)
    cells,quality=reconcile(data,ocr)
    assert not quality['issues']
    part=dict(file_id='t',file_name='t',source_id='t',table_cells=cells)
    with pytest.raises(TableStructureError):native_records([part],['Description'],[],100)
