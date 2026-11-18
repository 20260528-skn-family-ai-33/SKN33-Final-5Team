import pytest
from tests.documents.analysis.test_merged_repeats_and_ocr_contract import merged_case
from tests.documents.analysis.test_row_overlap_and_merge_evidence import pipeline_csv


def transform(ocr, scale, shift):
    ocr['rec_boxes'] = [[v * scale + shift for v in box] for box in ocr['rec_boxes']]


@pytest.mark.parametrize('repeated', [False, True])
@pytest.mark.parametrize('expansion', [(1, 1), (1, 0), (0, 1), (5, 5)])
@pytest.mark.parametrize('scale,shift', [(1, 0), (.25, 19), (3, 100)])
def test_existing_vertical_merge_allows_alignment_in_independent_gap(tmp_path, repeated, expansion, scale, shift):
    data, ocr = merged_case(vertical=True, repeated=repeated)
    for text, box in zip(ocr['rec_texts'], ocr['rec_boxes']):
        if text in ('Milk', 'Tea'):
            box[1] -= expansion[0]
            box[3] += expansion[1]
    transform(ocr, scale, shift)
    assert pipeline_csv(tmp_path, data, ocr, ['Count'], False) == [
        {'Count': str(r)} for r in range(1, 5)]


@pytest.mark.parametrize('failure', ['past_midpoint', 'other_row', 'shifted', 'huge_edge',
                                     'missing_internal', 'missing_support', 'duplicate', 'missing'])
@pytest.mark.parametrize('scale,shift', [(1, 0), (2, 17)])
def test_vertical_merge_uncertain_or_wrong_position_stays_blocked(tmp_path, failure, scale, shift):
    data, ocr = merged_case(vertical=True)
    indices = [i for i, text in enumerate(ocr['rec_texts']) if text == 'Milk']
    if failure == 'past_midpoint':
        ocr['rec_boxes'][indices[0]][3] = 85.001
    elif failure == 'other_row':
        ocr['rec_boxes'][indices[0]][3] = 90.001
    elif failure == 'shifted':
        ocr['rec_boxes'][indices[0]][1:4:2] = [90, 140]
    elif failure == 'huge_edge':
        ocr['rec_boxes'][indices[1]][3] = 250
    elif failure == 'missing_internal':
        # No independent row 3 evidence: do not extend the first merge into it.
        index = ocr['rec_texts'].index('3')
        ocr['rec_texts'][index] = ''
        next(c for c in data['cells'] if c['text'] == '3')['text'] = ''
        ocr['rec_boxes'][indices[0]][3] = 81
    elif failure == 'missing_support':
        index = ocr['rec_texts'].index('2')
        ocr['rec_texts'][index] = ''
        next(c for c in data['cells'] if c['text'] == '2')['text'] = ''
    elif failure == 'duplicate':
        ocr['rec_boxes'][indices[1]] = ocr['rec_boxes'][indices[0]][:]
    else:
        for values in ocr.values():
            values.pop(indices[1])
    transform(ocr, scale, shift)
    pipeline_csv(tmp_path, data, ocr, ['Count'], True)


@pytest.mark.parametrize('gap', [2, 10, 50])
@pytest.mark.parametrize('reverse', [False, True])
def test_different_row_gaps_and_centered_merge_text(tmp_path, gap, reverse):
    data, ocr = merged_case(vertical=True)
    # Independent glyph heights stay 20; row pitch varies. Text in the second
    # merge may be centered rather than filling its logical span.
    pitch = 20 + gap
    for text, box in zip(ocr['rec_texts'], ocr['rec_boxes']):
        if text == 'Milk':
            start = round(box[1] / 30)
            box[1], box[3] = start*pitch-gap/4, (start+1)*pitch+20+gap/4
            if start == 3:
                midpoint = (box[1]+box[3])/2
                box[1], box[3] = midpoint-5, midpoint+5
        else:
            start = round(box[1]/30)
            box[1], box[3] = start*pitch, start*pitch+20
    if reverse:
        ocr = {key: list(reversed(values)) for key, values in ocr.items()}
    assert pipeline_csv(tmp_path, data, ocr, ['Count'], False) == [
        {'Count': str(r)} for r in range(1, 5)]


@pytest.mark.parametrize('edge', ['top', 'bottom'])
@pytest.mark.parametrize('declared_missing', [False, True])
def test_real_edge_can_expand_but_unobserved_logical_neighbor_cannot(edge, declared_missing):
    from documents.analysis.table_quality import reconcile
    data, ocr = merged_case(vertical=True)
    # Move the unique boundary reference to the bottom for a genuine top
    # merge. Keep it above the merges for the bottom-edge case.
    if edge == 'top':
        for cell in data['cells'][:2]:
            cell['row'] = 5
        for box in ocr['rec_boxes'][:2]:
            box[1], box[3] = 150, 170
        for cell in data['cells']:
            cell['row'] -= 1
        for box in ocr['rec_boxes']:
            box[1] -= 30
            box[3] -= 30
    if declared_missing:
        if edge == 'top':
            for cell in data['cells']:
                cell['row'] += 1
        else:
            data['num_rows'] = 6
    indices = [i for i, text in enumerate(ocr['rec_texts']) if text == 'Milk']
    if edge == 'top':
        ocr['rec_boxes'][indices[0]][1] -= 1
    else:
        ocr['rec_boxes'][indices[-1]][3] += 1
    cells, quality = reconcile(data, ocr)
    assert cells == data['cells'] and not quality['repairs']
    assert bool(quality['issues']) is declared_missing
