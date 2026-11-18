import pytest
from tests.documents.analysis.test_table_quality import review_case
from tests.documents.analysis.test_row_overlap_and_merge_evidence import pipeline_csv


@pytest.mark.parametrize('first',['1','2'])
@pytest.mark.parametrize('bottom',[102.999,103,105,110])
@pytest.mark.parametrize('reverse',[False,True])
def test_uniqueness_does_not_change_wrong_row_decision(tmp_path,first,bottom,reverse):
    actual=[['Item','Count'],['Milk',first],['','2'],['Tea','']]
    wrong=[['Item','Count'],['Milk',first],['',''],['Tea','2']]
    data,ocr=review_case(wrong,[(t,[c*70,r*30,c*70+35,r*30+20])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    for box in ocr['rec_boxes']:
        if box[1]==60:box[3]=bottom
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    pipeline_csv(tmp_path,data,ocr,['Item','Count'],True)


@pytest.mark.parametrize('value',['1','2'])
@pytest.mark.parametrize('top',[60,80,100])
@pytest.mark.parametrize('reverse',[False,True])
def test_uniqueness_does_not_change_multiline_alignment(tmp_path,value,top,reverse):
    grid=[['Item','Count','Price'],['Milk','1','100'],['Tea\nChocolate',value,'1200']]
    data,ocr=review_case(grid,[('Item',[0,0,35,20]),('Count',[70,0,105,20]),('Price',[140,0,175,20]),
        ('Milk',[0,30,35,50]),('1',[70,30,105,50]),('100',[140,30,175,50]),
        ('Tea\nChocolate',[0,60,35,120]),(value,[70,top,105,top+20]),('1200',[140,100,175,120])])
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    assert pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],False)==[
        {'Item':'Milk','Count':'1','Price':'100'},{'Item':'Tea\nChocolate','Count':value,'Price':'1200'}]


@pytest.mark.parametrize('first',['1','2'])
def test_mirrored_scaled_misplacement_is_still_blocked(tmp_path,first):
    actual=[['Item','Count'],['Tea',''],['','2'],['Milk',first]]
    wrong=[['Item','Count'],['Tea','2'],['',''],['Milk',first]]
    data,ocr=review_case(wrong,[(t,[c*70,r*30,c*70+35,r*30+20])
        for r,row in enumerate(actual) for c,t in enumerate(row) if t])
    for box in ocr['rec_boxes']:
        if box[1]==60:box[1]=37
    ocr['rec_boxes']=[[v*1.25+5 for v in b] for b in ocr['rec_boxes']]
    pipeline_csv(tmp_path,data,ocr,['Item','Count'],True)


def test_duplicate_repeat_cannot_hide_unconfirmed_multiline_cell(tmp_path):
    grid=[['Item','Count','Price'],['Milk','1','100'],['Tea\nChocolate','1','1200']]
    data,ocr=review_case(grid,[('Item',[0,0,35,20]),('Count',[70,0,105,20]),('Price',[140,0,175,20]),
        ('Milk',[0,30,35,50]),('1',[70,30,105,50]),('100',[140,30,175,50]),
        ('Tea\nChocolate',[0,60,35,120]),('1',[70,30,105,50]),('1200',[140,100,175,120])])
    pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],True)


def test_repeated_count_can_use_horizontal_merged_row_reference(tmp_path):
    cells=[dict(row=r,column=c,col_span=2 if c==0 else 1,text=t)
           for r,row in enumerate([['Description','Count'],['Milk','1'],['Tea','1']])
           for c,t in zip([0,2],row)]
    ocr=dict(rec_texts=['Description','Count','Milk','1','Tea','1'],
        rec_boxes=[[0,0,120,20],[140,0,180,20],[0,30,120,50],[140,30,180,50],
                   [0,60,120,80],[140,60,180,80]],rec_scores=[.999]*6)
    assert pipeline_csv(tmp_path,dict(cells=cells),ocr,['Count'],False)==[{'Count':'1'},{'Count':'1'}]
