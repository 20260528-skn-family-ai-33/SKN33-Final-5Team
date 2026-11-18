import copy
import pytest
from PIL import Image
from documents.analysis.pipeline import run
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case
from tests.documents.analysis.test_table_quality_boundaries import Replay
from tests.documents.analysis.test_unique_anchor_positions import write_csv
from workflow_engine.executors.document.table_rows import native_records, TableStructureError


def sparse_case(axis, mirror=False, gaps=1, missing=False):
    if axis=='row':
        actual=[['Item','Count'],['Milk','1']]+[['',''] for _ in range(gaps)]+[['','2'],['Tea','']]
        wrong=copy.deepcopy(actual);wrong[-2][1]='';wrong[-1][1]='2'
    else:
        actual=[['Item','Count']+['']*gaps,['Milk','']+['']*(gaps-1)+['1200']]
        wrong=copy.deepcopy(actual);wrong[1][-1]='';wrong[1][1]='1200'
    if mirror:
        if axis=='row':actual[1:]=reversed(actual[1:]);wrong[1:]=reversed(wrong[1:])
        else:actual=[list(reversed(r)) for r in actual];wrong=[list(reversed(r)) for r in wrong]
    data,ocr=review_case(wrong,[(t,[c*40,r*25,c*40+25,r*25+15])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    data.update(num_rows=len(wrong),num_cols=len(wrong[0]))
    if missing:data['cells']=[c for c in data['cells'] if c['text']]
    return data,ocr


@pytest.mark.parametrize('axis,gaps',[('row',0),('row',1),('row',2),('column',1),('column',2)])
@pytest.mark.parametrize('mirror',[False,True])
@pytest.mark.parametrize('missing',[False,True])
def test_sparse_misplacement_is_not_verified_or_exported(tmp_path,axis,mirror,gaps,missing):
    data,ocr=sparse_case(axis,mirror,gaps,missing)
    original=copy.deepcopy(data)
    cells,quality=reconcile(data,ocr)
    assert quality['issues'] and not quality['repairs'] and cells==original['cells']
    source=tmp_path/'sparse.png';Image.new('RGB',(240,240),'white').save(source)
    class Dimensions(Replay):
        def run(self,engine,**kwargs):
            output=super().run(engine,**kwargs)
            if engine=='docling':
                output['blocks'][0]['data'].update(num_rows=data['num_rows'],num_cols=data['num_cols'])
            return output
    result=run(source,tmp_path/'work',engines=Dimensions(ocr,data['cells']))
    table=next(b for b in result['document_analysis']['blocks'] if b['engine']=='docling')
    assert result['parser_status']=='partial' and result['warnings']
    assert table['status']=='review_required' and table['data']['cells']==original['cells']
    part=dict(file_id='test',file_name='sparse.png',source_id='t',table_cells=table['data']['cells'],review_required=True)
    with pytest.raises(TableStructureError):
        rows=native_records([part],['Item','Count'],[],100)
        write_csv(rows,tmp_path/'bad.csv')
    assert not (tmp_path/'bad.csv').exists() and data==original


def test_multiple_candidates_cannot_manufacture_missing_column_boundary():
    actual=[['Item','',''],['Milk','','1200'],['Tea','','1300']]
    wrong=[['Item','',''],['Milk','1200',''],['Tea','1300','']]
    data,ocr=review_case(wrong,[(t,[c*40,r*25,c*40+25,r*25+15])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    assert reconcile(data,ocr)[1]['issues']


def test_supported_sparse_table_preserves_empty_rows_and_exports(tmp_path):
    grid=[['Item','Count','Price'],['Milk','1',''],['','',''],['Tea','2','1200']]
    data,ocr=review_case(grid,[(t,[c*60,r*30,c*60+40,r*30+20])
        for r,row in enumerate(grid) for c,t in enumerate(row) if t])
    cells,quality=reconcile(data,ocr)
    assert not quality['issues'] and cells==data['cells']
    source=tmp_path/'supported.png';Image.new('RGB',(240,240),'white').save(source)
    payload=run(source,tmp_path/'supported-work',engines=Replay(ocr,cells))
    assert payload['parser_status']=='succeeded' and not payload['warnings']
    table=next(b for b in payload['document_analysis']['blocks'] if b['engine']=='docling')
    assert table['data']['cells']==cells
    part=dict(file_id='test',file_name='test',source_id='t',table_cells=table['data']['cells'])
    rows=write_csv(native_records([part],['Item','Count'],[],100),tmp_path/'good.csv')
    assert rows==[{'Item':'Milk','Count':'1'},{'Item':'Tea','Count':'2'}]
