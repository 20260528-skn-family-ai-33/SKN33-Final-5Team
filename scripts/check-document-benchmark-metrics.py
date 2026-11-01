"""Known-answer checks of the exact pinned metric implementations (no models)."""
import csv
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
public = ROOT / 'data/bmt-public'
sys.path.insert(0, str(public / 'Pollock'))
from pollock.metrics import header_record_cell_measures_csv
sys.path.remove(str(public / 'Pollock'))  # Pollock/evaluate.py must not shadow the HF evaluate package.

with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    gold = folder / 'gold.csv'
    perfect = folder / 'perfect.csv'
    broken = folder / 'broken.csv'
    expected = [['code', 'name'], ['00123', '한글'], ['00007', '인용 "확인"\n다음 줄']]
    for path, rows in [(gold, expected), (perfect, expected), (broken, [['wrong'], ['missing']])]:
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            csv.writer(stream).writerows(rows)
    assert header_record_cell_measures_csv(str(gold), str(perfect), n_jobs=1) == (1.,) * 9
    assert header_record_cell_measures_csv(str(gold), str(broken), n_jobs=1) == (0.,) * 9
    sys.path.insert(0, str(public / 'OmniDocBench-v1_0'))
    from metrics.cal_metric import call_Edit_dist, call_TEDS
    old = Path.cwd()
    try:
        os.chdir(folder)
        Path('result').mkdir()
        for prediction, target in [('ABC', 0.), ('', 1.)]:
            samples = [dict(gt='ABC', pred=prediction, norm_gt='ABC', norm_pred=prediction, img_id='fixture.jpg', gt_attribute=[])]
            _, aggregate = call_Edit_dist(samples).evaluate(save_name='known-answer')
            assert aggregate['Edit_dist']['ALL_page_avg'] == target
        table = '<html><body><table><tr><td>00123</td></tr></table></body></html>'
        for prediction, target in [(table, 1.), ('', 0.)]:
            samples = [dict(gt=table, pred=prediction, norm_gt=table, norm_pred=prediction,
                            img_id='fixture.jpg', gt_idx=[0], gt_attribute=[])]
            _, aggregate = call_TEDS(samples).evaluate(save_name='known-answer')
            assert aggregate['TEDS']['all'] == target
    finally:
        os.chdir(old)
print('PASS: Pollock perfect/wrong predictions; OmniDocBench identical/empty Edit Distance and TEDS')
