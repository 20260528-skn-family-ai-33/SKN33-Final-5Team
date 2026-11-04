"""Synthetic localhost app check; no external credentials, AWS or paid API calls."""
import argparse
import base64
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:3041');p.add_argument('--output',default='output/local-http-smoke.json');p.add_argument('--timeout',type=float,default=660);args=p.parse_args()
if not 0<args.timeout<=1800:p.error('--timeout must be between 0 and 1800 seconds')
def call(path,data=None):
    body=None if data is None else json.dumps(data,ensure_ascii=False).encode()
    with urlopen(Request(args.url+path,data=body,headers={'Content-Type':'application/json'}),timeout=args.timeout) as r:
        return json.load(r)
report={'scope':'local synthetic app smoke; model recorded in connection; not an independent benchmark','started_at':time.time()}
try:
    report['health']=call('/api/health')
    connection=call('/api/agent-builder/connection')['connection'];report['connection']=connection
    s=call('/api/agent-builder/sessions',dict(model=connection['model'],credentialId=connection['credential']['id']))
    csv='code,stock\nA,20\nB,3\nC,1\n'
    s=call('/api/agent-builder/upload',dict(id=s['id'],revision=s['revision'],fileName='synthetic-stock.csv',data=base64.b64encode(csv.encode()).decode()))
    prompt='stock이 3 이상인 행만 남기고 stock 숫자 오름차순으로 정렬해서 CSV로 저장해줘. 빈 값은 제외하고 중복 행은 유지해줘.'
    started=time.perf_counter()
    s=call('/api/agent-builder/message',dict(id=s['id'],revision=s['revision'],content=prompt))
    report.update(design_seconds=time.perf_counter()-started,session=s)
    if s['status']=='ready':
        s=call('/api/agent-builder/confirm',dict(id=s['id'],revision=s['revision']))
        run=call('/api/agent-builder/test',dict(id=s['saved_agent_id'],content='저장한 조건으로 실행해줘.'))
        report['run']=run
        report['passed']=run['status']=='succeeded' and [r['code'] for r in run['rows']]==['B','A']
    else:report['passed']=False
except Exception as e:
    report.update(passed=False,error_type=type(e).__name__,error=str(e)[:500],error_body=e.read().decode()[:1000] if hasattr(e,'read') else None)
dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('session','run','connection')},ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
