"""Internal CPU parser using the existing document engine response contract."""
import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.runpod.gpu_parser_server import RequestError, make_handler

MAX_RESULT = 6 * 1024 * 1024


def infer(engine, files, folder):
    sources = {str(path): name for name, path in files}
    request = folder / 'request.json'
    result = folder / 'response.json'
    request.write_text(json.dumps({'engine': engine, 'sources': list(sources), 'raw': str(folder / 'raw.json')}))
    worker = Path(__file__).resolve().parents[1] / 'Backend/documents/analysis/engine_worker.py'
    completed = subprocess.run([sys.executable, str(worker), str(request), str(result)], timeout=225, check=False)
    if completed.returncode or not result.is_file() or result.stat().st_size > MAX_RESULT:
        raise RequestError(500, 'engine_error')
    response = json.loads(result.read_text())
    for output in response['outputs']:
        output['source'] = sources[output['source']]
    return response


def main():
    key = os.environ.get('A4B_CPU_PARSER_API_KEY', '')
    if len(key) < 24:
        sys.exit('A4B_CPU_PARSER_API_KEY must contain at least 24 characters')
    handler = make_handler(key, {}, 'cpu', '', max_body=88 * 1024**2, max_image=64 * 1024**2, infer=infer)
    # Health checks stay responsive during inference. Bound memory before reading uploads.
    slot = threading.Lock()

    class ParserHandler(handler):
        def do_POST(self):
            if not slot.acquire(blocking=False):
                return self.reply(503, {'error': 'cpu_parser_busy'})
            try:
                super().do_POST()
            finally:
                slot.release()

    class ParserServer(ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(15)
            return connection, address

    ParserServer(('0.0.0.0', 8100), ParserHandler).serve_forever()


if __name__ == '__main__':
    main()
