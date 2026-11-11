"""Evaluation-only destination and comparison policy; never changes app configuration."""
from importlib import metadata
import platform
import subprocess
from urllib.parse import urlsplit


def endpoint_policy(endpoint, allow_local_endpoint=None):
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/v1'):
        raise ValueError('Explicit model endpoint ending in /v1 is required')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    host = parsed.hostname
    origin = f'{parsed.scheme}://{"[" + host + "]" if ":" in host else host}:{port}'
    canonical = origin + '/v1'
    local = host in ('127.0.0.1', '::1')
    if local:
        if not allow_local_endpoint:
            raise ValueError('Local evaluation requires --allow-local-endpoint with the exact URL')
        allowed = urlsplit(allow_local_endpoint)
        if (allowed.scheme != parsed.scheme or allowed.hostname != host or
                (allowed.port or (443 if allowed.scheme == 'https' else 80)) != port or
                allowed.path.rstrip('/') != '/v1' or allowed.username is not None or
                allowed.password is not None or allowed.query or allowed.fragment):
            raise ValueError('Local endpoint differs from explicit opt-in')
    elif (allow_local_endpoint or parsed.scheme != 'https' or
          not host.endswith('.proxy.runpod.net')):
        raise ValueError('Only RunPod HTTPS or an explicitly selected loopback endpoint is permitted')
    return {'kind': 'local' if local else 'runpod', 'endpoint': canonical,
            'scheme': parsed.scheme, 'host': host, 'port': port}


def assert_model_url(url, policy):
    parsed = urlsplit(str(url))
    if (parsed.scheme != policy['scheme'] or parsed.hostname != policy['host'] or
            (parsed.port or (443 if parsed.scheme == 'https' else 80)) != policy['port'] or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or
            parsed.path not in ('/v1/models', '/v1/chat/completions', '/api/tags', '/api/show', '/api/chat', '/api/version')):
        raise OSError('Evaluation blocks non-model URL, port or API path')


def environment_identity(policy, provider):
    dependencies = {d.metadata['Name'].lower().replace('_', '-'): d.version
                    for d in metadata.distributions() if d.metadata.get('Name')}
    return {'version': 1, 'kind': policy['kind'], 'endpoint': policy['endpoint'],
            'provider': provider, 'python': platform.python_version(),
            'platform': platform.platform(), 'dependencies': dict(sorted(dependencies.items())),
            'hardware': hardware_identity(), 'server_version': None, 'models': None}


def hardware_identity():
    result = {'cpu': platform.processor(), 'gpu': None}
    try:
        gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
                                       '--format=csv,noheader,nounits'], text=True, timeout=5)
        result['gpu'] = [line.strip() for line in gpu.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        result['gpu_probe'] = 'unavailable'
    if platform.system() == 'Windows':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ('total_phys', 'avail_phys', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'extended')]]
        memory = MemoryStatus(); memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result['physical_memory_bytes'] = memory.total_phys
    return result


def assert_same_environment(first, second):
    """Legacy artifacts can agree with each other, but never with new identities."""
    if first != second:
        raise ValueError('Evaluation environment differs; do not mix endpoint, model weights or dependencies')


def require_complete_baseline(baseline, corpus):
    if not baseline or baseline.get('gold_sha256') != corpus['gold_sha256']:
        raise ValueError('After requires baseline with exactly the same frozen gold')
    expected = {c['id'] for c in corpus['cases']}
    records = baseline.get('cases', [])
    ids = [r['id'] for r in records]
    if (baseline.get('phase') != 'baseline' or baseline.get('status') != 'completed' or
            set(ids) != expected or len(ids) != len(expected) or
            any(r.get('status') not in ('completed', 'failed') for r in records) or
            not any(r.get('live_model_calls', 0) > 0 for r in records)):
        raise ValueError('After requires a complete baseline covering every frozen case exactly once')


def canonical_model(model, provider):
    if provider == 'ollama' and model and ':' not in model.rsplit('/', 1)[-1]:
        return model + ':latest'
    return model
