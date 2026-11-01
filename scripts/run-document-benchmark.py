"""Exercise both arms through the real HTTP upload path on the RunPod."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


class Server:
    def __init__(self, arm, output):
        self.arm, self.output = arm, output
        self.directory = output / arm / 'service-data'
        self.directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(port), 'EASYFLOW_DATA_DIR': str(self.directory),
               'A4B_FOCUSED_DEMO': '1', 'A4B_DOCUMENT_TIMEOUT_MS': '300000',
               'A4B_DOCUMENT_BENCHMARK_ARM': 'docling-paddle-fixed-v1' if arm == 'B' else ''}
        self.log = (output / arm / 'server.log').open('ab')
        self.process = subprocess.Popen(['node', str(ROOT / 'scripts/run-configured.mjs')], cwd=ROOT,
                                        env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, start_new_session=True)
        for _ in range(120):
            if self.process.poll() is not None:
                raise RuntimeError(f'{arm} 서비스 시작 실패: server.log 확인')
            try:
                self.call('/api/health', timeout=1)
                return
            except Exception:
                time.sleep(.5)
        self.stop()
        raise RuntimeError(f'{arm} 서비스 시작 시간 초과')

    def call(self, path, data=None, timeout=330, binary=False):
        request = urllib.request.Request(self.base + path, method='GET' if data is None else 'POST',
                                        data=None if data is None else json.dumps(data).encode(),
                                        headers={} if data is None else {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'HTTP {error.code}: {error.read().decode(errors="replace")[:2000]}') from error
        return raw if binary else json.loads(raw)

    def session(self):
        connection = self.call('/api/agent-builder/connection')['connection']
        return self.call('/api/agent-builder/sessions', dict(credentialId=connection['credential']['id'], model=connection['model']))

    def stop(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.log.close()

    def rows(self, asset_id):
        with sqlite3.connect(f'file:{self.directory}/agent-builder.sqlite?mode=ro', uri=True) as db:
            value = db.execute('SELECT rows_json FROM builder_assets WHERE id=?', (asset_id,)).fetchone()
            return json.loads(value[0]) if value else None


def upload(server, session, case, corpus_root, bundle_id, ordinal, name=None):
    instance_id = f'{bundle_id}-{ordinal:03d}-{server.arm}'
    destination = server.output / server.arm / 'files' / instance_id
    destination.mkdir(parents=True)
    record = dict(instance_id=instance_id, corpus_id=case['id'], arm=server.arm, bundle_id=bundle_id,
                  ordinal=ordinal, source_sha256=case['sha256'], upload_name=name or case['name'],
                  suite=case['suite'], format=case['format'], expected_failure=case.get('expected_failure', False),
                  input_bytes=case['bytes'],
                  status='failed', artifact=str(destination.relative_to(server.output)))
    started = time.perf_counter()
    previous_ids = {a['id'] for a in session.get('resources', [])}
    source = corpus_root / case['input']
    work_before = set((server.directory / 'document-results' / case['sha256']).glob('*'))
    try:
        raw = source.read_bytes()
        assert digest(raw) == case['sha256'], '고정된 원본 파일이 변경됐습니다.'
        session = server.call('/api/agent-builder/upload', dict(id=session['id'], revision=session['revision'],
                              fileName=record['upload_name'], data=base64.b64encode(raw).decode()))
        record['upload_elapsed_seconds'] = time.perf_counter() - started
        new_assets = [a for a in session['resources'] if a['id'] not in previous_ids]
        record['new_asset_count'] = len(new_assets)
        record['missing_result'] = len(new_assets) == 0
        record['unexpected_duplicate_assets'] = max(0, len(new_assets) - 1)
        if len(new_assets) != 1:
            raise RuntimeError('업로드마다 구분된 결과 자산이 하나 생성되지 않았습니다.')
        asset = new_assets[0]
        save(destination / 'asset.json', asset)
        record['source_mismatch'] = asset['hash'] != case['sha256']
        record['filename_mismatch'] = asset['name'] != record['upload_name']
        assert asset['hash'] == case['sha256'], '결과가 다른 원본 해시와 연결됐습니다.'
        assert asset['name'] == record['upload_name'], '결과 파일 이름이 원본 입력과 다릅니다.'
        all_rows = server.rows(asset['id'])
        save(destination / 'rows.json', all_rows)
        semantic = dict(rows=all_rows, columns=asset['columns'], text=asset.get('text', ''),
                        tables=[dict(text=t.get('text', ''), html=t.get('html', '')) for t in asset.get('tables', [])])
        record['semantic_sha256'] = digest(json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        downloaded = server.call('/api/agent-builder/original?id=' + urllib.parse.quote(asset['id']), binary=True)
        assert digest(downloaded) == case['sha256'], '원본 다운로드 해시가 다릅니다.'
        record.update(status='succeeded', asset_id=asset['id'], row_count=asset['row_count'],
                      original_preserved=True, parser_status=asset.get('parser_status', 'succeeded'),
                      processing_status=asset.get('processing_status'),
                      analysis_artifact_directory=asset.get('parser_metadata', {}).get('analysis_artifact_directory'))
    except Exception as error:
        record['error'] = str(error)[:3000]
        record.setdefault('upload_elapsed_seconds', time.perf_counter() - started)
        try:
            session = server.call('/api/agent-builder/session?id=' + urllib.parse.quote(session['id']), timeout=30)
        except Exception as refresh_error:
            record['session_refresh_error'] = str(refresh_error)[:500]
    work_after = set((server.directory / 'document-results' / case['sha256']).glob('*'))
    created = sorted(work_after - work_before)
    if created:
        artifact = created[-1]
        record['analysis_artifact_directory'] = str(artifact.relative_to(server.directory))
        result_file = artifact / 'result.json'
        if result_file.is_file():
            parsed = json.loads(result_file.read_text())
            analysis = parsed.get('document_analysis', {})
            save(destination / 'analysis.json', analysis or parsed)
            record['routes'] = analysis.get('routes', [])
            record['review_reasons'] = analysis.get('review_reasons', [])
            if server.arm == 'B':
                record['mandatory_both_succeeded'] = all(any(r['processor'] == engine and r['status'] == 'succeeded' for r in record['routes'])
                                                          for engine in ['docling', 'paddleocr'])
        record['raw_attempt_count'] = len(list(artifact.glob('attempt-*/request.json')))
        record['attempted_processors'] = [json.loads(p.read_text())['engine'] for p in sorted(artifact.glob('attempt-*/request.json'))]
        if server.arm == 'B':
            record['mandatory_both_attempted'] = all(engine in record['attempted_processors'] for engine in ['docling', 'paddleocr'])
    record['expected_rejection_observed'] = record['expected_failure'] and record['status'] == 'failed'
    record['elapsed_seconds_with_evidence'] = time.perf_counter() - started
    save(destination / 'record.json', record)
    return session, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=['pilot', 'singles', 'bundles', 'all'], default='all')
    parser.add_argument('--arms', nargs='+', choices=['A', 'B'], default=['A', 'B'])
    parser.add_argument('--pilot-cases', nargs='+')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(args.manifest.read_text())
    corpus_root = args.manifest.resolve().parent
    cases = {c['id']: c for c in manifest['cases']}
    jobs = []
    if args.phase == 'pilot':
        pilot_ids = args.pilot_cases or ['OMNI-01'] + [f'PRESERVE-{fmt}-01' for fmt in ['CSV', 'XLSX', 'JSON', 'TXT', 'MD', 'PDF']]
        assert all(identifier in cases for identifier in pilot_ids), '알 수 없는 소규모 시험 ID입니다.'
        jobs = [dict(id='pilot-' + identifier, cases=[dict(corpus_id=identifier)]) for identifier in pilot_ids]
    else:
        if args.phase in {'singles', 'all'}:
            jobs += [dict(id='single-' + case['id'], cases=[dict(corpus_id=case['id'])]) for case in manifest['cases']]
        if args.phase in {'bundles', 'all'}:
            jobs += manifest['bundles']
    context = dict(started_at=time.time(), phase=args.phase, manifest_sha256=digest(args.manifest.read_bytes()),
                   concurrency=1, arm_order='alternating_AB_BA_per_bundle', requested_arms=args.arms, human_review=False,
                   requested_jobs=len(jobs), source_code_manifest_sha256=digest((ROOT / 'deployment-manifest.json').read_bytes()))
    save(output / 'execution-context.json', context)
    servers, records, bundles = {}, [], []
    complete = False
    try:
        for arm in args.arms:
            servers[arm] = Server(arm, output)
        for job_index, job in enumerate(jobs):
            for arm in (['A', 'B'] if job_index % 2 == 0 else ['B', 'A']):
                if arm not in servers:
                    continue
                server = servers[arm]
                session = server.session()
                bundle_records = []
                started = time.perf_counter()
                for index, occurrence in enumerate(job['cases'], 1):
                    case = cases[occurrence['corpus_id']]
                    session, result = upload(server, session, case, corpus_root, job['id'], index, occurrence.get('upload_name'))
                    records.append(result); bundle_records.append(result)
                    save(output / 'progress.json', records)
                    print(json.dumps({k: result.get(k) for k in ['instance_id', 'status', 'parser_status', 'mandatory_both_succeeded', 'upload_elapsed_seconds', 'error']}, ensure_ascii=False), flush=True)
                session_read_error = None
                try:
                    restored = server.call('/api/agent-builder/session?id=' + urllib.parse.quote(session['id']))
                except Exception as error:
                    # Observe storage even when a large session cannot be returned
                    # by HTTP. Never use DB revisions to repair subsequent uploads.
                    session_read_error = str(error)[:1500]
                    with sqlite3.connect(f'file:{server.directory}/agent-builder.sqlite?mode=ro', uri=True) as db:
                        saved = db.execute('SELECT json FROM builder_sessions WHERE id=?', (session['id'],)).fetchone()
                        ids = json.loads(saved[0]).get('file_ids', []) if saved else []
                        restored = dict(resources=[json.loads(row[0]) for asset_id in ids
                            if (row := db.execute('SELECT json FROM builder_assets WHERE id=?', (asset_id,)).fetchone())])
                successful = [r for r in bundle_records if r['status'] == 'succeeded']
                stored = {a['id']: a for a in restored['resources']}
                bundles.append(dict(id=job['id'], arm=arm, requested=len(bundle_records), succeeded=len(successful),
                                    missing=sum(r.get('missing_result', False) for r in bundle_records) + sum(r['asset_id'] not in stored for r in successful),
                                    source_mismatches=sum(r.get('source_mismatch', False) for r in bundle_records) + sum(stored.get(r['asset_id'], {}).get('hash') != r['source_sha256'] for r in successful),
                                    filename_mismatches=sum(r.get('filename_mismatch', False) for r in bundle_records),
                                    duplicates=sum(r.get('unexpected_duplicate_assets', 0) for r in bundle_records) + len([r['asset_id'] for r in successful]) - len({r['asset_id'] for r in successful}),
                                    stored_assets=len(stored), elapsed_seconds=time.perf_counter() - started))
                bundles[-1].update(session_api_ok=session_read_error is None, session_read_error=session_read_error,
                                   persistence_validation='sqlite_after_http_failure' if session_read_error else 'http',
                                   stored_without_success_response=len(set(stored) - {r['asset_id'] for r in successful}))
                save(output / 'bundles.json', bundles)
        complete = True
    finally:
        for server in servers.values():
            server.stop()
        save(output / 'summary.json', dict(**context, completed_at=time.time(), complete=complete,
                                          records=len(records), succeeded=sum(r['status'] == 'succeeded' for r in records),
                                          expected_rejections=sum(r['expected_rejection_observed'] for r in records), bundles=len(bundles)))
    print(json.dumps(dict(output=str(output), complete=complete, records=len(records)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
