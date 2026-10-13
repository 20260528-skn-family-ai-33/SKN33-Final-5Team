"""Import synthetic pilot data separately from operational workflows."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from agent_builder.storage import Storage
from agent_builder.preprocessing import DatasetStore,ROOT
p=argparse.ArgumentParser();p.add_argument('--directory',default='data');p.add_argument('--source',default=str(ROOT));a=p.parse_args()
s=Storage(a.directory)
try:print(json.dumps(DatasetStore(s).import_directory(a.source),ensure_ascii=False,indent=2))
finally:s.close()
