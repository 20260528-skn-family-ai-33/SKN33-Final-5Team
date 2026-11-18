"""Rows must contain the entire OCR box, including whitespace between rows."""
import csv
import pytest
from documents.analysis.table_quality import reconcile, row_corridors
from tests.documents.analysis.test_table_quality import review_case
from workflow_engine.executors.document.table_rows import native_records, TableStructureError
from workflow_engine.executors.output.export_result import execute


def sample(top=60, bottom=80, next_top=110, value='2 3'):
    return review_case([['Item','Count'],['Bread','1'],['Milk',None],['Tea','']], [
        ('Item',[0,0,50,20]),('Count',[110,0,150,20]),
        ('Bread',[0,30,50,50]),('1',[110,30,130,50]),
        ('Milk',[0,60,50,80]),('Tea',[0,next_top,50,next_top+20]),
        (value,[110,top,130,bottom])])


def export(data, prediction, path):
    cells, quality = reconcile(data, prediction)
    part = dict(file_id='synthetic',file_name='receipt.png',source_id='table',
                table_cells=cells,review_required=bool(quality['issues']))
    records = native_records([part],['Item','Count'],[],10)
    node = dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
                bindings=[dict(input='records',node_id='extract',output='records')])
    result = execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path.write_text(result['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('top,bottom',[(60,95.001),(60,100),(60,120),(60,123),(60,130),(54.999,80),(30,80)])
def test_cross_row_box_never_reaches_csv(tmp_path,top,bottom):
    data,prediction=sample(top,bottom)
    cells,quality=reconcile(data,prediction)
    assert cells==data['cells'] and not quality['repairs'] and quality['issues']
    path=tmp_path/'bad.csv'
    with pytest.raises(TableStructureError):export(data,prediction,path)
    assert not path.exists()


@pytest.mark.parametrize('next_top',[90,110,180])
@pytest.mark.parametrize('overflow',[False,True])
def test_variable_row_gap_and_exact_boundary_contact(next_top,overflow):
    data,prediction=sample(bottom=(80+next_top)/2 + (.001 if overflow else 0),next_top=next_top)
    cells,quality=reconcile(data,prediction)
    assert bool(quality['repairs']) is not overflow
    assert bool(quality['issues']) is overflow


@pytest.mark.parametrize('top,bottom,value',[(60,80,'2'),(55,95,'2 3'),(60,80,'two\nunits')])
def test_contained_text_is_exported_without_numeric_heuristics(tmp_path,top,bottom,value):
    data,prediction=sample(top,bottom,value=value)
    rows=export(data,prediction,tmp_path/'good.csv')
    assert rows[1]=={'Item':'Milk','Count':value}


@pytest.mark.parametrize('next_top',[70,40])
def test_overlapping_or_reversed_rows_cannot_support_repairs(next_top):
    data,prediction=sample(next_top=next_top)
    assert not reconcile(data,prediction)[1]['repairs']


def test_missing_neighbor_does_not_extrapolate_row_extent():
    data,prediction=sample(bottom=90)
    for key in prediction:prediction[key].pop(5)
    assert not reconcile(data,prediction)[1]['repairs']


def test_vertical_merge_disables_affected_row_corridors():
    rows={1:[[0,30,50,50]],2:[[0,60,50,80]],3:[[0,110,50,130]]}
    assert row_corridors(rows,[dict(row=1,column=0,row_span=2)])=={3:(110,130)}


def test_multiline_row_and_ocr_order_preserve_valid_repair():
    data,prediction=sample(top=80,bottom=100,next_top=130,value='2')
    data['cells'][4]['text']='Milk\nChocolate'
    prediction['rec_texts'][4]='Milk\nChocolate'
    prediction['rec_boxes'][4]=[0,60,50,100]
    for reverse in [False,True]:
        candidate={k:list(reversed(v)) if reverse else v for k,v in prediction.items()}
        _,quality=reconcile(data,candidate)
        assert not quality['issues'] and quality['repairs'][0]['text']=='2'
