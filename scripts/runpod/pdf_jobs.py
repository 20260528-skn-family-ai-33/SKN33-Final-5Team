"""Single GPU PDF slot with polling leases, cancellation and bounded result retention."""
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_PDF = 8 * 1024 * 1024
MAX_BUNDLE = 64 * 1024 * 1024
WORKER = Path(__file__).with_name('pod_pdf_worker.py')
DEFAULT_TIMEOUT = 290
MAX_TIMEOUT = 1500
LEASE_SECONDS = 90  # longer than the client's 60 s HTTP timeout


@dataclass
class Job:
    deadline: float
    last_poll: float
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    response: tuple | None = None
    expires: float = 0
    delivered: bool = False


class PdfJobs:
    def __init__(self, python, *, lease_seconds=LEASE_SECONDS):
        self.python = python
        self.lease_seconds = lease_seconds
        self.lock = threading.Lock()
        self.jobs = {}
        self.closed = False

    def submit(self, data, policy=None, timeout=DEFAULT_TIMEOUT):
        if not self.python:
            return 503, {'error': 'pdf_router_unconfigured'}
        if b'%PDF-' not in data[:1024]:
            return 400, {'error': 'invalid_pdf'}
        if not isinstance(timeout, int) or not 1 <= timeout <= 1800:
            return 400, {'error': 'invalid_pdf_timeout'}
        with self.lock:
            if self.closed:
                return 503, {'error': 'pdf_router_stopping'}
            now = time.monotonic()
            self.jobs = {k: v for k, v in self.jobs.items() if v.response is None or v.expires > now}
            if any(v.response is None for v in self.jobs.values()):
                return 429, {'error': 'pdf_router_busy'}
            if len(self.jobs) >= 8:
                # Preserve unread ZIPs. A cancelled/failed client may never GET
                # its terminal error, so that record must not block admission.
                evict = next((k for k, v in self.jobs.items()
                              if v.response is not None and (v.delivered or v.response[0] != 200)), None)
                if evict is None:
                    return 429, {'error': 'pdf_router_busy'}
                self.jobs.pop(evict)
            identifier = str(uuid.uuid4())
            job = Job(deadline=now + min(timeout, MAX_TIMEOUT), last_poll=now)
            self.jobs[identifier] = job
            job.thread = threading.Thread(target=self._run, args=(identifier, job, data, policy), daemon=True)
            job.thread.start()
        return 202, {'job_id': identifier}

    @staticmethod
    def _stop_process_group(process):
        # Engines launch child Python/GPU processes. Reap the whole session even
        # when the worker has already exited, before making this slot available.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _stop_reason(self, job):
        with self.lock:
            now = time.monotonic()
            if job.cancel.is_set():
                return 'pdf_job_cancelled'
            if now >= job.deadline:
                return 'pdf_job_timeout'
            if now - job.last_poll >= self.lease_seconds:
                return 'pdf_job_lease_expired'
        return None

    def _run(self, identifier, job, data, policy):
        process = None
        response = (422, {'error': 'pdf_gpu_processing_failed'})
        try:
            with tempfile.TemporaryDirectory(prefix='a4b-pdf-') as folder:
                work = Path(folder)
                (work / 'original.pdf').write_bytes(data)
                env = {**os.environ, 'A4B_PDF_DEVICE': 'cuda', 'A4B_PDF_PARSER_MODE': 'standard'}
                command = [self.python, str(WORKER), folder]
                if policy:
                    command.append(policy)
                try:
                    reason = self._stop_reason(job)
                    if not reason:
                        process = subprocess.Popen(command, env=env, start_new_session=True)
                        while True:
                            reason = self._stop_reason(job)
                            if reason:
                                break
                            try:
                                process.wait(timeout=.1)
                                break
                            except subprocess.TimeoutExpired:
                                continue
                    if reason:
                        response = (410, {'error': reason})
                    elif process.returncode:
                        raise subprocess.CalledProcessError(process.returncode, command)
                    else:
                        archive = work / 'result.zip'
                        if archive.stat().st_size > MAX_BUNDLE:
                            raise ValueError('PDF result size limit')
                        response = (200, archive.read_bytes())
                finally:
                    if process is not None:
                        self._stop_process_group(process)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print('PDF worker failed:', type(exc).__name__, flush=True)
        with self.lock:
            job.response = response
            job.expires = time.monotonic() + 300
        timer = threading.Timer(300, self._expire, args=(identifier,))
        timer.daemon = True
        timer.start()

    def _expire(self, identifier):
        with self.lock:
            self.jobs.pop(identifier, None)

    def result(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if job is None:
                return 404, {'error': 'pdf_job_not_found'}
            if job.response is not None:
                job.delivered = True
                return job.response
            # An already expired lease cannot be revived by a late poll.
            now = time.monotonic()
            if now - job.last_poll < self.lease_seconds:
                job.last_poll = now
            return 202, {'job_id': identifier}

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if job is None:
                return 404, {'error': 'pdf_job_not_found'}
            if job.response is not None:
                return 200, {'job_id': identifier, 'finished': True}
            job.cancel.set()
            return 202, {'job_id': identifier, 'cancelling': True}

    def close(self):
        with self.lock:
            self.closed = True
            threads = []
            for job in self.jobs.values():
                if job.response is None:
                    job.cancel.set()
                    threads.append(job.thread)
        for thread in threads:
            thread.join()
