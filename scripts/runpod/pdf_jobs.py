"""Bounded asynchronous PDF jobs. Model startup can exceed the RunPod proxy timeout."""
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

MAX_PDF = 8 * 1024 * 1024
MAX_BUNDLE = 64 * 1024 * 1024
WORKER = Path(__file__).with_name('pod_pdf_worker.py')


class PdfJobs:
    def __init__(self, python):
        self.python = python
        self.lock = threading.Lock()
        self.jobs = {}
        self.delivered = set()

    def submit(self, data, policy=None):
        if not self.python:
            return 503, {'error': 'pdf_router_unconfigured'}
        if b'%PDF-' not in data[:1024]:
            return 400, {'error': 'invalid_pdf'}
        with self.lock:
            now = time.monotonic()
            self.jobs = {k: v for k, v in self.jobs.items() if v is None or v[0] > now}
            if any(v is None for v in self.jobs.values()):
                return 429, {'error': 'pdf_router_busy'}
            if len(self.jobs) >= 8:
                evict = next((k for k in self.jobs if k in self.delivered), None)
                if evict is None:
                    return 429, {'error': 'pdf_router_busy'}
                self.jobs.pop(evict)
                self.delivered.discard(evict)
            job = str(uuid.uuid4())
            self.jobs[job] = None
            threading.Thread(target=self._run, args=(job, data, policy), daemon=True).start()
        return 202, {'job_id': job}

    def _run(self, job, data, policy):
        try:
            with tempfile.TemporaryDirectory(prefix='a4b-pdf-') as folder:
                work = Path(folder)
                (work / 'original.pdf').write_bytes(data)
                env = {**os.environ, 'A4B_PDF_DEVICE': 'cuda', 'A4B_PDF_PARSER_MODE': 'standard'}
                command = [self.python, str(WORKER), folder]
                if policy:
                    command.append(policy)
                subprocess.run(command, env=env, check=True, timeout=1500)
                archive = work / 'result.zip'
                if archive.stat().st_size > MAX_BUNDLE:
                    raise ValueError('PDF result size limit')
                response = (200, archive.read_bytes())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print('PDF worker failed:', type(exc).__name__, flush=True)
            response = (422, {'error': 'pdf_gpu_processing_failed'})
        with self.lock:
            self.jobs[job] = (time.monotonic() + 300, response)
        timer = threading.Timer(300, self._expire, args=(job,))
        timer.daemon = True
        timer.start()

    def _expire(self, job):
        with self.lock:
            self.jobs.pop(job, None)
            self.delivered.discard(job)

    def result(self, job):
        with self.lock:
            if job not in self.jobs:
                return 404, {'error': 'pdf_job_not_found'}
            result = self.jobs[job]
            if result:
                self.delivered.add(job)
            return result[1] if result else (202, {'job_id': job})
