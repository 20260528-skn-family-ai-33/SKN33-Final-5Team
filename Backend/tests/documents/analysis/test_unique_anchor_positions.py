"""Unique text must not certify a cell at a contradictory row/column position."""
import copy
import csv
import pytest
from PIL import Image
from documents.analysis.pipeline import run
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import simple, review_case
from tests.documents.analysis.test_table_quality_boundaries import Replay
from workflow_engine.executors.document.table_rows import native_records, TableStructureError
from workflow_engine.executors.output.export_result import execute


def misplaced(case):
    data,ocr=simple()
    if case=='row_swap':
        data['cells'][3]['text'],data['cells'][5]['text']='2','1'
    elif case=='column_swap':
        data['cells'][2]['text'],data['cells'][3]['text']='1','Milk'
    elif case=='cyclic_rows':
        data,ocr=review_case([['Item','Count'],['Milk','3'],['Chocolate','1'],['Tea','2']],
            [(t,[c*100,r*30,c*100+50,r*30+20]) for r,row in enumerate(
                [['Item','Count'],['Milk','1'],['Chocolate','2'],['Tea','3']]) for c,t in enumerate(row)])
    elif case=='same_box':ocr['rec_boxes'][3]=ocr['rec_boxes'][2][:]
    elif case=='tiny_column_overlap':ocr['rec_boxes'][3][0]=49.999
    elif case=='mixed_repeated':
        data,ocr=review_case([['Item','Count'],['Milk','2'],['Chocolate','1'],['Tea','1']],
            [(t,[c*100,r*30,c*100+50,r*30+20]) for r,row in enumerate(
                [['Item','Count'],['Milk','1'],['Chocolate','1'],['Tea','2']]) for c,t in enumerate(row)])
    elif case=='conflict_with_missing':
        data,ocr=misplaced('row_swap')
        data['cells'].pop(2)
    return data,ocr


@pytest.mark.parametrize('case',['row_swap','column_swap','cyclic_rows','same_box',
    'tiny_column_overlap','mixed_repeated','conflict_with_missing'])
@pytest.mark.parametrize('reverse',[False,True])
def test_contradictory_unique_anchors_require_review_and_never_export(tmp_path,case,reverse):
    data,ocr=misplaced(case)
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    original=copy.deepcopy(data)
    cells,quality=reconcile(data,ocr)
    assert cells==original['cells'] and data==original and not quality['repairs']
    assert any(i['kind']=='anchor_geometry_conflict' for i in quality['issues'])
    source=tmp_path/'receipt.png';Image.new('RGB',(200,200),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Replay(ocr,data['cells']))
    assert payload['parser_status']=='partial' and payload['warnings']
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert table['status']=='review_required' and table['data']['cells']==original['cells']
    part=dict(file_id='test',file_name='receipt.png',source_id='table',table_cells=table['data']['cells'],
              review_required=table['status']=='review_required')
    path=tmp_path/'bad.csv'
    with pytest.raises(TableStructureError):
        records=native_records([part],['Item','Count'],[],100)
        write_csv(records,path)
    assert not path.exists()


def write_csv(records,path):
    node=dict(id='export',tool='export_result',settings={'format':'csv','fields':['Item','Count']},
        bindings=[dict(input='records',node_id='extract',output='records')])
    result=execute(node,{'outputs':{'extract':{'records':records}}},None,{},None)
    path.write_text(result['artifact']['content'],encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:return list(csv.DictReader(stream))


@pytest.mark.parametrize('variant',['normal','alignment','multiline','permutation','translated','touching'])
def test_consistent_unique_positions_reach_actual_csv(tmp_path,variant):
    data,ocr=simple()
    if variant=='alignment':
        ocr['rec_boxes'][3]=[160,29,180,51];ocr['rec_boxes'][5]=[160,59,180,81]
    elif variant=='multiline':
        data['cells'][4]['text']='Chocolate\nBar';ocr['rec_texts'][4]='Chocolate\nBar'
        ocr['rec_boxes'][4]=[0,60,50,100];ocr['rec_boxes'][5]=[100,80,150,100]
    elif variant=='permutation':ocr={k:list(reversed(v)) for k,v in ocr.items()}
    elif variant=='translated':
        ocr['rec_boxes']=[[v*2+(-100 if i%2==0 else 33) for i,v in enumerate(b)] for b in ocr['rec_boxes']]
    elif variant=='touching':
        for box in ocr['rec_boxes']:box[3]+=10
    source=tmp_path/'receipt.png';Image.new('RGB',(200,200),'white').save(source)
    payload=run(source,tmp_path/'work',engines=Replay(ocr,data['cells']))
    assert payload['parser_status']=='succeeded' and not payload['warnings']
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    part=dict(file_id='test',file_name='receipt.png',source_id='table',table_cells=table['data']['cells'])
    rows=write_csv(native_records([part],['Item','Count'],[],100),tmp_path/'good.csv')
    assert rows==[{'Item':data['cells'][2]['text'],'Count':'1'},{'Item':data['cells'][4]['text'],'Count':'2'}]


@pytest.mark.parametrize('vertical',[False,True])
def test_logical_spans_preserve_correct_merged_cells(vertical):
    data,ocr=simple()
    if vertical:
        data['cells'][2]['row_span']=2
        data['cells'].pop(4)
        ocr['rec_boxes'][2]=[0,30,50,80]
        for values in ocr.values():values.pop(4)
    else:
        data['cells'][4]['col_span']=2
        data['cells'].pop(5)
        ocr['rec_boxes'][4]=[0,60,150,80]
        for values in ocr.values():values.pop(5)
    cells,quality=reconcile(data,ocr)
    assert cells==data['cells'] and not quality['issues'] and not quality['repairs']
