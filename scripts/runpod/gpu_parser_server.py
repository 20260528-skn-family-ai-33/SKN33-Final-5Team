"""GPU layout/OCR server for the A4B document parser, run on the RunPod pod beside Ollama.

The app otherwise starts a CPU Paddle process per upload: about 21 s of model loading plus 12-16 s of
inference for one receipt on a 4-core PC. Here PP-DocLayout_plus-L and the Korean PP-OCRv5 models are
loaded once and stay on the GPU. Responses follow the engine_worker.paddle() contract, so the app's
pipeline is unchanged; images arrive base64-encoded and are written to a temporary folder only.

Environment: A4B_PARSER_API_KEY (required, 24+ chars), A4B_PARSER_PORT (8100),
A4B_PADDLE_DEVICE (gpu:0), A4B_PADDLE_MODEL_DIR (optional local model folder; otherwise PaddleX
downloads into PADDLE_PDX_CACHE_HOME).
"""
import base64
import binascii
import hmac
import importlib.metadata
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINES = ('layout', 'paddleocr')
MAX_BODY = 48 * 1024 * 1024
MAX_SOURCES = 32
MAX_IMAGE = 12 * 1024 * 1024
LAYOUT, DET, REC = 'PP-DocLayout_plus-L', 'PP-OCRv5_mobile_det', 'korean_PP-OCRv5_mobile_rec'


class RequestError(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def plain(value):
    if hasattr(value, 'tolist'):
        return value.tolist()
    if hasattr(value, 'item'):
        return value.item()
    raise TypeError(type(value).__name__)


def load_models(device):
    """Both engines once; model_dir only when a prepared local folder is configured."""
    from paddleocr import LayoutDetection, PaddleOCR
    root = os.environ.get('A4B_PADDLE_MODEL_DIR')
    folder = lambda name: {'model_dir': str(Path(root) / name)} if root else {}
    layout = LayoutDetection(model_name=LAYOUT, device=device, **folder(LAYOUT))
    ocr = PaddleOCR(text_detection_model_name=DET, text_recognition_model_name=REC,
                    **({'text_detection_model_dir': str(Path(root) / DET), 'text_recognition_model_dir': str(Path(root) / REC)} if root else {}),
                    use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device=device)
    return {'layout': (layout, LAYOUT), 'paddleocr': (ocr, DET + '+' + REC)}


def run(engine, payload, models, device, version, lock):
    """Validate one request and return the engine_worker.paddle() response for it."""
    if engine not in ENGINES:
        raise RequestError(404, 'unknown_engine')
    sources = payload.get('sources') if isinstance(payload, dict) else None
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise RequestError(400, 'invalid_sources')
    model, name = models[engine]
    outputs = []
    with tempfile.TemporaryDirectory(prefix='a4b-parser-') as folder:
        files = []
        for index, source in enumerate(sources):
            if not isinstance(source, dict) or not isinstance(source.get('name'), str) or not isinstance(source.get('data'), str):
                raise RequestError(400, 'invalid_sources')
            try:
                data = base64.b64decode(source['data'], validate=True)
            except (binascii.Error, ValueError):
                raise RequestError(400, 'invalid_image') from None
            if not data or len(data) > MAX_IMAGE:
                raise RequestError(413, 'image_too_large')
            # The client's path is only echoed back; it never names a file here.
            path = Path(folder) / f'{index}.png'
            path.write_bytes(data)
            files.append((source['name'], path))
        # One GPU, one predictor at a time; concurrent uploads queue here instead of sharing state.
        with lock:
            for name_given, path in files:
                for predicted in model.predict(str(path)):
                    raw = predicted.json
                    outputs.append({'source': name_given, 'result': raw.get('res', raw)})
    return {'engine': engine, 'version': version, 'model': {'name': name, 'device': device, 'remote': True}, 'outputs': outputs}


def make_handler(key, models, device, version):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'A4BParser/1'

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False, default=plain, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, {'ok': True, 'device': device, 'engines': list(ENGINES)})
            self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            given = self.headers.get('Authorization', '')
            if not hmac.compare_digest(given.encode(), ('Bearer ' + key).encode()):
                return self.reply(401, {'error': 'unauthorized'})
            if not self.path.startswith('/v1/'):
                return self.reply(404, {'error': 'not_found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {'error': 'request_too_large'})
            try:
                payload = json.loads(self.rfile.read(length))
                self.reply(200, run(self.path[4:], payload, models, device, version, lock))
            except RequestError as error:
                self.reply(error.status, {'error': error.code})
            except json.JSONDecodeError:
                self.reply(400, {'error': 'invalid_json'})
            except Exception as error:  # model failure; details stay in this pod's log
                print('parser error:', type(error).__name__, error, file=sys.stderr)
                self.reply(500, {'error': 'engine_error'})

        def log_message(self, format, *args):
            sys.stderr.write('%s %s\n' % (self.address_string(), format % args))

    return Handler


def main():
    key = os.environ.get('A4B_PARSER_API_KEY', '')
    if len(key) < 24:
        sys.exit('A4B_PARSER_API_KEY(24자 이상)를 설정하세요. 키 없이 공개 주소로 열지 않습니다.')
    os.environ.setdefault('FLAGS_allocator_strategy', 'auto_growth')  # leave the rest of VRAM to Ollama
    device = os.environ.get('A4B_PADDLE_DEVICE', 'gpu:0')
    models = load_models(device)
    version = importlib.metadata.version('paddleocr')
    port = int(os.environ.get('A4B_PARSER_PORT', '8100'))
    server = ThreadingHTTPServer(('0.0.0.0', port), make_handler(key, models, device, version))
    print(f'A4B GPU parser on :{port} ({device}, paddleocr {version})', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
