import copy
import csv
import pytest
from PIL import Image
from documents.analysis.pipeline import run
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case
from tests.documents.analysis.test_table_quality_boundaries import Replay
from workflow_engine.executors.document.table_rows import native_records, TableStructureError
from workflow_engine.executors.output.export_result import execute


def case(missing,repeat=False,shift=7,multiline=False):
    first=['2','10'] if repeat else ['1','20']
    name='Tea\nLeaves' if multiline else 'Tea'
    data,ocr=review_case([['Item','Count','Price'],['Milk',*first],
        [name,None if missing in ('count','both') else '2',None if missing in ('price','both') else '10'],
        ['Coffee','3','30']], [
        ('Item',[0,0,35,20]),('Count',[70,0,105,20]),('Price',[140,0,175,20]),
        ('Milk',[0,30,35,50]),(first[0],[70,30,105,50]),(first[1],[140,30,175,50]),
        (name,[0,80 if multiline else 90,35,120 if multiline else 110]),
        ('2',[70,90-shift,105,110-shift]),('10',[140,90+shift,175,110+shift]),
        ('Coffee',[0,150,35,170]),('3',[70,150,105,170]),('30',[140,150,175,170])])
    return data,ocr


def pipeline_check(tmp_path,data,ocr,blocked):
    before=copy.deepcopy(data)
    expected,quality=reconcile(data,ocr)
    if blocked:assert expected==before['cells'] and not quality['repairs'] and quality['issues']
    else:assert not quality['issues']
    source=tmp_path/'test.png';Image.new('RGB',(200,200),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Replay(ocr,data['cells']))
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert payload['parser_status']==('partial' if blocked else 'succeeded')
    assert bool(payload['warnings']) is blocked
    assert table['data']['cells']==expected and data==before
    if blocked:
        assert 'original_cells' not in table['data']
        assert not table['data']['table_quality']['repairs']
    elif quality['repairs']:assert table['data']['original_cells']==before['cells']
    part=dict(file_id='test',file_name='test',source_id='table',table_cells=expected,
        review_required=table['status']=='review_required')
    fields=['Item','Count','Price'];path=tmp_path/'out.csv'
    if blocked:
        with pytest.raises(TableStructureError):native_records([part],fields,[],100)
        assert not path.exists()
        return
    records=native_records([part],fields,[],100)
    node=dict(id='export',tool='export_result',settings={'format':'csv','fields':fields},
        bindings=[dict(input='records',node_id='extract',output='records')])
    output=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path.write_text(output['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:
        rows=list(csv.DictReader(stream))
        assert rows[1]['Count']=='2' and rows[1]['Price']=='10'


@pytest.mark.parametrize('missing',['none','count','price','both'])
@pytest.mark.parametrize('repeat',[False,True])
@pytest.mark.parametrize('reverse',[False,True])
def test_cell_presence_cannot_bypass_final_set_verification(tmp_path,missing,repeat,reverse):
    data,ocr=case(missing,repeat)
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    pipeline_check(tmp_path,data,ocr,True)


@pytest.mark.parametrize('shift',[3.4999,3.5,3.5001,7])
@pytest.mark.parametrize('mirror',[False,True])
def test_repair_peers_share_the_same_agreement_boundary(tmp_path,shift,mirror):
    data,ocr=case('both',shift=shift)
    if mirror:
        for b in ocr['rec_boxes'][6:9]:b[1],b[3]=200-b[3],200-b[1]
    pipeline_check(tmp_path,data,ocr,shift>3.5)


@pytest.mark.parametrize('reverse',[False,True])
def test_transformed_repair_conflict_is_atomic(tmp_path,reverse):
    data,ocr=case('both')
    ocr['rec_boxes']=[[v*.9+5 for v in b] for b in ocr['rec_boxes']]
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    pipeline_check(tmp_path,data,ocr,True)


@pytest.mark.parametrize('missing',['count','price','both'])
@pytest.mark.parametrize('multiline',[False,True])
def test_normal_single_or_multiline_repairs_export(tmp_path,missing,multiline):
    data,ocr=case(missing,shift=7 if multiline else 0,multiline=multiline)
    pipeline_check(tmp_path,data,ocr,False)


def test_repair_beside_merged_cells_keeps_independent_column_export(tmp_path):
    data=dict(cells=[dict(row=r,column=c,col_span=2 if c==0 else 1,text=t)
        for r,row in enumerate([['Description','Count'],['Tea',None],['Coffee','3']])
        for c,t in zip([0,2],row) if t is not None])
    ocr=dict(rec_texts=['Description','Count','Tea','2','Coffee','3'],
        rec_boxes=[[0,0,120,20],[140,0,180,20],[0,40,120,60],[140,40,180,60],
                   [0,80,120,100],[140,80,180,100]],rec_scores=[.999]*6)
    source=tmp_path/'merged.png';Image.new('RGB',(200,200),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Replay(ocr,data['cells']))
    assert payload['parser_status']=='succeeded' and not payload['warnings']
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert table['data']['original_cells']==data['cells']
    assert len(table['data']['table_quality']['repairs'])==1
    records=native_records([dict(file_id='t',file_name='t',source_id='t',table_cells=table['data']['cells'])],['Count'],[],100)
    node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Count']},
        bindings=[dict(input='records',node_id='extract',output='records')])
    output=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path=tmp_path/'merged.csv';path.write_text(output['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:assert list(csv.DictReader(stream))==[{'Count':'2'},{'Count':'3'}]
