"""Import synthetic pilot data separately from operational workflows."""
import argparse,json,os,sys
from pathlib import Path
PROJECT_ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_builder.storage import Storage
from agent_builder.preprocessing import DatasetStore,ROOT

def project_path(value):
    path=Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT/path).resolve()

p=argparse.ArgumentParser();p.add_argument('--directory',type=project_path,default=os.environ.get('EASYFLOW_DATA_DIR') or str(PROJECT_ROOT/'data'));p.add_argument('--source',type=project_path,default=str(ROOT));a=p.parse_args()
s=Storage(a.directory)
try:print(json.dumps(DatasetStore(s).import_directory(a.source),ensure_ascii=False,indent=2))
finally:s.close()
