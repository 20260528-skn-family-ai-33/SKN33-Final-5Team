"""Record RunPod identity and verify frozen source/input/gold hashes before runs."""
import collections
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
assert platform.system() == 'Linux', '실제 검증은 승인된 RunPod Linux 환경에서 실행하세요.'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


deployment = json.loads((ROOT / 'deployment-manifest.json').read_text())
for entry in deployment['files']:
    assert sha(ROOT / entry['path']) == entry['sha256'], '배포 코드 해시 불일치: ' + entry['path']
gpu_probe = json.loads((ROOT / 'output/runpod-setup/mineru-vulkan-health.json').read_text())
assert gpu_probe['explicit_backend'] == 'vulkan' and 'NVIDIA L40S' in gpu_probe['gpu'], 'VLM의 실제 GPU 검증이 필요합니다.'
corpus_root = ROOT / 'data/document-benchmark/v1'
manifest = json.loads((corpus_root / 'manifest.json').read_text())
for case in manifest['cases']:
    assert sha(corpus_root / case['input']) == case['sha256'], case['id']
    if case.get('gold'):
        assert sha(corpus_root / case['gold']) == case['gold_sha256'], case['id']
    if case['suite'] == 'omnidocbench':
        import pymupdf
        with pymupdf.open(corpus_root / case['input']) as doc:
            assert len(doc) == 1, 'OmniDocBench 입력 PDF는 페이지별 파일이어야 합니다.'
for bundle in manifest['bundles']:
    assert 1 <= len(bundle['cases']) <= 10
environment = {}
for name in ['document-analysis', 'agent', 'docling', 'mineru', 'paddle', 'bmt']:
    python = ROOT / f'Backend/.venv-{name}/bin/python'
    environment[name] = subprocess.check_output([str(python), '--version'], text=True).strip()
    frozen = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(python)], text=True)
    (ROOT / f'output/runpod-setup/{name}-freeze.txt').write_text(frozen)
limits = {}
for name in ['cpu.max', 'memory.max']:
    path = Path('/sys/fs/cgroup') / name
    limits[name] = path.read_text().strip() if path.exists() else None
result = dict(hostname=platform.node(), platform=platform.platform(), python=sys.version,
              gpu=subprocess.check_output(['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.total', '--format=csv'], text=True),
              node=subprocess.check_output(['node', '--version'], text=True).strip(), environments=environment, cgroup=limits,
              manifest_sha256=sha(corpus_root / 'manifest.json'), deployment_manifest_sha256=sha(ROOT / 'deployment-manifest.json'),
              source_files_verified=len(deployment['files']), inputs_verified=len(manifest['cases']),
              mineru_native_gpu_probe=gpu_probe,
              suites=dict(collections.Counter(case['suite'] for case in manifest['cases'])),
              corpus_input_bytes=sum(case['bytes'] for case in manifest['cases']),
              service_size_limit_rejections=[case['id'] for case in manifest['cases'] if case['bytes'] > 8 * 1024 * 1024])
target = ROOT / 'output/runpod-setup/preflight.json'
target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
