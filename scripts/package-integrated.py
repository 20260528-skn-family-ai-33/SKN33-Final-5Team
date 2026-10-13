"""Package the integrated sources, builds and docs, excluding local runtime data."""
from pathlib import Path
import hashlib,json,zipfile

root=Path(__file__).resolve().parents[1]
name='A4B_node_connections_2026-10-13'
target=root.parent/'output'/(name+'.zip')
target.parent.mkdir(exist_ok=True)
excluded={'node_modules','.venv-agent','.venv','.tools','data','.git','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'}
def included(path):
    rel=path.relative_to(root)
    return path.is_file() and not path.is_symlink() and not set(rel.parts)&excluded and path.name not in ['.DS_Store','DELIVERY_MANIFEST.json'] and not path.name.startswith('.env') and not path.name.endswith(('.pyc','.tsbuildinfo','.sqlite','.sqlite-wal','.sqlite-shm','.db','.log'))
files=sorted((p for p in root.rglob('*') if included(p)),key=lambda p:str(p.relative_to(root)))
manifest={'release':name,'files':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(root/'DELIVERY_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for p in files+[root/'DELIVERY_MANIFEST.json']:archive.write(p,name+'/'+p.relative_to(root).as_posix())
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    for relative,digest in manifest['files'].items():assert hashlib.sha256(archive.read(name+'/'+relative)).hexdigest()==digest
    assert not any(set(Path(entry).parts)&excluded for entry in archive.namelist())
print(json.dumps({'archive':str(target),'files':len(files)+1,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()},ensure_ascii=False))
