"""Run the real supervisor on Linux with disposable service/process fixtures."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'scripts/runpod'


def eventually(check, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for fixture state')


@unittest.skipUnless(sys.platform == 'linux', 'Linux Bash/process-group integration')
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='a4b-supervisor-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / 'workspace/a4b'
        self.run = self.root / 'run/a4b-final'
        self.auth = self.root / 'run/a4b-auth'
        self.envs = self.root / 'envs'
        self.bin = self.root / 'bin'
        self.app = self.base / 'app/scripts/runpod'
        for path in (self.run, self.auth, self.bin, self.app, self.base/'logs', self.base/'receipts'):
            path.mkdir(parents=True)
        for name in ('gateway-token', 'parser-api-key'):
            (self.auth/name).write_text('local-fixture-token-0123456789')
        for name in ('pdf-prepared', 'parser-prepared', 'runtimes-packed'):
            (self.base/'receipts'/name).touch()
        for runtime in ('parser', 'mineru'):
            path = self.envs/runtime
            (path/'bin').mkdir(parents=True)
            (path/'.a4b-runtime-complete').touch()
            (path/'bin/python').symlink_to(sys.executable)
        for name, body in {'mountpoint': 'exit 0', 'dpkg-query': 'echo "install ok installed"'}.items():
            path = self.bin/name
            path.write_text('#!/bin/sh\n'+body+'\n'); path.chmod(0o700)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        profile = (SCRIPTS/'final_model_profile.py').read_text().replace('/workspace/a4b', str(self.base))
        (self.app/'final_model_profile.py').write_text(profile)
        namespace = {}; exec(profile, namespace)
        self.models = {name: str(i+1)*64 for i, name in enumerate(namespace['SERVING_MODELS'])}
        (self.base/'receipts/models-registered.json').write_text(json.dumps(self.models))
        checker = (SCRIPTS/'check-gateway-ready.py').read_text().replace('127.0.0.1:11434', f'127.0.0.1:{self.port}')
        (self.app/'check-gateway-ready.py').write_text(checker)
        self.common = f'import os,time,json\nfrom pathlib import Path\nr=Path({str(self.root)!r})\n'
        idle = 'while True:time.sleep(.05)\n'
        self.executable(self.base/'ollama/bin/ollama', self.common+"(r/'ollama.pid').write_text(str(os.getpid()))\n"+idle)
        parser = self.app/'gpu_parser_server.py'
        (self.app/'pod_pdf_worker.py').touch()
        (self.app/'parser-paths.sh').write_text((SCRIPTS/'parser-paths.sh').read_text())
        legacy = self.base/'app/legacy-pdf/scripts/runpod/gpu_parser_server.py'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(self.common+"(r/'legacy-started').touch()\n")
        parser.write_text(self.common+"(r/'parser.pid').write_text(str(os.getpid()))\n"+idle)
        (self.app/'check-final-ready.py').write_text('pass\n')
        (self.app/'warm-final.py').write_text(self.common+"(r/'warm.pid').write_text(str(os.getpid()))\nwhile not (r/'warm-release').exists():time.sleep(.02)\nraise SystemExit(1 if (r/'warm-fail').exists() else 0)\n")
        (self.app/'runpod-ollama-proxy.py').write_text(self.common+f'''
from http.server import BaseHTTPRequestHandler,HTTPServer
(r/'gateway.pid').write_text(str(os.getpid()))
while not (r/'gateway-listen').exists():time.sleep(.02)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  authenticated=self.headers.get('Authorization')=='Bearer local-fixture-token-0123456789'
  (r/'authenticated-request').write_text(str(authenticated))
  status=200 if authenticated and (r/'gateway-release').exists() else 503 if authenticated else 401
  self.send_response(status);self.end_headers()
  self.wfile.write(json.dumps({{'models':[{{'name':k+':latest','digest':v}} for k,v in {self.models!r}.items()]}}).encode())
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',{self.port}),Handler)
(r/'gateway-listening').touch()
server.serve_forever()
''')
        script = (SCRIPTS/'start-final.sh').read_text()
        for old, new in (('/workspace/a4b', str(self.base)), ('/run/a4b-final', str(self.run)),
                         ('/run/a4b-auth', str(self.auth)), ('/opt/a4b-envs', str(self.envs))):
            script = script.replace(old, new)
        # Keep real startup/control-flow and processes; shorten retry backoff only.
        script = script.replace('sleep 2', 'sleep .05').replace('sleep 5', 'sleep .05')
        self.script = self.app/'start-final.sh'; self.script.write_text(script)
        self.processes = []
        self.addCleanup(self.stop_all)

    def executable(self, path, code):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!'+sys.executable+'\n'+code); path.chmod(0o700)

    def start(self, source_root=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('A4B_')}
        env.update(PATH=str(self.bin)+':'+os.environ['PATH'], A4B_BASE_DIR=str(self.base))
        if source_root is not None:
            env['A4B_PARSER_SOURCE_ROOT'] = str(source_root)
        process = subprocess.Popen(['bash', str(self.script)], env=env, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            # Also collect a foreground child left by a broken/older supervisor.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        for path in self.root.glob('*.pid'):
            try: os.killpg(int(path.read_text()), signal.SIGKILL)
            except ProcessLookupError: pass

    def test_missing_committed_parser_fails_without_using_legacy(self):
        (self.run/'ready').write_text('stale')
        (self.app/'gpu_parser_server.py').unlink()
        process = self.start()
        self.assertNotEqual(process.wait(timeout=3), 0)
        self.assertFalse((self.root/'legacy-started').exists())
        self.assertFalse((self.root/'ollama.pid').exists())
        self.assertFalse((self.run/'ready').exists())

    def test_explicit_release_is_used_again_after_parser_restart(self):
        release = self.root/'releases/commit-fixture'
        scripts = release/'scripts/runpod'
        scripts.mkdir(parents=True)
        (scripts/'pod_pdf_worker.py').touch()
        (scripts/'gpu_parser_server.py').write_text((self.app/'gpu_parser_server.py').read_text())
        (self.app/'gpu_parser_server.py').write_text(self.common+"(r/'wrong-source').touch()\n")
        process = self.start(source_root=release)
        eventually(lambda: (self.root/'warm.pid').exists())
        first = int((self.root/'parser.pid').read_text())
        os.kill(first, signal.SIGTERM)
        eventually(lambda: (self.root/'parser.pid').read_text() not in ('', str(first)))
        self.assertIsNone(process.poll())
        self.assertFalse((self.root/'wrong-source').exists())
        self.assertFalse((self.root/'legacy-started').exists())

    def test_stale_ready_removed_after_lock_before_receipts(self):
        (self.base/'receipts/pdf-prepared').unlink()
        (self.run/'ready').write_text('stale')
        process = self.start()
        eventually(lambda: (self.base/'logs/startup.log').exists())
        self.assertIsNone(process.poll())
        self.assertFalse((self.run/'ready').exists())

    def test_term_during_warmup_stops_all_children_promptly(self):
        process = self.start()
        eventually(lambda: all((self.root/(name+'.pid')).exists() for name in ('ollama', 'parser', 'warm')))
        pids = [int((self.root/(name+'.pid')).read_text()) for name in ('ollama', 'parser', 'warm')]
        started = time.monotonic(); process.terminate(); process.wait(timeout=2)
        self.assertLess(time.monotonic()-started, 2)
        for pid in pids:
            eventually(lambda pid=pid: not Path('/proc', str(pid)).exists())
        self.assertFalse((self.root/'gateway.pid').exists())
        self.assertFalse((self.run/'ready').exists())

    def assert_service_exit_during_warmup_restarts(self, service, exit_signal):
        process = self.start()
        eventually(lambda: all((self.root/(name+'.pid')).exists() for name in ('ollama', 'parser', 'warm')))
        original = {name: int((self.root/(name+'.pid')).read_text()) for name in ('ollama', 'parser', 'warm')}
        os.kill(original[service], exit_signal)
        # Warmup stays blocked indefinitely, so restart cannot depend on it finishing.
        eventually(lambda: int((self.root/'warm.pid').read_text()) != original['warm'], timeout=3)
        for pid in original.values():
            eventually(lambda pid=pid: not Path('/proc', str(pid)).exists())
        self.assertIsNone(process.poll())
        self.assertFalse((self.root/'legacy-started').exists())
        self.assertFalse((self.root/'warm-release').exists())
        self.assertFalse((self.root/'gateway.pid').exists())
        self.assertFalse((self.run/'ready').exists())
        # The replacement services can finish warming and publish authenticated readiness.
        for name in ('warm-release', 'gateway-listen', 'gateway-release'):
            (self.root/name).touch()
        eventually(lambda: (self.run/'ready').exists())
        self.assertEqual((self.root/'authenticated-request').read_text(), 'True')

    def test_parser_exit_during_warmup_restarts_all_services(self):
        self.assert_service_exit_during_warmup_restarts('parser', signal.SIGTERM)

    def test_ollama_crash_during_warmup_restarts_all_services(self):
        self.assert_service_exit_during_warmup_restarts('ollama', signal.SIGKILL)

    def test_ready_requires_authenticated_gateway_and_duplicate_start_preserves_it(self):
        (self.root/'warm-release').touch()
        process = self.start()
        eventually(lambda: (self.root/'gateway.pid').exists())
        time.sleep(.15)
        self.assertFalse((self.run/'ready').exists())
        (self.root/'gateway-listen').touch()
        eventually(lambda: (self.root/'authenticated-request').exists())
        self.assertEqual((self.root/'authenticated-request').read_text(), 'True')
        self.assertFalse((self.run/'ready').exists(), '503 must not publish ready')
        (self.root/'gateway-release').touch()
        eventually(lambda: (self.run/'ready').exists() and (self.run/'ready').read_bytes().endswith(b'\n'))
        before = (self.run/'ready').read_bytes()
        duplicate = self.start(); self.assertEqual(duplicate.wait(timeout=2), 0)
        self.assertEqual((self.run/'ready').read_bytes(), before)
        process.terminate(); process.wait(timeout=3)
        self.assertFalse((self.run/'ready').exists())

    def test_failed_warmup_never_opens_gateway(self):
        (self.root/'warm-fail').touch(); (self.root/'warm-release').touch()
        self.start()
        eventually(lambda: 'restarting services after failure' in (self.base/'logs/startup.log').read_text()
                   if (self.base/'logs/startup.log').exists() else False)
        self.assertFalse((self.root/'gateway.pid').exists())
        self.assertFalse((self.run/'ready').exists())


if __name__ == '__main__':
    unittest.main()
