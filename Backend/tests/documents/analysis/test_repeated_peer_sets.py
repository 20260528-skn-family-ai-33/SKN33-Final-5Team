import copy
import pytest
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case
from tests.documents.analysis.test_row_overlap_and_merge_evidence import pipeline_csv


def peer_case(mode,shift=7,mirror=False):
    first={'unique':['1','20'],'mixed':['2','20'],'repeated':['2','10']}[mode]
    grid=[['Item','Count','Price'],['Milk',*first],['','',''],['Tea','2','10'],['','','']]
    data,ocr=review_case(grid,[('Item',[0,0,35,20]),('Count',[70,0,105,20]),('Price',[140,0,175,20]),
        ('Milk',[0,30,35,50]),(first[0],[70,30,105,50]),(first[1],[140,30,175,50]),
        ('Tea',[0,90,35,110]),('2',[70,90-shift,105,110-shift]),('10',[140,90+shift,175,110+shift])])
    if mirror:
        # Reflect positions within the Tea row without changing column identity.
        for b in ocr['rec_boxes'][6:]:b[1],b[3]=200-b[3],200-b[1]
    return data,ocr


@pytest.mark.parametrize('mode',['unique','mixed','repeated'])
@pytest.mark.parametrize('shift',[3.5001,7,7.001])
@pytest.mark.parametrize('reverse',[False,True])
def test_peer_conflict_is_blocked_independent_of_uniqueness_and_order(tmp_path,mode,shift,reverse):
    data,ocr=peer_case(mode,shift)
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    before=copy.deepcopy(data)
    pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],True)
    cells,quality=reconcile(data,ocr)
    assert cells==before['cells'] and data==before and not quality['repairs']


@pytest.mark.parametrize('reverse',[False,True])
def test_mirrored_transformed_peers_do_not_expand_the_row(tmp_path,reverse):
    data,ocr=peer_case('repeated',mirror=True)
    ocr['rec_boxes']=[[v*1.05+2 for v in b] for b in ocr['rec_boxes']]
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],True)


@pytest.mark.parametrize('reverse',[False,True])
def test_rejected_peers_cannot_supply_evidence_for_another_repair(tmp_path,reverse):
    data,ocr=peer_case('repeated')
    data['cells'].extend([dict(row=5,column=0,text='Coffee'),dict(row=5,column=2,text='')])
    ocr['rec_texts'].extend(['Coffee','5']);ocr['rec_boxes'].extend([[0,150,35,170],[70,150,105,170]])
    ocr['rec_scores'].extend([.999,.999])
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    cells,quality=reconcile(data,ocr)
    assert cells==data['cells'] and not quality['repairs'] and quality['issues']
    pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],True)


@pytest.mark.parametrize('multiline',[False,True])
@pytest.mark.parametrize('reverse',[False,True])
def test_normal_repeated_peer_set_exports_with_fixed_reference(tmp_path,multiline,reverse):
    item='Tea\nChocolate' if multiline else 'Tea'
    data,ocr=review_case([['Item','Count','Price'],['Milk','2','10'],[item,'2','10']], [
        ('Item',[0,0,35,20]),('Count',[70,0,105,20]),('Price',[140,0,175,20]),
        ('Milk',[0,30,35,50]),('2',[70,30,105,50]),('10',[140,30,175,50]),
        (item,[0,60,35,120 if multiline else 80]),('2',[70,60,105,80]),
        ('10',[140,100 if multiline else 60,175,120 if multiline else 80])])
    if reverse:ocr={k:list(reversed(v)) for k,v in ocr.items()}
    assert pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],False)==[
        {'Item':'Milk','Count':'2','Price':'10'},{'Item':item,'Count':'2','Price':'10'}]


@pytest.mark.parametrize('mode',['unique','mixed','repeated'])
@pytest.mark.parametrize('shift',[3.4999,3.5])
def test_symmetric_agreement_boundary_is_consistent_for_all_value_modes(tmp_path,mode,shift):
    data,ocr=peer_case(mode,shift)
    rows=pipeline_csv(tmp_path,data,ocr,['Item','Count','Price'],False)
    assert rows[-1]=={'Item':'Tea','Count':'2','Price':'10'}
