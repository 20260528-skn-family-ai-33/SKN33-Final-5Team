#!/usr/bin/env python3
"""Apply or restore one A4B release under the existing EC2 deployment lock."""
import base64
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path('/var/lib/a4b-deploy')
FRONTEND = ROOT / 'frontend'
CURRENT = ROOT / 'components.json'
BASELINE = ROOT / 'components-before-test.json'
PREVIOUS = ROOT / 'components-previous.json'
PENDING = ROOT / 'components-pending.json'
NAMES = {'backend': 'a4b-jhd-aws', 'ocr': 'a4b-ocr-cpu'}
COMPONENTS = {'frontend', *NAMES}
IMAGE = re.compile(r'(?P<repository>[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*):(?P<component>frontend|backend|ocr)-(?P<commit>[0-9a-f]{40})')


def call(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n')
    temporary.replace(path)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def probe(url, host=None):
    try:
        request = urllib.request.Request(url, headers={'Host': host} if host else {})
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status == 200
    except OSError:
        return False


def verify(public_url):
    for _ in range(30):
        if (probe('http://127.0.0.1:8101/health')
                and probe('http://127.0.0.1:3041/', public_url.removeprefix('https://'))
                and probe(public_url + '/api/health')):
            return
        time.sleep(2)
    raise RuntimeError('OCR, local homepage or public API health check failed')


def frontend_link(release):
    link = FRONTEND / 'current'
    if release:
        temporary = FRONTEND / '.current-new'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(release)
        temporary.replace(link)
    else:
        link.unlink(missing_ok=True)


def inspect(identifier):
    return json.loads(output('docker', 'inspect', identifier))[0]


def activate(component, identifier):
    name = NAMES[component]
    known = output('docker', 'ps', '-aq', '--filter', f'name=^/{name}$')
    if known and known != identifier[:12] and known != identifier:
        if inspect(known)['State']['Running']:
            call('docker', 'stop', known)
        call('docker', 'rename', known, f'{name}-previous-{known[:12]}')
    if identifier:
        candidate = inspect(identifier)
        if candidate['Name'] != '/' + name:
            call('docker', 'rename', identifier, name)
        if not candidate['State']['Running']:
            call('docker', 'start', identifier)


def current_release():
    current = load(CURRENT)
    if current:
        return current
    active = inspect(NAMES['backend'])
    return {'backend': {'id': active['Id'], 'image': active['Config']['Image']}}


def prepare_frontend(image, release):
    directory = FRONTEND / release
    if directory.is_dir():
        return
    with tempfile.TemporaryDirectory(dir=FRONTEND) as staging:
        container = output('docker', 'create', image, '/unused')
        try:
            call('docker', 'cp', f'{container}:/dist/.', staging)
            root = Path(staging)
            if not (root / 'index.html').is_file() or any(
                p.is_symlink() or not (p.is_file() or p.is_dir()) for p in root.rglob('*')
            ):
                raise ValueError('Invalid frontend build artifact')
            root.chmod(0o755)
            root.rename(directory)
        finally:
            call('docker', 'rm', container)


def prepare_containers(images, compose_file, old, staging):
    selected = set(images) & NAMES.keys()
    if not selected:
        return {}
    app = inspect(old['backend']['id'])
    bindings = app['HostConfig']['Binds']
    data = next(m.split(':', 1)[0] for m in bindings if ':/app/data' in m)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', data):
        raise ValueError('Expected a named production data volume')
    ca = next(m for m in bindings if ':/run/a4b/global-bundle.pem:' in m)
    app_env = staging / 'app.env'
    env = app['Config']['Env']
    if any('\n' in line or '\r' in line or '=' not in line for line in env):
        raise ValueError('Existing container environment is not a raw env file')
    app_env.write_text('\n'.join(env) + '\n')
    key_path = ROOT / 'cpu-ocr.key'
    if not key_path.exists():
        key_path.write_text(secrets.token_hex(32))
    settings = {**os.environ, 'A4B_APP_IMAGE': images.get('backend', old['backend']['image']),
                'A4B_OCR_IMAGE': images.get('ocr', old.get('ocr', {}).get('image', 'unused:latest')),
                'A4B_APP_ENV_FILE': str(app_env), 'A4B_DATA_VOLUME': data, 'A4B_CA_MOUNT': ca,
                'A4B_FRONTEND_ROOT': str(FRONTEND), 'A4B_CPU_PARSER_API_KEY': key_path.read_text()}
    project = 'a4b-' + secrets.token_hex(6)
    compose = ('docker', 'compose', '-f', str(compose_file), '-p', project)
    services = ['app' if name == 'backend' else name for name in sorted(selected)]
    try:
        call(*compose, 'create', '--no-build', '--pull', 'never', *services, env=settings)
        return {name: output(*compose, 'ps', '-a', '-q', 'app' if name == 'backend' else name, env=settings) for name in selected}
    except Exception:
        subprocess.run([*compose, 'down'], env=settings, check=False)
        raise


def recover(pending):
    frontend_link(pending.get('frontend', {}).get('release'))
    for name in ('backend', 'ocr'):
        activate(name, pending.get(name, {}).get('id', ''))
    PENDING.unlink()


def deploy(request, compose_file, public_url):
    transaction = load(PENDING)
    if transaction:
        if load(CURRENT) != transaction['new']:
            recover(transaction['old'])
        else:
            PENDING.unlink()
    old = current_release()
    if request['operation'] == 'restore':
        new = load(BASELINE)
        if not new:
            raise ValueError('No saved pre-test release')
    else:
        images = request['images']
        if not images.keys() <= COMPONENTS:
            raise ValueError('Unknown deployment component')
        matches = {name: IMAGE.fullmatch(image) for name, image in images.items()}
        if any(not match or match['component'] != name for name, match in matches.items()):
            raise ValueError('Invalid component image reference')
        if not CURRENT.exists() and (images.keys() != COMPONENTS or request['mode'] != 'production'):
            raise ValueError('Initialize split deployment from main with all components')
        if not images:
            print('The selected commit has no deployable changes.')
            return
        repository = request['repository']
        if any(match['repository'] != repository for match in matches.values()):
            raise ValueError('Image is outside the deployment repository')
        new = dict(old)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            staging = Path(folder)
            auth = staging / 'docker'
            auth.mkdir()
            env = {**os.environ, 'DOCKER_CONFIG': str(auth)}
            registry = repository.split('/')[0]
            password = subprocess.check_output(['aws', 'ecr', 'get-login-password', '--region', 'ap-northeast-2'])
            call('docker', 'login', '--username', 'AWS', '--password-stdin', registry, input=password, env=env)
            for image in images.values():
                call('docker', 'pull', image, env=env)
            for name, image in images.items():
                new[name] = {'commit': matches[name]['commit'], 'image': image}
            if 'frontend' in images:
                release = images['frontend'].split(':')[-1]
                prepare_frontend(images['frontend'], release)
                new['frontend']['release'] = release
            for name, identifier in prepare_containers(images, compose_file, old, staging).items():
                new[name]['id'] = identifier
        if request['mode'] == 'test' and not BASELINE.exists():
            save(BASELINE, old)

    changed = [name for name in ('ocr', 'backend') if old.get(name) != new.get(name)]
    save(PENDING, {'old': old, 'new': new})
    try:
        for name in changed:
            activate(name, new.get(name, {}).get('id', ''))
        frontend_link(new.get('frontend', {}).get('release'))
        verify(public_url)
    except BaseException:
        recover(old)
        if request['operation'] == 'deploy':
            for name in changed:
                if identifier := new.get(name, {}).get('id'):
                    subprocess.run(['docker', 'rm', identifier], check=False)
        raise

    former = load(PREVIOUS)
    save(CURRENT, new)
    save(PREVIOUS, old)
    PENDING.unlink()
    retired = load(BASELINE)
    if request['operation'] == 'restore' or request['mode'] == 'production':
        BASELINE.unlink(missing_ok=True)
    kept = [new, old, load(BASELINE)]
    for name, item in (*former.items(), *retired.items()):
        if any(item.get('id', item.get('release')) == r.get(name, {}).get('id', r.get(name, {}).get('release')) for r in kept):
            continue
        if item.get('id'):
            subprocess.run(['docker', 'rm', item['id']], check=False)
        if item.get('release'):
            shutil.rmtree(FRONTEND / item['release'], ignore_errors=True)
        if IMAGE.fullmatch(item.get('image', '')) and not any(item['image'] == r.get(name, {}).get('image') for r in kept):
            subprocess.run(['docker', 'image', 'rm', item['image']], check=False)
    print('Component deployment and health checks passed.')


def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    FRONTEND.mkdir(mode=0o755, exist_ok=True)
    FRONTEND.chmod(0o755)
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    public_url = sys.argv[2]
    if not re.fullmatch(r'https://[a-z0-9.-]+', public_url):
        raise ValueError('Invalid public URL')
    with open('/run/a4b-ec2-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(dir=ROOT) as staging:
            compose_file = Path(staging) / 'compose.yml'
            compose_file.write_bytes(base64.b64decode(sys.argv[3], validate=True))
            deploy(request, compose_file, public_url)


if __name__ == '__main__':
    main()
