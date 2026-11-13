"""Gateway with configurable Bearer authentication: public 11434 -> loopback Ollama 11435."""
import hmac
import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from serving_request_policy import ensure_shift_support, pin_serving_request

TOKEN = os.environ['A4B_GATEWAY_TOKEN']

class Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if os.environ.get('A4B_API_AUTH_REQUIRED', '1') != '0' and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
            self.send_error(401, 'Bearer authentication required')
            return
        if self.path.split('?')[0] not in {'/api/tags', '/api/chat', '/api/version', '/v1/models', '/v1/chat/completions'}:
            self.send_error(404)
            return
        upstream = http.client.HTTPConnection('127.0.0.1', 11435, timeout=600)
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.command == 'POST':
                try:
                    body = pin_serving_request(self.path, body)
                except (ValueError, UnicodeError) as error:
                    self.send_error(400, str(error))
                    return
            upstream.request(self.command, self.path, body=body, headers={'Content-Type': 'application/json'})
            response = upstream.getresponse()
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            upstream.close()

if __name__ == '__main__':
    ensure_shift_support()
    ThreadingHTTPServer(('0.0.0.0', 11434), Gateway).serve_forever()
