import copy
import pytest
from PIL import Image
from documents.analysis.pipeline import run
from documents.analysis.table_quality import reconcile
from tests.documents.analysis.test_repair_set_consistency import case, pipeline_check
from tests.documents.analysis.test_merged_repeats_and_ocr_contract import merged_case
from tests.documents.analysis.test_table_quality_boundaries import Replay
from workflow_engine.executors.document.table_rows import native_records, TableStructureError


@pytest.mark.parametrize('key', ['num_rows', 'num_cols'])
@pytest.mark.parametrize('value', [2, 0, -1, '2', None, True, 2.5, 20001])
def test_invalid_dimensions_never_publish_repairs(tmp_path, key, value):
    data, ocr = case('both', shift=0)
    data.update(num_rows=4, num_cols=3)
    data[key] = value
    before = copy.deepcopy(data)
    cells, quality = reconcile(data, ocr)
    assert cells == before['cells'] and data == before
    assert quality['repairs'] == [] and 'invalid_table_dimensions' in quality['issues']

    class Dimensions(Replay):
        def run(self, engine, **kwargs):
            result = super().run(engine, **kwargs)
            if engine == 'docling':
                result['blocks'][0]['data'].update(copy.deepcopy(data), html='<table>original</table>')
            return result

    source = tmp_path/'test.png'
    Image.new('RGB', (200, 200), 'white').save(source)
    payload = run(source, tmp_path/'work', engines=Dimensions(ocr, data['cells']))
    table = next(b for b in payload['document_analysis']['blocks'] if b['engine'] == 'docling')
    assert payload['parser_status'] == 'partial' and payload['warnings']
    assert table['status'] == 'review_required'
    assert table['data']['cells'] == before['cells'] and len(table['data']['cells']) == 10
    assert table['data']['table_quality']['repairs'] == []
    assert 'invalid_table_dimensions' in table['data']['table_quality']['issues']
    assert 'original_cells' not in table['data']
    assert table['text'] == 'BASE TABLE' and table['data']['html'] == '<table>original</table>'
    with pytest.raises(TableStructureError):
        native_records([dict(file_id='t', file_name='t', source_id='t',
                            table_cells=table['data']['cells'], review_required=True)],
                       ['Item', 'Count', 'Price'], [], 100)


@pytest.mark.parametrize('declared', [False, True])
def test_valid_dimensions_keep_normal_recovery_and_csv(tmp_path, declared):
    data, ocr = case('both', shift=0)
    if declared:
        data.update(num_rows=4, num_cols=3)
    cells, quality = reconcile(data, ocr)
    assert len(cells) == 12 and len(quality['repairs']) == 2 and not quality['issues']
    pipeline_check(tmp_path, data, ocr, False)


@pytest.mark.parametrize('key,value', [('num_rows', 4), ('num_cols', 1)])
def test_merged_range_must_fit_dimensions(key, value):
    data, ocr = merged_case(vertical=True)
    data.update(num_rows=5, num_cols=3)
    data[key] = value
    cells, quality = reconcile(data, ocr)
    assert cells == data['cells'] and quality['repairs'] == []
    assert 'invalid_table_dimensions' in quality['issues']


@pytest.mark.parametrize('span_key,dimension', [('row_span', 'num_rows'), ('col_span', 'num_cols')])
def test_only_merged_end_outside_declared_size_is_invalid(span_key, dimension):
    # The start is in bounds, and no separate cell exposes the overflow.
    data = dict(cells=[dict(row=0, column=0, text='Merged', **{span_key: 2})], num_rows=2, num_cols=2)
    data[dimension] = 1
    ocr = dict(rec_texts=['Merged'], rec_boxes=[[0, 0, 20, 20]], rec_scores=[.999])
    cells, quality = reconcile(data, ocr)
    assert cells == data['cells'] and quality['repairs'] == []
    assert quality['issues'] == ['invalid_table_dimensions']


def test_declared_area_limit_rejected_before_recovery():
    data, ocr = case('both', shift=0)
    data.update(num_rows=101, num_cols=200)
    cells, quality = reconcile(data, ocr)
    assert cells == data['cells'] and quality['repairs'] == []
    assert quality['issues'] == ['invalid_table_dimensions']
