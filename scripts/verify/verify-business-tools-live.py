import argparse,base64,hashlib,io,json,time,urllib.request,urllib.error,sys
from pathlib import Path
parser=argparse.ArgumentParser(description='Real local-model generation, save, execute and download checks using synthetic inputs only.')
parser.add_argument('--base-url',default='http://127.0.0.1:3047')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--model',default='a4b-qwen35-4b-16k')
parser.add_argument('--cases',default='scalar,filter,url,xlsx,join')
options=parser.parse_args()
BASE=options.base_url.rstrip('/')+'/api/agent-builder/'
ROOT=options.output;ROOT.mkdir(parents=True,exist_ok=True);REPORT=ROOT/'report.json'
report={'model':options.model,'svm_model_sha256':None,'real_model':True,'external_actions':False,'cases':[]}

def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
def emit(stage,**data):print(json.dumps({'stage':stage,**data},ensure_ascii=False),flush=True)
def call(action,body=None,binary=False):
 req=urllib.request.Request(BASE+action,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=650) as response:
   data=response.read();return data if binary else json.loads(data)
 except urllib.error.HTTPError as e:
  content=e.read().decode();raise RuntimeError(content[:2000]) from None

cases=[
 {'id':'scalar','request':'변수 x 값은 7입니다. x * 3 + 2를 계산해서 결과 숫자를 화면에 보여주는 워크플로우를 만들어줘.','required':{'scalar_calculate'},'expected':23},
 {'id':'filter','request':'첨부 CSV에서 amount가 100 이상인 행만 원래 순서대로 골라 JSON 파일로 저장해줘. 빈 값이면 오류로 멈추고 중복은 모두 유지해.','required':{'read_table','filter_sort','write_result'},'files':[('sales.csv','id,amount\nA,50\nB,100\nC,150\n')],'expected_ids':['B','C']},
 {'id':'url','request':'https://example.com/orders?status=paid#items 이 URL을 scheme, host, path, query, fragment로 분리해 화면에 보여주는 워크플로우를 만들어줘. 외부 사이트에 접속할 필요는 없어.','required':{'url_transform'}},
 {'id':'xlsx','request':'실제 XLSX 파일을 만드는 워크플로우를 만들어줘. 시트 이름은 Data, 열은 id와 amount, 데이터는 첫 행 id=A amount=10, 둘째 행 id=B amount=20이야. 새 엑셀 파일로 저장해줘.','required':{'create_spreadsheet'}},
 {'id':'join','request':'첨부한 orders.csv와 customers.csv 두 표를 customer_id 열로 left join해줘. orders를 왼쪽, customers를 오른쪽으로 쓰고 고객 키는 customers에서 유일해야 해. null 키는 오류로 멈추고 최대 결과는 100행으로 제한해. 고객이 없는 주문도 남기고 결과를 JSON 파일로 저장해줘.','required':{'join_tables'},'files':[('orders.csv','order_id,customer_id,amount\nO1,C1,10\nO2,C2,20\nO3,C3,30\n'),('customers.csv','customer_id,name\nC1,Kim\nC2,Lee\n')]},
]
cases=[c for c in cases if c['id'] in options.cases.split(',')]
cat=call('catalog');report['catalog']={'business':cat.get('business_tool_count'),'registered':len(cat['tools'])};save();emit('catalog',**report['catalog'])
for case in cases:
 item={'id':case['id'],'request':case['request'],'attempts':[], 'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('Backend/agent_builder/design/nodes/conversation.py'),Path('Backend/workflow_engine/catalog/canonical.py'),Path('Backend/workflow_engine/workflow.py'),Path('Backend/workflow_engine/executors/canonical_operation.py'),Path('Backend/contracts/models.py'),Path('Backend/infrastructure/llm/model_factory.py'),Path('Backend/agent_builder/design/nodes/verification.py'),Path('Backend/infrastructure/llm/ollama_chat_model.py'),Path('Backend/agent_builder/design/operation_steps.py'),Path('Backend/agent_builder/design/name_policy.py')]}};report['cases'].append(item);save()
 emit('case_start',id=case['id'])
 try:
  s=call('sessions',{'credentialId':'local-model','model':report['model']});item['session_id']=s['id'];save()
  for name,content in case.get('files',[]):
   s=call('upload',{'id':s['id'],'revision':s['revision'],'fileName':name,'data':base64.b64encode(content.encode()).decode()})
  for attempt in range(2):
   started=time.monotonic()
   content=case['request'] if attempt==0 else '원래 요청의 수치·자료·결과 형식을 유지하면서 현재 코드 검증 오류와 누락된 노드·연결만 고쳐줘. 이미 지정한 업무 조건은 다시 묻지 말고 실제 등록된 도구의 동작·입출력 규격을 확인해서 전체 초안을 반환해줘.'
   s=call('message',{'id':s['id'],'revision':s['revision'],'content':content})
   draft=s.get('draft') or {};wf=draft.get('workflow') or {}
   obs={'status':s.get('status'),'seconds':round(time.monotonic()-started,2),'issues':s.get('issues'),'semantic_issues':s.get('semantic_issues'),'questions':draft.get('questions'),'unsupported':draft.get('unsupported'),'tools':draft.get('tool_ids'),'nodes':wf.get('nodes'),'edges':wf.get('edges'),'capability':s.get('capability_assessment'),'condition':s.get('condition_ledger'),'trace':s.get('trace')}
   item['attempts'].append(obs);item['last_session']=s;save();emit('draft',id=case['id'],attempt=attempt,status=s.get('status'),seconds=obs['seconds'],issues=s.get('issues'),tools=draft.get('tool_ids'),questions=draft.get('questions'))
   if s.get('status')=='ready':break
   if s.get('status') in ('unsupported','needs_clarification','collecting') or draft.get('questions'):break
  if s.get('status')!='ready':item['result']='generation_blocked';save();continue
  actual={n['tool'] for n in s['draft']['workflow']['nodes']};item['required_tools_present']=case['required']<=actual
  if not item['required_tools_present']:item['result']='required_tool_missing';save();emit('missing_tool',id=case['id'],missing=sorted(case['required']-actual));continue
  s=call('confirm',{'id':s['id'],'revision':s['revision']});item['agent_id']=s['saved_agent_id'];emit('saved',id=case['id'])
  run=call('test',{'id':s['saved_agent_id'],'content':'검증용 요청을 실행해줘.'});item['run']=run
  assert run['status']=='succeeded',run.get('error')
  if case['id']=='scalar':
   values=[o.get('result') for o in run.get('outputs',{}).values() if 'result' in o];assert 23 in values,(values,run['answer'])
  if case['id']=='filter':assert [r['id'] for r in run['rows']]==case['expected_ids'],run['rows']
  if case['id']=='url':
   values=[o.get('result') for o in run.get('outputs',{}).values() if isinstance(o.get('result'),dict)];assert any(v.get('scheme')=='https' and v.get('host')=='example.com' and v.get('path')=='/orders' and v.get('query')=={'status':'paid'} and v.get('fragment')=='items' for v in values),values
  if case['id']=='xlsx':
   data=call('report?id='+run['id'],binary=True);assert data[:2]==b'PK';(ROOT/'live-result.xlsx').write_bytes(data);item['download_bytes']=len(data)
   from openpyxl import load_workbook
   wb=load_workbook(io.BytesIO(data),read_only=True);rows=list(wb['Data'].values);wb.close()
   assert rows==[('id','amount'),('A',10),('B',20)],rows
   item['verified_workbook_rows']=rows
  if case['id']=='join':
   rows=next(o['table']['rows'] for o in run['outputs'].values() if 'table' in o and any('name' in r for r in o['table']['rows']));assert len(rows)==3 and rows[0]['name']=='Kim' and rows[-1]['name'] is None,rows
  item['result']='passed';emit('case_passed',id=case['id'],run_id=run['id'])
 except Exception as e:
  item['result']='failed';item['error']=str(e);emit('case_failed',id=case['id'],error=str(e)[:1500])
 save()
report['summary']={status:sum(c.get('result')==status for c in report['cases']) for status in ['passed','generation_blocked','required_tool_missing','failed']};save();emit('complete',**report['summary'])

sys.exit(0 if report['summary']['passed']==len(cases) else 1)
