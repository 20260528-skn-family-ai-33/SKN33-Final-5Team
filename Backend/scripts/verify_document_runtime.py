"""Run local source files through the real router, then inspect scoped fields.

The case manifest contains id/input/original/sha256/truth_path. Truth is loaded
only after parsing and is never passed to a parser or model. Output is an
execution/field-preservation check, not an overall document-accuracy BMT.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_analysis.contracts import sha256, validate
from document_analysis.pipeline import run


def compact(text):
    return re.sub(r'\s+', '', str(text))


def load_truth(case):
    truth = json.loads(Path(case['truth_path']).read_text(encoding='utf-8'))
    pointer = case.get('truth_pointer', '').lstrip('/')
    for part in pointer.split('/') if pointer else []:
        truth = truth[part.replace('~1', '/').replace('~0', '~')]
    return truth


def expected_fields(case):
    truth = load_truth(case)
    if isinstance(truth.get('fields'), dict):
        return {k: str(v) for k, v in truth['fields'].items() if isinstance(v, (str, int, float))}
    names = case.get('field_names', ['person', 'resident_id', 'phone', 'bank', 'account'])
    return {key: str(truth[key]) for key in names if key in truth}


def inspect_fields(text, expected):
    return dict(total=len(expected), exact=[k for k, v in expected.items() if v in text],
                whitespace_normalized=[k for k, v in expected.items() if compact(v) in compact(text)],
                missing=[k for k, v in expected.items() if compact(v) not in compact(text)])


def structural_checks(payload, truth, primary_text):
    blocks = payload['document_analysis']['blocks']
    checks = {}
    gold_tables = truth.get('tables') or []
    actual_tables = [b for b in blocks if b['engine'] == 'docling' and 'table' in b['content_types'] and b.get('phase') == 'primary']
    if gold_tables:
        exact = normalized = merged = total = merged_total = 0
        for table_index, table in enumerate(gold_tables):
            actual = actual_tables[table_index]['data'].get('cells', []) if table_index < len(actual_tables) else []
            actual = {(c.get('start_row_offset_idx', c.get('row')), c.get('start_col_offset_idx', c.get('column'))): c for c in actual}
            base = table.get('index_base', 1)
            for cell in table['cells']:
                candidate = actual.get((cell['row'] - base, cell['column'] - base))
                total += 1
                if candidate:
                    exact += candidate['text'] == cell['text']
                    normalized += compact(candidate['text']) == compact(cell['text'])
                if cell['row_span'] > 1 or cell['col_span'] > 1:
                    merged_total += 1
                    merged += bool(candidate and candidate.get('row_span') == cell['row_span'] and candidate.get('col_span') == cell['col_span'])
        checks['table_cells'] = dict(total=total, exact=exact, whitespace_normalized=normalized)
        checks['merged_spans'] = dict(total=merged_total, matched=merged)
    order = truth.get('reading_order') or []
    if order:
        positions = {anchor: primary_text.find(anchor) for anchor in order}
        pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1:]]
        checks['reading_order_anchor_pairs'] = dict(total=len(pairs), matched=sum(0 <= positions[a] < positions[b] for a, b in pairs))
    consent = truth.get('required_consent') or {}
    if consent.get('present') and consent.get('literal'):
        checks['checkbox_literal'] = dict(exact=consent['literal'] in primary_text,
                                           whitespace_normalized=compact(consent['literal']) in compact(primary_text),
                                           independent_state_detection_measured=False)
    if truth.get('signature_literal'):
        checks['signature_literal'] = truth['signature_literal'] in primary_text
    sheet_truth = truth.get('spreadsheet') or {}
    if sheet_truth and payload['document_analysis']['source_format'] == 'xls':
        sheets = [b for b in blocks if 'sheet' in b['content_types'] and b['native_location']['sheet'] == sheet_truth['sheet']]
        ranges = sheets[0]['data']['merged_ranges'] if sheets else []
        checks['native_spreadsheet_merges'] = dict(total=len(sheet_truth['mandatory_merges']), matched=sum(r in ranges for r in sheet_truth['mandatory_merges']))
        cells = {b['native_location']['cell']: b for b in blocks if 'cell' in b['content_types'] and b['native_location']['sheet'] == sheet_truth['sheet']}
        sample = sheet_truth['numeric_amount_example']
        cell = cells.get(sample['cell'])
        checks['native_numeric_cell'] = bool(cell and cell['data']['value_type'] == 'n' and float(cell['data']['raw_value']) == sample['value'])
        checks['native_blank_examples'] = dict(total=len(sheet_truth['blank_examples']),
            matched=sum(c not in cells or cells[c]['data']['raw_value'] is None and cells[c]['data']['formula'] is None for c in sheet_truth['blank_examples']))
    return checks


def execute(cases, output, prior_report=None):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    originals = {str(Path(c['original']).resolve()): sha256(c['original']) for c in cases}
    prior = {r['id']: r for r in json.loads(prior_report.read_text(encoding='utf-8'))['cases']} if prior_report else {}
    for case in cases:
        started = time.perf_counter()
        folder = output / case['id']
        folder.mkdir()
        row = dict(id=case['id'], source_format=Path(case['input']).suffix[1:].lower(),
                   input_sha256=sha256(case['input']), status='failed')
        try:
            if row['input_sha256'] != case['sha256']:
                raise ValueError('입력 스냅샷 해시가 일치하지 않습니다.')
            result_path = Path(prior[case['id']]['result_path']) if prior_report else folder / 'result.json'
            payload = json.loads(result_path.read_text(encoding='utf-8')) if prior_report else run(case['input'], folder)
            if prior_report and payload['document_analysis']['source_sha256'] != row['input_sha256']:
                raise ValueError('기존 실행 결과의 원본 해시가 일치하지 않습니다.')
            if not prior_report:
                result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            analysis = payload['document_analysis']
            validate(analysis)
            expected = expected_fields(case)  # Deliberately after all engine requests.
            selected = inspect_fields(payload['text'], expected)
            primary_text = '\n'.join(b['text'] for b in analysis['blocks'] if b['engine'] == 'docling' and b.get('phase') == 'primary')
            primary = inspect_fields(primary_text, expected) if analysis['primary_processor'] == 'docling' else None
            routes = analysis['routes']
            engine_ok = (any(r['processor'] == 'docling' and r['phase'] == 'primary' and r['status'] == 'succeeded' for r in routes)
                         if analysis['primary_processor'] == 'docling' else analysis['primary_processor'] == 'native')
            row.update(status='passed' if engine_ok else 'failed', parser_status=analysis['status'],
                       primary_processor=analysis['primary_processor'], selected_fields=selected, primary_fields=primary,
                       review_reasons=analysis['review_reasons'], block_count=len(analysis['blocks']),
                       cell_count=sum(b['content_types'] == ['cell'] for b in analysis['blocks']),
                       table_count=sum('table' in b['content_types'] for b in analysis['blocks']),
                       routes=[{k: r[k] for k in ['processor', 'phase', 'status', 'elapsed_seconds', 'version', 'error_code'] if k in r} for r in routes],
                       conversions=analysis['conversions'], result_path=str(result_path),
                       structural_checks=structural_checks(payload, load_truth(case), primary_text),
                       engine_execution_succeeded=engine_ok, review_required=analysis['review_required'])
        except Exception as error:
            row.update(error_type=type(error).__name__, error=str(error))
            (folder / 'failure.log').write_text(traceback.format_exc(), encoding='utf-8')
        row['evaluation_elapsed_seconds'] = time.perf_counter() - started
        row['elapsed_seconds'] = prior[case['id']]['elapsed_seconds'] if prior_report else row['evaluation_elapsed_seconds']
        results.append(row)
        (output / 'progress.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        fields = row.get('selected_fields', {})
        print(json.dumps(dict(id=row['id'], status=row['status'], parser_status=row.get('parser_status'),
                              fields_found=len(fields.get('whitespace_normalized', [])), fields_total=fields.get('total'),
                              elapsed_seconds=round(row['elapsed_seconds'], 3)), ensure_ascii=False), flush=True)
    preserved = {p: sha256(p) == before for p, before in originals.items()}
    report = dict(created_at=datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                  scope='real local engine execution and scoped authored-field preservation',
                  overall_document_accuracy_measured=False, production_holdout=False,
                  authored_hwp_truth_sets=len({c['truth_path'] for c in cases if c['id'].startswith('HWP-')}),
                  inference_reexecuted=prior_report is None,
                  prior_report=str(prior_report) if prior_report else None,
                  prior_report_sha256=sha256(prior_report) if prior_report else None,
                  cases=results, counts=dict(total=len(results), passed=sum(r['status'] == 'passed' for r in results)),
                  original_files_preserved=preserved,
                  field_counts=dict(total=sum(r.get('selected_fields', {}).get('total', 0) for r in results),
                      exact=sum(len(r.get('selected_fields', {}).get('exact', [])) for r in results),
                      whitespace_normalized=sum(len(r.get('selected_fields', {}).get('whitespace_normalized', [])) for r in results)),
                  counts_mean='Required processors executed; field and structure checks are reported separately.',
                  validation_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  python=sys.version.split()[0])
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'execution.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id', 'source_format', 'status', 'parser_status', 'primary_processor', 'fields_found', 'fields_total', 'elapsed_seconds'])
        writer.writeheader()
        for row in results:
            fields = row.get('selected_fields', {})
            writer.writerow({**{k: row.get(k) for k in writer.fieldnames if not k.startswith('fields_')},
                             'fields_found': len(fields.get('whitespace_normalized', [])), 'fields_total': fields.get('total')})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prior-report', type=Path, help='기존 실제 출력에 평가만 추가; 엔진을 다시 실행하지 않음')
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding='utf-8'))
    os.environ.update(settings)
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    output = args.output.resolve() / datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d-%H%M%S') / uuid.uuid4().hex[:8]
    report = execute(cases, output, args.prior_report.resolve() if args.prior_report else None)
    print('REPORT', output / 'summary.json', flush=True)
    return 0 if report['counts']['passed'] == len(cases) and all(report['original_files_preserved'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
