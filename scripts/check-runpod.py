"""Check the configured RunPod gateway with synthetic inputs only."""
import json, os, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
for line in (ROOT/'.env').read_text().splitlines():
 if line.strip() and not line.lstrip().startswith('#') and '=' in line:
  name,value=line.split('=',1)
  os.environ[name.strip()]=value.strip().strip('\"\'')
base=os.environ['A4B_LLM_BASE_URL'].rstrip('/')
key=os.environ.get('A4B_LLM_API_KEY','')
os.environ.update(A4B_MAIN_PROVIDER='ollama',A4B_MAIN_BASE_URL=base,A4B_MAIN_API_KEY=key,
 A4B_VERIFIER_PROVIDER='ollama',A4B_VERIFIER_BASE_URL=base.removesuffix('/v1'),A4B_VERIFIER_API_KEY=key)
report={'endpoint':base,'started_at':time.time()}
try:
 try:
  with urlopen(Request(base+'/models',headers={'User-Agent':'A4B/0.1'}),timeout=20) as r:report['unauthenticated_status']=r.status
 except HTTPError as e:report['unauthenticated_status']=e.code
 with urlopen(Request(base+'/models',headers={'Authorization':'Bearer '+key,'User-Agent':'A4B/0.1'}),timeout=30) as r:
  report['models']=json.load(r)
 from agent_builder.model_factory import create_chat_model
 start=time.perf_counter()
 reply=create_chat_model(key,os.environ['A4B_LLM_MODEL'],timeout=180,max_tokens=64).invoke('연결 확인입니다. 다른 설명 없이 연결 성공이라고만 답하세요.')
 report['main']={'seconds':time.perf_counter()-start,'content':reply.content}
 from a4b_verifier.client import OllamaConfig,health,call_qwen
 from a4b_verifier.contracts import VerificationInput
 cfg=OllamaConfig.from_env()
 report['verifier_health']=health(cfg)
 data=VerificationInput.model_validate_json((ROOT/'Backend/integrations/qwen_verifier/examples/inputs/01_ready.json').read_text())
 result,meta=call_qwen(data,cfg)
 report['verifier']={'output':result.model_dump(mode='json'),'meta':meta}
 report['passed']=report['unauthenticated_status']==401 and bool(reply.content) and report['verifier_health']['model_present']
except Exception as e:
 report.update(passed=False,error_type=type(e).__name__,error=str(e).replace(key,'[REDACTED]')[:600])
path=ROOT/'output/runpod-live-check.json'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('models','verifier')},ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
