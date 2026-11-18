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


def pipeline_csv(tmp_path,data,ocr,fields,blocked):
    source=tmp_path/'test.png';Image.new('RGB',(400,400),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Replay(ocr,data['cells']))
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert table['data']['cells']==data['cells']
    assert payload['parser_status']==('partial' if blocked else 'succeeded')
    assert bool(payload['warnings']) is blocked
    part=dict(file_id='test',file_name='test',source_id='table',table_cells=table['data']['cells'],
              review_required=table['status']=='review_required')
    path=tmp_path/'out.csv'
    if blocked:
        with pytest.raises(TableStructureError):native_records([part],fields,[],100)
        assert not path.exists()
        return
    records=native_records([part],fields,[],100)
    node=dict(id='export',tool='export_result',settings={'format':'csv','fields':fields},
              bindings=[dict(input='records',node_id='extract',output='records')])
    result=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path.write_text(result['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:return list(csv.DictReader(stream))


@pytest.mark.parametrize('overlap',[0,.001,1,5,13,20,30])
@pytest.mark.parametrize('mirror',[False,True])
def test_small_or_tall_overlap_cannot_confirm_wrong_row(tmp_path,overlap,mirror):
    actual=[['Item','Count'],['Milk','1'],['','2'],['Tea','']]
    wrong=[['Item','Count'],['Milk','1'],['',''],['Tea','2']]
    if mirror:actual[1:]=reversed(actual[1:]);wrong[1:]=reversed(wrong[1:])
    data,ocr=review_case(wrong,[(t,[c*70,r*30,c*70+35,r*30+20])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    box=ocr['rec_boxes'][ocr['rec_texts'].index('2')]
    if mirror:box[1]=50-overlap
    else:box[3]=90+overlap
    # Translation/scaling must not change the decision.
    ocr['rec_boxes']=[[v*1.5+7 for v in b] for b in ocr['rec_boxes']]
    before=copy.deepcopy(data)
    pipeline_csv(tmp_path,data,ocr,['Item','Count'],True)
    assert data==before and not reconcile(data,ocr)[1]['repairs']


@pytest.mark.parametrize('quantity_top',[40,60,80])
@pytest.mark.parametrize('reverse',[False,True])
def test_multiline_anchor_supports_top_middle_bottom_alignment(tmp_path,quantity_top,reverse):
    data,ocr=review_case([['Item','Count','Price'],['Milk\nChocolate','1','1200']], [
        ('Item',[0,0,40,20]),('Count',[70,0,100,20]),('Price',[140,0,180,20]),
        ('Milk\nChocolate',[0,40,40,100]),('1',[70,quantity_top,100,quantity_top+20]),
        ('1200',[140,80,180,100])])
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    assert pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],False)==[
        {'Item':'Milk\nChocolate','Count':'1','Price':'1200'}]


@pytest.mark.parametrize('vertical',[False,True])
def test_merged_neighbor_supplies_outer_boundary_for_independent_column(tmp_path,vertical):
    cells=[dict(row=0,column=0,col_span=2,row_span=1,text='Description'),dict(row=0,column=2,text='Count'),
           dict(row=1,column=0,col_span=2,row_span=1,text='Milk'),dict(row=1,column=2,text='1')]
    texts=['Description','Count','Milk','1'];boxes=[[0,0,120,20],[140,0,180,20],[0,30,120,50],[140,30,180,50]]
    if vertical:
        cells[2]['row_span']=2;cells.append(dict(row=2,column=2,text='2'))
        boxes[2][3]=80;texts.append('2');boxes.append([140,60,180,80])
    data=dict(cells=cells,num_cols=3,num_rows=3 if vertical else 2)
    ocr=dict(rec_texts=texts,rec_boxes=boxes,rec_scores=[.999]*len(texts))
    assert pipeline_csv(tmp_path,data,ocr,['Count'],False)==([{'Count':'1'},{'Count':'2'}] if vertical else [{'Count':'1'}])


@pytest.mark.parametrize('repeated',[False,True])
def test_multiple_misplaced_values_cannot_expand_the_row(tmp_path,repeated):
    value='1' if repeated else '2'
    actual=[['Item','Count','Price'],['Milk','1','10'],['',value,'3'],['Tea','','']]
    wrong=[['Item','Count','Price'],['Milk','1','10'],['','',''],['Tea',value,'3']]
    data,ocr=review_case(wrong,[(t,[c*70,r*30,c*70+35,r*30+20])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    for box in ocr['rec_boxes']:
        if box[1]==60:box[3]=91
    pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],True)
    assert not reconcile(data,ocr)[1]['repairs']
