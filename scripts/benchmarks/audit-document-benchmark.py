"""Audit complete benchmark ledgers without changing model predictions or scores."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--mixed-manifest', type=Path, required=True)
    parser.add_argument('--primary', type=Path, required=True)
    parser.add_argument('--mixed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    corpus = args.manifest.resolve().parent
    manifest = read(args.manifest)
    cases = {c['id']: c for c in manifest['cases']}
    assert len(cases) == 179
    for case in cases.values():
        assert sha(corpus / case['input']) == case['sha256'], case['id']
        if 'gold' in case:
            assert sha(corpus / case['gold']) == case['gold_sha256'], case['id']
    mixed_manifest = read(args.mixed_manifest)
    assert mixed_manifest['cases'] == manifest['cases']
    assert collections.Counter(c['corpus_id'] for b in mixed_manifest['bundles'] for c in b['cases']) == collections.Counter(cases.keys())
    all_records, audits = [], []
    for run, plan, manifest_path in [(args.primary, manifest, args.manifest), (args.mixed, mixed_manifest, args.mixed_manifest)]:
        context, summary = read(run / 'execution-context.json'), read(run / 'summary.json')
        assert summary['complete'] and context['human_review'] is False
        assert context['concurrency'] == 1 and context['manifest_sha256'] == sha(manifest_path)
        jobs = []
        if context['phase'] == 'all':
            jobs += [dict(id='single-' + c['id'], cases=[dict(corpus_id=c['id'])]) for c in plan['cases']]
        jobs += plan['bundles']
        expected = {f"{job['id']}-{i:03d}-{arm}": (case, job['id'], arm)
                    for job in jobs for arm in ['A', 'B'] for i, case in enumerate(job['cases'], 1)}
        records = read(run / 'progress.json')
        assert len(records) == len(expected) == summary['records']
        assert len({r['instance_id'] for r in records}) == len(records)
        assert {r['instance_id'] for r in records} == set(expected)
        scores = read(run / 'scores/file-quality.json')
        assert {s['instance_id'] for s in scores} == set(expected) and len(scores) == len(records)
        by_score = {s['instance_id']: s for s in scores}
        for record in records:
            occurrence, bundle, arm = expected[record['instance_id']]
            case = cases[occurrence['corpus_id']]
            assert record['corpus_id'] == case['id'] and record['source_sha256'] == case['sha256']
            assert record['bundle_id'] == bundle and record['arm'] == arm
            assert record['upload_name'] == occurrence.get('upload_name', case['name'])
            artifact = run / record['artifact']
            assert read(artifact / 'record.json') == record
            score = by_score[record['instance_id']]
            assert score['upload_status'] == record['status']
            if record['status'] == 'succeeded':
                asset = read(artifact / 'asset.json')
                assert asset['hash'] == case['sha256'] and asset['name'] == record['upload_name']
                assert record['original_preserved'] and (artifact / 'rows.json').exists()
                assert not record.get('source_mismatch') and not record.get('filename_mismatch')
                if arm == 'B':
                    assert record.get('mandatory_both_attempted') is True, record['instance_id']
            elif case['suite'] == 'pollock':
                assert all(value == 0 for value in score['metrics'].values())
        bundles = read(run / 'bundles.json')
        assert len(bundles) == len(jobs) * 2
        for arm in ['A', 'B']:
            selected = [r for r in records if r['arm'] == arm]
            db_path = run / arm / 'service-data/agent-builder.sqlite'
            with sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True) as db:
                review_counts = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                                 for table in ['document_reviews', 'document_review_events']}
                assert all(count == 0 for count in review_counts.values())
                asset_count = db.execute('SELECT count(*) FROM builder_assets').fetchone()[0]
                full_rows_verified = 0
                for record in selected:
                    if record['status'] != 'succeeded':
                        continue
                    stored = db.execute('SELECT json,rows_json FROM builder_assets WHERE id=?', (record['asset_id'],)).fetchone()
                    assert stored is not None, record['instance_id']
                    stored_asset = json.loads(stored[0])
                    assert stored_asset['hash'] == record['source_sha256'] and stored_asset['name'] == record['upload_name']
                    assert json.loads(stored[1]) == read(run / record['artifact'] / 'rows.json'), record['instance_id']
                    full_rows_verified += 1
            arm_bundles = [b for b in bundles if b['arm'] == arm]
            audits.append(dict(run=run.name, arm=arm, records=len(selected),
                succeeded=sum(r['status'] == 'succeeded' for r in selected),
                expected_rejections=sum(r['expected_rejection_observed'] for r in selected),
                partial=sum(r.get('parser_status') == 'partial' for r in selected),
                b_both_attempted=sum(r.get('mandatory_both_attempted') is True for r in selected),
                b_both_succeeded=sum(r.get('mandatory_both_succeeded') is True for r in selected),
                stored_assets=asset_count, full_rows_verified=full_rows_verified, review_counts=review_counts,
                bundle_missing=sum(b['missing'] for b in arm_bundles),
                bundle_duplicates=sum(b['duplicates'] for b in arm_bundles),
                bundle_source_mismatches=sum(b['source_mismatches'] for b in arm_bundles),
                bundle_filename_mismatches=sum(b.get('filename_mismatches', 0) for b in arm_bundles),
                session_api_failures=sum(not b.get('session_api_ok', True) for b in arm_bundles),
                stored_without_success_response=sum(b.get('stored_without_success_response', 0) for b in arm_bundles)))
        all_records.extend(records)
    assert len(all_records) == 930
    assert sum(r['expected_rejection_observed'] for r in all_records) == 6
    result = dict(audit_passed=True, uploads=len(all_records), originals=len(cases),
                  gold_references=sum('gold' in c for c in cases.values()),
                  manifest_sha256=sha(args.manifest), mixed_manifest_sha256=sha(args.mixed_manifest), runs=audits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
