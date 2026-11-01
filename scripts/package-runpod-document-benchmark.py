"""Build a source-only deployment archive; never copy local envs or credentials."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def permitted(name):
    path = Path(name)
    return (not path.is_absolute() and '..' not in path.parts
            and path.parts[0] in {'Backend', 'Frontend', 'scripts', 'docs'}
            and not any(p in {'.git', '.ssh', '.aws', 'node_modules', '__pycache__'} or p.startswith('.venv') for p in path.parts)
            and not path.name.startswith('.env')
            and path.suffix.lower() not in {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db', '.log'}
            and path.name not in {'credentials.json', 'service-account.json'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include', action='append', default=[])
    args = parser.parse_args()
    tracked = subprocess.check_output(['git', 'ls-files', '-z', '--', 'Backend', 'Frontend', 'scripts'], cwd=ROOT).decode().split('\0')
    names = sorted(set(filter(None, tracked + args.include + [str(Path(__file__).relative_to(ROOT))])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'files': []}
    with tarfile.open(args.output, 'w:gz') as archive:
        for name in names:
            path = ROOT / name
            if not permitted(name):
                if name in args.include:
                    raise ValueError('금지된 파일 경로: ' + name)
                continue
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
                raise ValueError('일반 프로젝트 파일만 묶을 수 있습니다: ' + name)
            raw = path.read_bytes()
            manifest['files'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
            archive.add(path, arcname='finalproject/' + name, recursive=False)
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
        member = tarfile.TarInfo('finalproject/deployment-manifest.json')
        member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
    args.output.with_suffix(args.output.suffix + '.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'archive': str(args.output), 'files': len(manifest['files']), 'bytes': args.output.stat().st_size,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
