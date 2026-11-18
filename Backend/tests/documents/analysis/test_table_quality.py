import copy
import json
from pathlib import Path

import pytest
from documents.analysis.table_quality import reconcile, position
from workflow_engine.executors.document.table_rows import native_records, TableStructureError


def receipt():
    return json.loads((Path(__file__).parents[2] / 'fixtures/receipt_table_ocr.json').read_text())


def test_recorded_receipt_repairs_only_three_missing_values_and_preserves_original():
    fixture = receipt()
    original = copy.deepcopy(fixture)
    cells, report = reconcile(fixture, fixture['ocr'])
    assert fixture == original
    assert [(r['row'], r['column'], r['text']) for r in report['repairs']] == [
        (5, 0, '초콜릿'), (5, 1, '2'), (8, 3, '-420')]
    assert all(c in cells for c in original['cells'])
    # Sparse receipt summary rows cannot be declared blank from absent objects.
    assert [i['kind'] for i in report['issues']] == ['unknown_cell_slots']
    part = dict(file_id='synthetic', file_name='synthetic.png', source_id='table',
                table_cells=cells, review_required=True)
    with pytest.raises(TableStructureError):
        native_records([part], ['상품명', '수량', '단가(원)', '금액(원)'], [], 100)


def simple():
    data = {'cells': [dict(row=r, column=c, text=t) for r, row in enumerate([
        ['Item', 'Count'], ['Milk', '1'], ['Chocolate', '2']]) for c, t in enumerate(row)]}
    ocr = dict(rec_texts=['Item', 'Count', 'Milk', '1', 'Chocolate', '2'],
               rec_boxes=[[c*100, r*30, c*100+50, r*30+20] for r in range(3) for c in range(2)],
               rec_scores=[.999]*6)
    return data, ocr


def test_complete_table_recovery_flows_to_native_csv_records():
    data, ocr = simple()
    data['cells'].pop()
    cells, report = reconcile(data, ocr)
    assert report['issues'] == []
    part = dict(file_id='synthetic', file_name='synthetic.png', source_id='table', table_cells=cells)
    records = native_records([part], ['Item', 'Count'], [], 10)
    assert [(r['Item'], r['Count']) for r in records] == [('Milk', '1'), ('Chocolate', '2')]
    assert records[-1]['row_coverage'] == 'complete'


@pytest.mark.parametrize('mode', ['low_score', 'overlap_rows', 'duplicate', 'missing_boxes', 'invalid_box', 'conflict'])
def test_uncertainty_never_fills_or_overwrites(mode):
    data, ocr = simple()
    if mode != 'conflict':
        data['cells'].pop()
    if mode == 'low_score': ocr['rec_scores'][-1] = .8
    if mode == 'overlap_rows': ocr['rec_boxes'][-1] = [100, 20, 150, 50]
    if mode == 'duplicate':
        for k in ocr: ocr[k].append(copy.deepcopy(ocr[k][-1]))
    if mode == 'missing_boxes': ocr['rec_boxes'] = []
    if mode == 'invalid_box': ocr['rec_boxes'][-1][0] = float('nan')
    if mode == 'conflict': ocr['rec_texts'][-1] = '9'
    cells, report = reconcile(data, ocr)
    assert report['issues']
    assert cells == data['cells']


def test_explicit_blank_and_merged_slots_are_not_missing_cells():
    data, ocr = simple()
    data['cells'][-1]['text'] = ''
    for k in ocr: ocr[k].pop()
    cells, report = reconcile(data, ocr)
    assert cells == data['cells'] and not report['repairs']
    data['cells'][-2]['col_span'] = 2
    data['cells'].pop()
    cells, report = reconcile(data, ocr)
    assert cells == data['cells'] and not report['repairs']
    assert not any(isinstance(i, dict) and i['kind'] == 'unknown_cell_slots' for i in report['issues'])


def test_duplicate_cell_structure_requires_review():
    data, ocr = simple()
    data['cells'].append(copy.deepcopy(data['cells'][0]))
    _, report = reconcile(data, ocr)
    assert 'overlapping_cells' in report['issues']


def test_pipeline_replay_marks_sparse_receipt_partial_and_preserves_evidence(tmp_path):
    from PIL import Image
    from documents.analysis.pipeline import run
    fixture = receipt()

    class Replay:
        attempts = []
        def run(self, engine, **kwargs):
            base = dict(engine=engine, version='recorded-regression', model={}, partial=False)
            if engine == 'layout':
                return {**base, 'outputs': [dict(source=str(p), result={'boxes': [
                    dict(label='table', score=.999, coordinate=[0, 0, 1000, 1250])]}) for p in kwargs['sources']]}
            if engine == 'paddleocr':
                return {**base, 'outputs': [dict(source=str(p), result=fixture['ocr']) for p in kwargs['sources']]}
            return {**base, 'blocks': [dict(kind='table', text='original missing cells',
                data={'cells': fixture['cells'], 'num_rows': 13, 'num_cols': 4})]}

    source = tmp_path / 'synthetic.png'
    Image.new('RGB', (1000, 1250), 'white').save(source)
    payload = run(source, tmp_path / 'work', engines=Replay())
    analysis = payload['document_analysis']
    assert analysis['status'] == 'partial' and analysis['review_required']
    assert any(w.startswith('table_quality_unresolved:') for w in analysis['warnings'])
    table = next(b for b in analysis['blocks'] if b['engine'] == 'docling')
    assert table['status'] == 'review_required'
    assert table['data']['original_cells'] == fixture['cells']
    assert len(table['data']['table_quality']['repairs']) == 3
    assert '초콜릿' in payload['text'] and '-420' in payload['text']
    region = next(b for b in analysis['blocks'] if b['engine'] == 'layout')
    assert region['data']['table_ocr']['rec_boxes'] == fixture['ocr']['rec_boxes']


def test_recovered_complete_table_executes_csv_export(tmp_path):
    import csv
    import io
    from workflow_engine.executors.output.export_result import execute
    data, ocr = simple()
    data['cells'].pop()
    cells, report = reconcile(data, ocr)
    assert not report['issues']
    part = dict(file_id='synthetic', file_name='synthetic.png', source_id='table', table_cells=cells)
    records = native_records([part], ['Item', 'Count'], [], 10)
    node = dict(id='export', tool='export_result', settings={'format': 'csv', 'fields': ['Item', 'Count']},
                bindings=[dict(input='records', node_id='extract', output='records')])
    result = execute(node, {'outputs': {'extract': {'records': records}}}, None, {}, None)
    path = tmp_path / 'result.csv'
    path.write_text(result['artifact']['content'], encoding='utf-8')
    with path.open(encoding='utf-8-sig') as stream:
        assert list(csv.DictReader(stream)) == [{'Item': 'Milk', 'Count': '1'}, {'Item': 'Chocolate', 'Count': '2'}]


@pytest.mark.parametrize('scale,dx,dy', [(1, -160, -450), (2, 30, 60)])
def test_reconciliation_uses_crop_geometry_without_docling_point_pixel_assumptions(scale, dx, dy):
    fixture = receipt()
    for box in fixture['ocr']['rec_boxes']:
        box[:] = [box[0]*scale+dx, box[1]*scale+dy, box[2]*scale+dx, box[3]*scale+dy]
    _, report = reconcile(fixture, fixture['ocr'])
    assert [(r['row'], r['column'], r['text']) for r in report['repairs']] == [
        (5, 0, '초콜릿'), (5, 1, '2'), (8, 3, '-420')]


def test_missing_ocr_text_does_not_prove_a_complete_table():
    data, ocr = simple()
    for k in ocr: ocr[k].pop()
    _, report = reconcile(data, ocr)
    assert any(i.get('kind') == 'cell_not_confirmed_by_ocr' for i in report['issues'] if isinstance(i, dict))
