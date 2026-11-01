"""Score immutable HTTP/SQLite evidence with the pinned public evaluators."""
import argparse
import collections
import csv
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda v: float(v)) + '\n', encoding='utf-8')


def markdown(analysis):
    pieces = []
    for block in analysis.get('blocks', []):
        if not block.get('selected', True):
            continue
        text = block.get('text', '')
        if 'table' in block.get('content_types', []):
            text = block.get('data', {}).get('html') or text
        elif 'formula' in block.get('content_types', []) and text.strip() and not text.lstrip().startswith('$$'):
            text = '$$\n' + text + '\n$$'
        if text.strip():
            pieces.append(text)
    return '\n\n'.join(pieces)


def preserved(expected, asset, rows, analysis):
    target = expected['rows']
    matches = 0
    if asset.get('kind') == 'table':
        for index, row in enumerate(target):
            for key, value in row.items():
                actual = rows[index].get(key, object()) if index < len(rows or []) else object()
                matches += type(actual) is type(value) and actual == value
        result = dict(exact_cells=matches, expected_cells=sum(len(row) for row in target),
                      exact_rows=rows == target, schema_match=asset.get('columns') == expected['columns'])
    else:
        result = dict(exact_tokens=sum(token in asset.get('text', '') for token in expected['tokens']),
                      expected_tokens=len(expected['tokens']), literal_text_match=expected['text'].strip() in asset.get('text', ''))
    if expected.get('structure'):
        blocks = [b for b in analysis.get('blocks', []) if b.get('selected', True)]
        gold = expected['structure']
        native_sheets = {b['native_location'].get('sheet') for b in blocks}
        result['structure'] = dict(sheet_names=all(name in native_sheets for name in gold['sheet_names']),
            merged=any(gold['merged'] in b.get('data', {}).get('merged_ranges', []) for b in blocks),
            formula=any(b.get('data', {}).get('formula') == gold['formula'].lstrip('=') for b in blocks),
            audit_string=any(b['native_location'].get('sheet') == 'Audit' and b['native_location'].get('cell') == 'A2'
                             and b.get('data', {}).get('value') == gold['audit_string'] for b in blocks))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    manifest = read(args.manifest)
    cases = {case['id']: case for case in manifest['cases']}
    corpus = args.manifest.resolve().parent
    ledger = read(run / 'progress.json')
    output = run / 'scores'
    output.mkdir(exist_ok=True)
    public = ROOT / 'data/bmt-public'
    sys.path.insert(0, str(public / 'Pollock'))
    from pollock.metrics import header_record_cell_measures_csv
    sys.path.remove(str(public / 'Pollock'))  # Avoid its top-level evaluate.py during Omni imports.
    names = ['header_precision', 'header_recall', 'header_f1', 'record_precision', 'record_recall', 'record_f1', 'cell_precision', 'cell_recall', 'cell_f1']
    scores = []
    for result in ledger:
        case = cases[result['corpus_id']]
        artifact = run / result['artifact']
        asset = read(artifact / 'asset.json') if (artifact / 'asset.json').exists() else {}
        rows = read(artifact / 'rows.json') if (artifact / 'rows.json').exists() else []
        analysis = read(artifact / 'analysis.json') if (artifact / 'analysis.json').exists() else {}
        score = dict(instance_id=result['instance_id'], corpus_id=case['id'], arm=result['arm'], bundle_id=result['bundle_id'],
                     suite=case['suite'], format=case['format'], upload_status=result['status'], parser_status=result.get('parser_status'),
                     mandatory_both_succeeded=result.get('mandatory_both_succeeded'), upload_elapsed_seconds=result['upload_elapsed_seconds'])
        if case['suite'] == 'pollock':
            metrics = [0.] * 9
            if result['status'] == 'succeeded':
                prediction = artifact / 'loaded.csv'
                with prediction.open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=asset['columns'], extrasaction='raise')
                    writer.writeheader(); writer.writerows(rows)
                metrics = header_record_cell_measures_csv(str(corpus / case['gold']), str(prediction), n_jobs=1)
            score['metrics'] = dict(zip(names, map(float, metrics)))
            score['interpretation'] = 'shared_service_csv_loader_validation'
        elif case['suite'] == 'preservation':
            expected = read(corpus / case['gold'])
            inferred_kind = 'table' if case['format'] in {'csv', 'json', 'xlsx'} else 'document'
            score['preservation'] = preserved(expected, {**asset, 'kind': asset.get('kind', inferred_kind)}, rows, analysis)
        scores.append(score)
    save(output / 'file-quality.json', scores)
    # Empty prediction files are deliberately written for failures: upstream
    # otherwise skips files with no prediction on disk.
    omni_scores = {}
    if any(case['suite'] == 'omnidocbench' for case in cases.values()) and any(r['suite'] == 'omnidocbench' for r in ledger):
        sys.path.insert(0, str(public / 'OmniDocBench-v1_0'))
        from dataset.end2end_dataset import End2EndDataset
        from metrics.cal_metric import call_Edit_dist, call_TEDS
        for arm in ['A', 'B']:
            prediction = output / f'omni-{arm}'
            prediction.mkdir(exist_ok=True)
            subset = []
            all_gold = read(corpus / 'gold/omni-selected.json')
            lookup = {Path(g['page_info']['image_path']).name: g for g in all_gold}
            for result in ledger:
                case = cases[result['corpus_id']]
                if result['arm'] != arm or case['suite'] != 'omnidocbench':
                    continue
                subset.append(lookup[case['image_id']])
                artifact = run / result['artifact']
                analysis_file = artifact / 'analysis.json'
                content = markdown(read(analysis_file)) if result['status'] == 'succeeded' and analysis_file.exists() else ''
                (prediction / (case['image_id'][:-4] + '.md')).write_text(content, encoding='utf-8')
            gold_path = output / f'omni-{arm}-gold.json'
            save(gold_path, subset)
            cfg = dict(dataset=dict(ground_truth=dict(data_path=str(gold_path)), prediction=dict(data_path=str(prediction)), match_method='quick_match'))
            old_cwd = Path.cwd()
            try:
                os.chdir(output)
                Path('result').mkdir(exist_ok=True)
                dataset = End2EndDataset(cfg)
                arm_scores = {}
                for category in ['text_block', 'table', 'reading_order']:
                    samples = dataset.samples[category]
                    names_for_category = [('TEDS', call_TEDS), ('Edit_dist', call_Edit_dist)] if category == 'table' else [('Edit_dist', call_Edit_dist)]
                    arm_scores[category] = {}
                    for metric_name, metric in names_for_category:
                        evaluated, aggregate = metric(samples).evaluate(save_name=f'{arm}_{category}')
                        arm_scores[category].update(aggregate)
                        sample_list = evaluated if isinstance(evaluated, list) else evaluated.samples
                        arm_scores[category]['sample_count'] = len(sample_list)
                        save(output / f'omni-{arm}-{category}-{metric_name}-samples.json', sample_list)
                omni_scores[arm] = arm_scores
            finally:
                os.chdir(old_cwd)
        save(output / 'omnidocbench-official.json', omni_scores)
    aggregates = {}
    for arm in ['A', 'B']:
        selected = [s for s in scores if s['arm'] == arm]
        primary = [s for s in selected if s['bundle_id'].startswith('single-')]
        pollock = [s for s in primary if s['suite'] == 'pollock']
        pollock_success = [s for s in pollock if s['upload_status'] == 'succeeded']
        aggregates[arm] = dict(files=len(primary), upload_success=sum(s['upload_status'] == 'succeeded' for s in primary),
            parser_partial=sum(s.get('parser_status') == 'partial' for s in primary),
            mandatory_both_succeeded=sum(s.get('mandatory_both_succeeded') is True for s in primary),
            pollock_cases=len(pollock), pollock_success=sum(s['upload_status'] == 'succeeded' for s in pollock),
            pollock_all_files_macro={name: statistics.mean(s['metrics'][name] for s in pollock) for name in names} if pollock else {},
            pollock_success_only_macro={name: statistics.mean(s['metrics'][name] for s in pollock_success) for name in names} if pollock_success else {},
            file_elapsed_seconds=sum(s['upload_elapsed_seconds'] for s in primary))
    summary = dict(arms=aggregates, omnidocbench=omni_scores,
                   interpretation=dict(csv='공통 CSV 로더 검증. PaddleOCR 대체 효과 지표가 아님.',
                                       preservation='정답을 파일 생성 전에 정한 추가 보존 시험. 논문 벤치마크와 별도.',
                                       pdf='OmniDocBench v1_0 공식 매칭과 정규화 사용. 18페이지 표본 결과이며 전체 데이터 점수가 아님.'))
    save(output / 'summary.json', summary)
    print(json.dumps(dict(output=str(output), arms=aggregates), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
