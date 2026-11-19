import copy
import pytest
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_table_quality import review_case
from tests.documents.analysis.test_row_overlap_and_merge_evidence import pipeline_csv


def sample():
    return review_case([['Item','Count'],['Bread','2'],['Milk','1'],['Coffee','3']], [
        ('Item',[0,0,35,20]),('Count',[70,0,105,20]),
        ('Bread',[0,30,35,50]),('2',[70,30,105,50]),
        ('Milk',[0,90,35,110]),('1',[70,90,105,110]),
        ('Coffee',[0,150,35,170]),('3',[70,150,105,170])])


@pytest.mark.parametrize('box', [[70,90,105,110],[70,70,105,105],[70,95,105,130],
                               [70,69.999,105,105],[70,70,105,110]])
@pytest.mark.parametrize('reverse', [False,True])
@pytest.mark.parametrize('scale,offset', [(1,0),(1.5,17)])
def test_unconfirmed_duplicate_cannot_disappear_at_occupied_slot(tmp_path, box, reverse, scale, offset):
    data, ocr = sample()
    ocr['rec_texts'].append('1'); ocr['rec_boxes'].append(box[:]); ocr['rec_scores'].append(.999)
    ocr['rec_boxes'] = [[v*scale+offset for v in b] for b in ocr['rec_boxes']]
    if reverse: ocr = {k:list(reversed(v)) for k,v in ocr.items()}
    before = copy.deepcopy(data)
    cells, report = reconcile(data, ocr)
    assert cells == before['cells'] and data == before and not report['repairs']
    assert report['issues']
    if box == [70,70,105,105]:
        issue = next(i for i in report['issues'] if isinstance(i, dict)
                     and i.get('kind') == 'unmatched_existing_cell_ocr')
        assert issue['slot'] == [2, 1]
        assert ocr['rec_boxes'][issue['ocr_index']] == [v*scale+offset for v in box]
    pipeline_csv(tmp_path, data, ocr, ['Item','Count'], True)


@pytest.mark.parametrize('reverse', [False,True])
def test_single_confirmed_ocr_keeps_normal_csv(tmp_path, reverse):
    data, ocr = sample()
    if reverse: ocr = {k:list(reversed(v)) for k,v in ocr.items()}
    assert pipeline_csv(tmp_path,data,ocr,['Item','Count'],False) == [
        {'Item':'Bread','Count':'2'}, {'Item':'Milk','Count':'1'}, {'Item':'Coffee','Count':'3'}]
