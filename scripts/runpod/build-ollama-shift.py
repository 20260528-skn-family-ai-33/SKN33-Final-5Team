"""Build the pinned Ollama frontend with the native shift option exposed on /v1.

GPU runtimes and model files are reused unchanged. This only prepares a binary;
deployment/restart is a separate operation. Build logs contain no user requests.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request

VERSION = '0.40.13'
root = Path('/tmp/a4b-ollama-shift-build')
root.mkdir(parents=True, exist_ok=True)
out = Path('/workspace/a4b/receipts/reload-fix-20261111')
out.mkdir(parents=True, exist_ok=True)


def download(url, path):
    with urllib.request.urlopen(url, timeout=120) as response, path.open('wb') as file:
        while chunk := response.read(1024 * 1024):
            file.write(chunk)
    return hashlib.sha256(path.read_bytes()).hexdigest()


archive = root / 'source.tar.gz'
source_sha = download(f'https://codeload.github.com/ollama/ollama/tar.gz/refs/tags/v{VERSION}', archive)
assert source_sha == '68e04a5fcd5dc0f782eb8b66b1a6f6d00560b9bc0bd7d79cd353ee92f7e43339', 'Upstream archive changed'
with tarfile.open(archive) as tar:
    tar.extractall(root, filter='data')
source = root / f'ollama-{VERSION}'
go_version = re.search(r'^go (\S+)', (source / 'go.mod').read_text(), re.M)[1]
with urllib.request.urlopen('https://go.dev/dl/?mode=json&include=all', timeout=60) as response:
    releases = json.load(response)
release = next(r for r in releases if r['version'] == 'go' + go_version)
package = next(f for f in release['files'] if f['os'] == 'linux' and f['arch'] == 'amd64' and f['kind'] == 'archive')
go_archive = root / 'go.tar.gz'
assert download('https://go.dev/dl/' + package['filename'], go_archive) == package['sha256']
with tarfile.open(go_archive) as tar:
    tar.extractall(root, filter='data')
go = str(root / 'go/bin/go')
original_build = subprocess.check_output([go, 'version', '-m', '/workspace/a4b/ollama/bin/ollama'], text=True)
(out / 'original-build-info.txt').write_text(original_build)

path = source / 'openai/openai.go'
text = path.read_text()
# Add an optional field without changing existing omitted/default behavior.
anchor = 'type ChatCompletionRequest struct {\n'
assert text.count(anchor) == 1
text = text.replace(anchor, anchor + '\tShift *bool `json:"shift,omitempty"`\n\tTruncate *bool `json:"truncate,omitempty"`\n', 1)
anchor = 'return &api.ChatRequest{\n'
assert text.count(anchor) == 1
text = text.replace(anchor, anchor + '\t\tShift: r.Shift,\n\t\tTruncate: r.Truncate,\n', 1)
path.write_text(text)
(source / 'openai/a4b_shift_test.go').write_text('''package openai
import ("encoding/json"; "testing")
func TestA4BShiftPassThrough(t *testing.T) {
 for _, raw := range []string{`{"model":"test","shift":false,"truncate":false}`, `{"model":"test","shift":true,"truncate":true}`, `{"model":"test"}`} {
  var request ChatCompletionRequest
  if err := json.Unmarshal([]byte(raw), &request); err != nil { t.Fatal(err) }
  native, err := FromChatRequest(request)
  if err != nil { t.Fatal(err) }
  if request.Shift == nil { if native.Shift != nil { t.Fatal("default changed") } } else if native.Shift == nil || *native.Shift != *request.Shift { t.Fatal("shift lost") }
  if request.Truncate == nil { if native.Truncate != nil { t.Fatal("truncate default changed") } } else if native.Truncate == nil || *native.Truncate != *request.Truncate { t.Fatal("truncate lost") }
 }
}
''')
env = {**os.environ, 'PATH': str(root / 'go/bin') + ':' + os.environ['PATH'],
       'GOTOOLCHAIN': 'local', 'GOMAXPROCS': '4', 'GOCACHE': str(root / 'cache'),
       'GOPATH': str(root / 'gopath'), 'CGO_ENABLED': '1'}
subprocess.run([str(root / 'go/bin/gofmt'), '-w', str(path), str(source / 'openai/a4b_shift_test.go')], check=True)
print('SOURCE_READY', source_sha, flush=True)
subprocess.run([go, 'test', '-p', '4', './openai'], cwd=source, env=env, check=True)
print('OPENAI_TESTS_PASSED', flush=True)
binary = out / 'ollama-shift'
subprocess.run([go, 'build', '-p', '4', '-trimpath', '-buildmode=pie',
                '-ldflags=-w -s -X github.com/ollama/ollama/version.Version=0.40.13-a4b-shift1',
                '-o', str(binary), '.'], cwd=source, env=env, check=True)
receipt = {'upstream_version': VERSION, 'source_archive_sha256': source_sha,
           'go_version': go_version, 'go_archive_sha256': package['sha256'],
           'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
           'patch': 'Pass optional OpenAI chat shift and truncate fields through to native ChatRequest',
           'tests': 'go test ./openai passed', 'deployed': False}
(out / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
print('BUILD_COMPLETE', json.dumps(receipt), flush=True)
