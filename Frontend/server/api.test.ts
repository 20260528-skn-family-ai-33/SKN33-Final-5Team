import assert from 'node:assert/strict';
import {SERVER_STDIO,requestShutdown,removeDirectory,scaled} from './test-process';
import test from 'node:test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import type { Flow, FlowNode, Execution, Json } from '../lib/workflow';

const project = resolve(fileURLToPath(new URL('..', import.meta.url)));
const delay = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds));

async function freePort(): Promise<number> {
  const socket = createServer();
  await new Promise<void>((resolve,reject) => { socket.once('error',reject); socket.listen(0,'127.0.0.1',resolve); });
  const address = socket.address();
  assert.ok(address && typeof address !== 'string');
  const port = address.port;
  await new Promise<void>((resolve,reject) => socket.close(error => error ? reject(error) : resolve()));
  return port;
}

class Server {
  child?: ChildProcess;
  output = '';
  constructor(readonly port: number, readonly directory: string) {}
  get base() { return `http://127.0.0.1:${this.port}`; }

  async start(): Promise<void> {
    assert.ok(!this.child, 'previous test server must stop before restart');
    this.output = '';
    const environment: NodeJS.ProcessEnv = { ...process.env, PORT: String(this.port), HOST: '127.0.0.1', EASYFLOW_DATA_DIR: this.directory };
    // Integration tests run our own process with no n8n configuration or external service.
    for (const key of Object.keys(environment)) if (/^N8N_/i.test(key)) delete environment[key];
    const child = this.child = spawn(process.execPath, ['--import','tsx','server/index.ts'], { cwd: project, env: environment, stdio: SERVER_STDIO });
    child.stdout?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    child.stderr?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    let launchError: Error | undefined;
    child.once('error', error => { launchError = error; });
    const deadline = Date.now() + scaled(12_000);
    while (Date.now() < deadline) {
      if (launchError) throw launchError;
      if (child.exitCode !== null || child.signalCode) throw new Error(`test server exited during startup:\n${this.output}`);
      try {
        const response = await fetch(this.base + '/api/health', { signal: AbortSignal.timeout(500) });
        if (response.ok) { const data = await response.json() as Json; assert.equal(data.standalone,true); return; }
      } catch { /* The listening socket may not be ready yet. */ }
      await delay(40);
    }
    throw new Error(`test server did not become ready:\n${this.output}`);
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    this.child = undefined;
    if (child.exitCode !== null || child.signalCode) return;
    const exit = new Promise<{code:number|null;signal:NodeJS.Signals|null}>(resolve => child.once('exit',(code,signal) => resolve({code,signal})));
    requestShutdown(child);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([exit, new Promise<never>((_,reject) => { timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(`test server did not shut down gracefully:\n${this.output}`)); }, scaled(4_000)); })]);
      assert.equal(result.code,0, `SIGTERM must close the database cleanly: ${this.output}`);
      assert.equal(result.signal,null);
    } finally { if (timer) clearTimeout(timer); }
  }

  async request(path: string, method = 'GET', data?: unknown, headers: Record<string,string> = {}): Promise<{status:number;data:Json;raw:string}> {
    const response = await fetch(this.base + path, { method, headers: {'Content-Type':'application/json',...headers}, ...(data !== undefined ? {body:JSON.stringify(data)} : {}), signal: AbortSignal.timeout(5_000) });
    const raw = await response.text();
    return {status:response.status,data:JSON.parse(raw) as Json,raw};
  }

  async execution(id: string, predicate: (execution:Execution) => boolean): Promise<Execution> {
    const deadline = Date.now() + 8_000;
    let last: Execution | undefined;
    while (Date.now() < deadline) {
      const response = await this.request('/api/executions?id='+encodeURIComponent(id));
      assert.equal(response.status,200);
      last = response.data.execution as Execution;
      if (predicate(last)) return last;
      await delay(30);
    }
    throw new Error(`execution ${id} did not reach expected state: ${JSON.stringify(last)}`);
  }
}

function stage(type: string, name: string, parameters: Json = {}): FlowNode {
  return {id:randomUUID(),name,type:`n8n-nodes-base.${type}`,typeVersion: type === 'set' ? 3.4 : type === 'webhook' ? 2.1 : 1,position:[0,0],parameters};
}
function workflow(name: string, nodes: FlowNode[]): Flow {
  return {id:randomUUID(),name,nodes,connections:Object.fromEntries(nodes.slice(0,-1).map((node,index) => [node.name,{main:[[{node:nodes[index+1].name,type:'main',index:0}]]}])),settings:{timezone:'Asia/Seoul',executionOrder:'v1'},active:false};
}
function rawSet(name: string) { return stage('set',name,{mode:'raw',jsonOutput:'={{ $json }}',includeOtherFields:false,options:{}}); }

test('standalone server persists and executes workflows, credentials, schedules and webhooks without n8n', {timeout:scaled(60_000)}, async t => {
  const directory = await mkdtemp(join(tmpdir(),'easyflow-api-'));
  const server = new Server(await freePort(),directory);
  let savedFlow: Flow; let successId = ''; let waitingFlow: Flow; let canceledId = ''; let credentialId = ''; let scheduleFlow: Flow; let hookFlow: Flow; let hookExecutionId = ''; let hookToken = '';
  const secret = 'example-only-bearer-token-'+randomUUID();
  try {
    await server.start();

    await t.test('OAuth authorization begins locally without exposing secrets and canceled callbacks consume the state',async()=>{
      const privateValue='example-oauth-client-secret-'+randomUUID();const input={name:'Google 로그인 테스트',service:'googleSheets',clientId:'example-client-id.apps.googleusercontent.com',clientSecret:privateValue};
      const begin=await server.request('/api/oauth/begin','POST',input);assert.equal(begin.status,200);assert.equal(begin.data.redirectUri,server.base+'/api/oauth/callback');const authorization=new URL(begin.data.authorizationUrl);assert.equal(authorization.hostname,'accounts.google.com');assert.equal(authorization.searchParams.get('code_challenge_method'),'S256');assert.ok(!begin.raw.includes(privateValue));assert.equal(authorization.searchParams.has('client_secret'),false);
      const denied=await server.request('/api/oauth/begin','POST',input,{Origin:'https://unrelated.example'});assert.equal(denied.status,403);
      const callback=await fetch(server.base+'/api/oauth/callback?state='+encodeURIComponent(begin.data.stateId)+'&error=access_denied');assert.equal(callback.status,400);assert.match(callback.headers.get('content-type')||'',/text\/html/);const html=await callback.text();assert.ok(!html.includes(privateValue));assert.match(html,/easyflow-oauth/);
      const replay=await fetch(server.base+'/api/oauth/callback?state='+encodeURIComponent(begin.data.stateId)+'&code=not-a-real-code');assert.equal(replay.status,400);assert.ok(!(await replay.text()).includes('not-a-real-code'));
    });
    await t.test('file upload, CSV extraction and download return actual bytes with safe metadata',async()=>{
      const csv='name,score\r\n"예시 고객, A",92\r\n예시 고객 B,68\r\n';const uploaded=await server.request('/api/files','POST',{data:Buffer.from(csv).toString('base64'),fileName:'../입력.csv',mimeType:'text/csv'});assert.equal(uploaded.status,200);assert.equal(uploaded.data.input[0].__easyflowBinary.data.fileName,'입력.csv');
      assert.equal((await server.request('/api/files','POST',{data:'AB==',fileName:'bad.txt'})).status,400);assert.equal((await server.request('/api/files','POST',{data:'AA==',property:'__proto__'})).status,400);
      const extract=stage('extractFromFile','CSV 읽기',{operation:'csv',binaryPropertyName:'data'});extract.typeVersion=1.2;const convert=stage('convertToFile','CSV 쓰기',{operation:'csv',binaryPropertyName:'result',fileName:'정리한 결과.csv',headerRow:'true'});convert.typeVersion=1.1;const fileFlow=workflow('파일 입출력 검증',[stage('manualTrigger','파일 시작'),extract,convert]);assert.equal((await server.request('/api/workflows','POST',fileFlow)).status,200);
      const started=await server.request('/api/runs','POST',{workflowId:fileFlow.id,mode:'learning',input:uploaded.data.input});assert.equal(started.status,202);const result=await server.execution(started.data.executionId,e=>['success','error'].includes(e.status));assert.equal(result.status,'success',result.error);assert.deepEqual(result.runs[1].output[0],[{name:'예시 고객, A',score:'92'},{name:'예시 고객 B',score:'68'}]);
      const url=server.base+'/api/files/download?'+new URLSearchParams({executionId:result.id,runIndex:'2',item:'0',property:'result'});const download=await fetch(url);assert.equal(download.status,200);assert.equal(download.headers.get('x-content-type-options'),'nosniff');assert.match(download.headers.get('content-disposition')||'',/attachment.*filename\*=UTF-8''/);assert.equal(await download.text(),csv.trimEnd());
      const denied=await fetch(url,{headers:{Origin:'https://unrelated.example'}});assert.equal(denied.status,403);assert.equal((await fetch(url.replace('runIndex=2','runIndex=-1'))).status,400);
    });
    await t.test('binary inputs exceed the old JSON upload size while forged binary metadata is rejected',async()=>{
      const bytes=Buffer.alloc(2*1024*1024,97);const uploaded=await server.request('/api/files','POST',{data:bytes.toString('base64'),fileName:'large.txt',mimeType:'text/plain'});assert.equal(uploaded.status,200);
      const f=workflow('2MB 파일 그대로 전달',[stage('manualTrigger','파일 입력'),stage('noOp','파일 통과')]);assert.equal((await server.request('/api/workflows','POST',f)).status,200);const started=await server.request('/api/runs','POST',{workflowId:f.id,mode:'learning',input:uploaded.data.input});assert.equal(started.status,202);const result=await server.execution(started.data.executionId,e=>['success','error'].includes(e.status));assert.equal(result.status,'success',result.error);const download=await fetch(server.base+'/api/files/download?'+new URLSearchParams({executionId:result.id,runIndex:'1',property:'data'}));assert.equal(download.status,200);assert.equal(Buffer.compare(Buffer.from(await download.arrayBuffer()),bytes),0);
      uploaded.data.input[0].__easyflowBinary.data.fileSize=1;const invalid=await server.request('/api/runs','POST',{workflowId:f.id,mode:'learning',input:uploaded.data.input});assert.equal(invalid.status,400);assert.match(invalid.data.error,/크기/);
    });
    await t.test('default workflow executes live and the server owns its execution record', async () => {
      const response = await server.request('/api/workflows');
      assert.equal(response.status,200);
      const first = response.data.workflows.find((f:Flow)=>f.name==='나의 첫 번째 자동화') as Flow;
      assert.ok(first?.id);
      const run = await server.request('/api/runs','POST',{workflowId:first.id,mode:'live'});
      assert.equal(run.status,202);
      assert.equal(typeof run.data.executionId,'string');
      successId = run.data.executionId;
      const result = await server.execution(successId,e => ['success','error','canceled'].includes(e.status));
      assert.equal(result.status,'success',result.error);
      assert.equal(result.mode,'live');
      assert.equal(result.runs.length,4);
      assert.ok(result.runs.every(r => r.status === 'success'));
      assert.equal(result.runs.at(-1)?.output[0][0].score,92);
      const forged = await server.request('/api/executions','POST',{id:'forged-record',workflowId:first.id,status:'success',runs:[]});
      assert.equal(forged.status,404);
      assert.equal((await server.request('/api/executions?id=forged-record')).status,404);
      assert.equal((await server.request('/api/n8n')).status,404);
    });

    await t.test('workflow persistence rejects malformed graphs and retains caller input', async () => {
      const proposed = workflow('API 저장 확인',[stage('manualTrigger','시작'),rawSet('입력 보존')]);
      proposed.n8nId = 'unused-legacy-id';
      const saved = await server.request('/api/workflows','POST',proposed);
      assert.equal(saved.status,200);
      savedFlow = saved.data.workflow;
      assert.equal(savedFlow.id,proposed.id);
      assert.equal(savedFlow.n8nId,undefined);
      assert.equal(savedFlow.settings.timezone,'Asia/Seoul');
      const payload = [{example:'로컬 입력',count:3}];
      const run = await server.request('/api/runs','POST',{workflowId:savedFlow.id,mode:'live',input:payload});
      assert.equal(run.status,202);
      const result = await server.execution(run.data.executionId,e => ['success','error'].includes(e.status));
      assert.equal(result.status,'success',result.error);
      assert.deepEqual(result.runs.at(-1)?.output,[payload]);
      const broken = workflow('잘못된 연결',[stage('manualTrigger','시작')]);
      broken.connections = {'시작':{main:[[{node:'존재하지 않는 단계',type:'main',index:0}]]}};
      assert.equal((await server.request('/api/workflows','POST',broken)).status,400);
      const flows = (await server.request('/api/workflows')).data.workflows as Flow[];
      assert.ok(flows.some(f => f.id === savedFlow.id));
      assert.ok(!flows.some(f => f.id === broken.id));
    });

    await t.test('credential POST and public GET never return secret values', async () => {
      const saved = await server.request('/api/credentials','POST',{name:'API 예시 계정',provider:'bearer',data:{token:secret}});
      assert.equal(saved.status,200);
      credentialId = saved.data.credential.id;
      assert.equal(typeof credentialId,'string');
      assert.ok(!saved.raw.includes(secret));
      assert.equal(saved.data.credential.data,undefined);
      const list = await server.request('/api/credentials');
      assert.equal(list.status,200);
      assert.ok(!list.raw.includes(secret));
      const credential = list.data.credentials.find((c:Json) => c.id === credentialId);
      assert.deepEqual(Object.keys(credential).sort(),['createdAt','id','name','provider','updatedAt']);
      // A generic bearer connection test validates local storage without contacting an API.
      assert.equal((await server.request('/api/credentials/test','POST',{id:credentialId})).status,200);
    });

    await t.test('a durable wait survives restart and can be canceled before its next step', async () => {
      waitingFlow = workflow('재시작과 취소 확인',[stage('manualTrigger','시작'),rawSet('대기 전'),stage('wait','30초 대기',{resume:'timeInterval',amount:scaled(30),unit:'seconds'}),rawSet('대기 후')]);
      assert.equal((await server.request('/api/workflows','POST',waitingFlow)).status,200);
      const run = await server.request('/api/runs','POST',{workflowId:waitingFlow.id,mode:'live',input:[{example:'재시작 보존'}]});
      assert.equal(run.status,202);
      canceledId = run.data.executionId;
      const waiting = await server.execution(canceledId,e => e.status === 'waiting');
      assert.equal(waiting.pendingNodeId,waitingFlow.nodes[2].id);
      assert.ok(Date.parse(waiting.resumeAt) > Date.now());
      assert.equal(waiting.runs.length,2);
      await server.stop(); await server.start();
      const restarted = await server.execution(canceledId,e => e.status === 'waiting');
      assert.equal(restarted.resumeAt,waiting.resumeAt);
      assert.deepEqual(restarted.runs,waiting.runs);
      assert.equal((await server.request('/api/executions/cancel','POST',{id:canceledId})).status,200);
      const canceled = await server.execution(canceledId,e => e.status === 'canceled');
      assert.ok(!canceled.runs.some(r => r.nodeId === waitingFlow.nodes[3].id));
    });

    await t.test('daily schedule activation stores Seoul time and rejects invalid settings', async () => {
      scheduleFlow = workflow('매일 오전 9시',[stage('scheduleTrigger','매일 시작',{rule:{interval:[{field:'days',daysInterval:1,triggerAtHour:9,triggerAtMinute:0}]}}),rawSet('결과')]);
      assert.equal((await server.request('/api/workflows','POST',scheduleFlow)).status,200);
      const activation = await server.request('/api/activation','POST',{workflowId:scheduleFlow.id,active:true});
      assert.equal(activation.status,200);
      const flow = activation.data.workflow as Flow;
      assert.equal(flow.active,true);
      assert.equal(flow.schedule.triggerId,flow.nodes[0].id);
      assert.ok(flow.schedule.nextRunAt > flow.schedule.activatedAt);
      const parts = new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(flow.schedule.nextRunAt);
      assert.equal(parts.find(p => p.type === 'hour')?.value,'09');
      assert.equal(parts.find(p => p.type === 'minute')?.value,'00');
      const deactivation = await server.request('/api/activation','POST',{workflowId:flow.id,active:false});
      assert.equal(deactivation.status,200); assert.equal(deactivation.data.workflow.schedule,undefined);
      const invalid = workflow('잘못된 예약',[stage('scheduleTrigger','예약',{rule:{interval:[{field:'days',triggerAtHour:24}]}})]);
      assert.equal((await server.request('/api/workflows','POST',invalid)).status,200);
      assert.equal((await server.request('/api/activation','POST',{workflowId:invalid.id,active:true})).status,400);
    });

    await t.test('saving unchanged active graphs preserves scheduling while graph changes deactivate them', async () => {
      const activation = await server.request('/api/activation','POST',{workflowId:scheduleFlow.id,active:true});
      assert.equal(activation.status,200);
      const active = activation.data.workflow as Flow;
      const unchanged = await server.request('/api/workflows','POST',active);
      assert.equal(unchanged.status,200);
      assert.equal(unchanged.data.workflow.active,true);
      assert.deepEqual(unchanged.data.workflow.schedule,active.schedule);
      const changed = structuredClone(unchanged.data.workflow) as Flow;
      changed.nodes[1].parameters.jsonOutput = '{"changed":true}';
      const saved = await server.request('/api/workflows','POST',changed);
      assert.equal(saved.status,200);
      assert.equal(saved.data.workflow.active,false);
      assert.equal(saved.data.workflow.schedule,undefined);
      assert.equal(saved.data.workflow.nodes[1].parameters.jsonOutput,'{"changed":true}');
    });

    await t.test('own scheduler tick starts and completes a live execution without a browser trigger', async () => {
      const scheduled = workflow('1초 예약 실행 확인',[stage('scheduleTrigger','1초마다 시작',{rule:{interval:[{field:'seconds',secondsInterval:1}]}}),rawSet('예약 입력 보존')]);
      assert.equal((await server.request('/api/workflows','POST',scheduled)).status,200);
      const activation = await server.request('/api/activation','POST',{workflowId:scheduled.id,active:true});
      assert.equal(activation.status,200);
      const due = activation.data.workflow.schedule.nextRunAt as number;
      let result: Execution | undefined;
      try {
        const deadline = Date.now() + 4_000;
        while (Date.now() < deadline) {
          const listed = await server.request('/api/executions');
          assert.equal(listed.status,200);
          result = (listed.data.executions as Execution[]).find(e => e.workflowId === scheduled.id && e.trigger === 'schedule');
          if (result && ['success','error','canceled'].includes(result.status)) break;
          await delay(50);
        }
        assert.ok(result,'the server timer must create its own scheduled execution');
        assert.equal(result.status,'success',result.error);
        assert.equal(result.mode,'live');
        assert.equal(result.runs.length,2);
        assert.ok(result.runs.every(run => run.status === 'success'));
        assert.equal(result.runs.at(-1)?.output[0][0].timestamp,new Date(due).toISOString());
        assert.equal(result.runs.at(-1)?.output[0][0].timezone,'Asia/Seoul');
        const stored = (await server.request('/api/workflows')).data.workflows.find((flow:Flow) => flow.id === scheduled.id) as Flow;
        assert.ok(stored.schedule.nextRunAt > due,'the timer must advance the persisted next execution time');
        const executions = (await server.request('/api/executions')).data.executions as Execution[];
        assert.equal(executions.filter(e => e.workflowId === scheduled.id && e.runs[0]?.input[0]?.timestamp === new Date(due).toISOString()).length,1);
      } finally {
        const stopped = await server.request('/api/activation','POST',{workflowId:scheduled.id,active:false});
        assert.equal(stopped.status,200);
        assert.equal(stopped.data.workflow.active,false);
        assert.equal(stopped.data.workflow.schedule,undefined);
      }
    });

    await t.test('activation rejects workflows with both manual and scheduled starts', async () => {
      const ambiguous = workflow('시작점이 두 개인 예약',[stage('manualTrigger','버튼 시작'),stage('scheduleTrigger','예약 시작',{rule:{interval:[{field:'seconds',secondsInterval:1}]}}),rawSet('결과')]);
      // Both starts have outgoing paths but neither trigger receives an incoming edge.
      ambiguous.connections = {
        '버튼 시작':{main:[[{node:'결과',type:'main',index:0}]]},
        '예약 시작':{main:[[{node:'결과',type:'main',index:0}]]},
      };
      assert.equal((await server.request('/api/workflows','POST',ambiguous)).status,200);
      const activation = await server.request('/api/activation','POST',{workflowId:ambiguous.id,active:true});
      assert.equal(activation.status,400);
      const stored = (await server.request('/api/workflows')).data.workflows.find((flow:Flow) => flow.id === ambiguous.id) as Flow;
      assert.equal(stored.active,false);
      assert.equal(stored.schedule,undefined);
    });

    await t.test('active webhook rejects bad tokens and starts its own execution', async () => {
      hookFlow = workflow('Webhook 입력 확인',[stage('webhook','외부 요청',{httpMethod:'POST',path:'api-test',responseMode:'onReceived',options:{}}),rawSet('받은 데이터')]);
      assert.equal((await server.request('/api/workflows','POST',hookFlow)).status,200);
      const active = await server.request('/api/activation','POST',{workflowId:hookFlow.id,active:true});
      assert.equal(active.status,200);
      hookToken = active.data.workflow.hookToken;
      assert.ok(hookToken.length >= 32);
      const details = await server.request('/api/webhook?workflowId='+hookFlow.id);
      assert.equal(details.status,200); assert.equal(details.data.active,true);
      const path = `/hooks/${hookFlow.id}/${hookFlow.nodes[0].id}`;
      assert.equal((await server.request(path,'POST',{example:'거부'})).status,401);
      assert.equal((await server.request(path+'?token=wrong','POST',{example:'거부'})).status,401);
      assert.equal((await server.request(path,'GET',undefined,{'x-easyflow-token':hookToken})).status,405);
      const accepted = await server.request(path+'?label=example','POST',{message:'예시 Webhook 데이터'},{'x-easyflow-token':hookToken,'x-example':'local-test'});
      assert.equal(accepted.status,200);
      hookExecutionId = accepted.data.executionId;
      assert.equal(typeof hookExecutionId,'string');
      const result = await server.execution(hookExecutionId,e => ['success','error'].includes(e.status));
      assert.equal(result.status,'success',result.error); assert.equal(result.trigger,'webhook');
      const output = result.runs.at(-1)?.output[0][0];
      assert.equal(output?.body.message,'예시 Webhook 데이터');
      assert.equal(output?.query.label,'example'); assert.equal(output?.headers['x-example'],'local-test');
      assert.equal(output?.headers['x-easyflow-token'],undefined);
      assert.ok(!JSON.stringify(result).includes(hookToken));
    });

    await t.test('scoped credentials persist safe service metadata and reject another app before requesting',async()=>{
      const c=await server.request('/api/credentials','POST',{name:'GitHub 테스트용 계정',provider:'bearer',data:{service:'github',token:'private-scoped-example'}});
      assert.equal(c.status,200);assert.equal(c.data.credential.service,'github');assert.ok(!c.raw.includes('private-scoped-example'));
      const n=stage('noOp','Telegram 알림');n.type='easyflow.telegram';n.credentialId=c.data.credential.id;n.parameters={operation:'sendMessage',chatId:'example',text:'전송되면 안 됨'};
      const f=workflow('잘못된 계정 검증',[stage('manualTrigger','버튼 시작'),n]);assert.equal((await server.request('/api/workflows','POST',f)).status,200);
      const refused=await server.request('/api/runs','POST',{workflowId:f.id,mode:'live'});assert.equal(refused.status,400);assert.match(refused.data.error,/계정/);
      const generation=await server.request('/api/assistant/generate','POST',{prompt:'자동화 초안 만들기',credentialId:c.data.credential.id});assert.equal(generation.status,400);assert.match(generation.data.error,/OpenAI/);
    });

    await t.test('only production webhook failures launch a separate error workflow and preserve original failure',async()=>{
      const handler=workflow('오류 정보 기록',[stage('errorTrigger','오류로 시작'),rawSet('오류 데이터')]);await server.request('/api/workflows','POST',handler);
      const failure=workflow('실패 후 별도 처리',[stage('webhook','외부 시작',{httpMethod:'POST',path:'error-test'}),stage('stopAndError','검증 실패',{errorType:'errorMessage',errorMessage:'예시 실패 이유'})]);failure.settings.errorWorkflow=handler.id;
      await server.request('/api/workflows','POST',failure);
      const manual=await server.request('/api/runs','POST',{workflowId:failure.id,mode:'live'});const manualResult=await server.execution(manual.data.executionId,e=>e.status==='error');assert.equal(manualResult.errorExecutionId,undefined);
      const activation=await server.request('/api/activation','POST',{workflowId:failure.id,active:true});assert.equal(activation.status,200);
      try{
        const started=await server.request(`/hooks/${failure.id}/${failure.nodes[0].id}`,'POST',{example:true},{'x-easyflow-token':activation.data.workflow.hookToken});assert.equal(started.status,200);
        const parent=await server.execution(started.data.executionId,e=>e.status==='error'&&!!e.errorExecutionId);assert.match(parent.error||'',/예시 실패 이유/);
        const child=await server.execution(parent.errorExecutionId,e=>e.status==='success');assert.equal(child.trigger,'error');assert.equal(child.parentExecutionId,parent.id);assert.equal(child.errorWorkflowOf,failure.id);assert.equal(child.runs.at(-1)?.output[0][0].execution.error.message,'예시 실패 이유');
      }finally{await server.request('/api/activation','POST',{workflowId:failure.id,active:false});}
    });

    await t.test('graceful restart preserves workflows, safe credential metadata and execution outcomes', async () => {
      await server.stop(); await server.start();
      const list = await server.request('/api/workflows');
      const saved = list.data.workflows as Flow[];
      assert.ok(saved.some(f => f.id === savedFlow.id && f.name === savedFlow.name));
      assert.ok(saved.some(f => f.id === waitingFlow.id));
      assert.equal(saved.find(f => f.id === scheduleFlow.id)?.active,false);
      assert.equal(saved.find(f => f.id === hookFlow.id)?.active,true);
      const credentials = await server.request('/api/credentials');
      assert.ok(credentials.data.credentials.some((c:Json) => c.id === credentialId));
      assert.ok(!credentials.raw.includes(secret));
      assert.equal((await server.execution(successId,() => true)).status,'success');
      assert.equal((await server.execution(hookExecutionId,() => true)).status,'success');
      const canceled = await server.execution(canceledId,() => true);
      assert.equal(canceled.status,'canceled');
      assert.ok(!canceled.runs.some(r => r.nodeId === waitingFlow.nodes[3].id));
      assert.ok(!JSON.stringify((await server.request('/api/executions')).data).includes(secret));
      assert.equal((await server.request('/api/activation','POST',{workflowId:hookFlow.id,active:false})).status,200);
      assert.equal((await server.request(`/hooks/${hookFlow.id}/${hookFlow.nodes[0].id}`,'POST',{}, {'x-easyflow-token':hookToken})).status,404);
    });
  } finally {
    try { await server.stop(); } finally { await removeDirectory(directory); }
  }
});
