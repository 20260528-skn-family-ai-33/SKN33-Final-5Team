import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
import {shouldRefreshAgentRuns,refreshedAgentRun,startAgentRunRefresh} from '../app/agent-run-refresh';

// Exercise the component's real effect and dependencies without adding a DOM test runner.
const filename=new URL('../app/agent-studio.tsx',import.meta.url);
const source=ts.createSourceFile(filename.pathname,readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=source.statements.find((node):node is ts.FunctionDeclaration=>ts.isFunctionDeclaration(node)&&node.name?.text==='AgentStudio');
assert.ok(component?.body);
const refreshState=component.body.statements.find(node=>ts.isVariableStatement(node)&&node.declarationList.declarations.some(declaration=>declaration.name.getText(source)==='refreshRuns'));
const refreshEffect=component.body.statements.find(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&node.expression.expression.getText(source)==='useEffect'&&node.getText(source).includes('/api/agent-builder/agent?id=')&&(node.getText(source).includes('setInterval')||node.getText(source).includes('startAgentRunRefresh')));
assert.ok(refreshEffect,'AgentStudio must retain an effect that refreshes run results');
const script=ts.transpileModule(`(()=>{const queuedSourceRuns=(globalThis as any).queuedSourceRuns;${refreshState?.getText(source)||''}\n${refreshEffect.getText(source)}})()`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;

type Run={id:string;status:string;answer?:string;public_projection?:{history_preview?:boolean}};
const run=(status:string,id='manual-run'):Run=>({id,status});
const completed=run('succeeded');
const flush=()=>new Promise<void>(resolve=>setImmediate(resolve));
function deferred<T>(){
 let resolve!:(value:T)=>void,reject!:(reason:Error)=>void;
 const promise=new Promise<T>((accept,fail)=>{resolve=accept;reject=fail;});
 return {promise,resolve,reject};
}
function harness({scheduled=false,current=run('running'),history=[current],pinned=false}:{scheduled?:boolean;current?:Run;history?:Run[];pinned?:boolean}={}){
 let cleanup:(()=>void)|undefined,deps:unknown[]|undefined;
 const requests:string[]=[];
 const context:any={
  selected:{id:'manual-agent',version:1,config:{schedule:scheduled?{frequency:'daily'}:null}},
  testRun:current,history,busy:'',inFlight:{current:false},generation:{current:0},pinnedRun:{current:pinned},
  shouldRefreshAgentRuns,refreshedAgentRun,startAgentRunRefresh,encodeURIComponent,setInterval,clearInterval,
  applyRunInputs:()=>{},setSchedule:()=>{},setSource:()=>{},setJobs:()=>{},
  queuedSourceRuns:[],
 };
 const snapshot=(tests:Run[],id=context.selected.id)=>({agent:{...context.selected,id},tests,resources:[],schedule:null,source:null,jobs:[]});
 let load:(path:string)=>Promise<ReturnType<typeof snapshot>|{run:Run}>=async()=>snapshot([completed]);
 context.request=(path:string)=>{requests.push(path);return load(path);};
 for(const [setter,key] of [['setSelected','selected'],['setTestRun','testRun'],['setHistory','history'],['setQueuedSourceRuns','queuedSourceRuns']])context[setter]=(value:any)=>{context[key]=typeof value==='function'?value(context[key]):value;};
 context.useEffect=(effect:()=>void|(()=>void),next:unknown[])=>{
  if(deps&&deps.length===next.length&&deps.every((value,index)=>Object.is(value,next[index])))return;
  cleanup?.();deps=[...next];cleanup=effect()||undefined;
 };
 createContext(context);
 return {context,requests,snapshot,load:(next:typeof load)=>{load=next;},render:()=>runInContext(script,context),stop:()=>cleanup?.()};
}

for(const status of ['succeeded','failed','needs_input'])test(`restored manual running result refreshes to ${status} and then stops polling`,async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const h=harness();t.after(h.stop);
 h.load(async()=>h.snapshot([run(status)]));h.render();
 t.mock.timers.tick(5000);await flush();h.render();
 assert.equal(h.requests.length,1,'a manual workflow must fetch its restored running result');
 assert.equal(h.context.testRun.status,status);
 assert.equal(h.context.history[0].status,status);
 t.mock.timers.tick(15000);await flush();
 assert.equal(h.requests.length,1,'manual polling must stop after the run leaves running');
});

test('idle manual results do not poll, while scheduled workflows keep refreshing after completion',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 for(const status of ['succeeded','failed','needs_input']){
  const h=harness({current:run(status)});h.render();
  t.mock.timers.tick(5000);await flush();
  assert.equal(h.requests.length,0);h.stop();
 }
 const scheduled=harness({scheduled:true});t.after(scheduled.stop);scheduled.render();
 t.mock.timers.tick(5000);await flush();scheduled.render();
 assert.equal(scheduled.context.testRun.status,'succeeded');
 t.mock.timers.tick(5000);await flush();
 assert.equal(scheduled.requests.length,2);
});

test('a running history entry keeps manual polling active while an older run is pinned',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const older={...run('succeeded','older-run'),answer:'old answer'};
 const h=harness({current:older,history:[run('running'),older],pinned:true});t.after(h.stop);
 h.load(async()=>h.snapshot([completed,{...older,answer:'saved answer'}]));h.render();
 t.mock.timers.tick(5000);await flush();h.render();
 assert.equal(h.context.testRun.id,'older-run');assert.equal(h.context.testRun.answer,'saved answer');
 assert.equal(h.context.history[0].status,'succeeded');
 t.mock.timers.tick(5000);await flush();assert.equal(h.requests.length,1);
});

test('a pinned run outside the recent history window remains selected',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const older=run('succeeded','older-run');
 const h=harness({scheduled:true,current:older,pinned:true});t.after(h.stop);h.render();
 t.mock.timers.tick(5000);await flush();
 assert.equal(h.context.testRun,older);
});

test('poll requests do not overlap and a previous selection cannot overwrite the current result',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const h=harness();t.after(h.stop);
 const oldRequest=deferred<ReturnType<typeof h.snapshot>>(),newRequest=deferred<ReturnType<typeof h.snapshot>>();
 h.load(path=>path.endsWith('new-agent')?newRequest.promise:oldRequest.promise);h.render();
 t.mock.timers.tick(5000);t.mock.timers.tick(15000);
 assert.equal(h.requests.length,1,'slow requests must not overlap');
 h.context.generation.current++;h.context.selected={...h.context.selected,id:'new-agent'};
 h.context.testRun=run('running','new-run');h.context.history=[h.context.testRun];h.render();
 t.mock.timers.tick(5000);assert.equal(h.requests.length,2);
 oldRequest.resolve(h.snapshot([completed],'manual-agent'));await flush();
 assert.equal(h.context.selected.id,'new-agent');assert.equal(h.context.testRun.id,'new-run');assert.equal(h.context.testRun.status,'running');
 newRequest.resolve(h.snapshot([run('succeeded','new-run')],'new-agent'));await flush();h.render();
 assert.equal(h.context.testRun.id,'new-run');assert.equal(h.context.testRun.status,'succeeded');
});

test('starting an operation disposes the old refresh request before its delayed response arrives',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const h=harness({scheduled:true});t.after(h.stop);const pending=deferred<ReturnType<typeof h.snapshot>>();
 h.load(()=>pending.promise);h.render();t.mock.timers.tick(5000);
 h.context.inFlight.current=true;h.context.busy='실행 중';h.render();
 pending.resolve(h.snapshot([completed]));await flush();
 assert.equal(h.context.testRun.status,'running');
 t.mock.timers.tick(10000);assert.equal(h.requests.length,1);
 h.context.inFlight.current=false;h.context.busy='';h.load(async()=>h.snapshot([completed]));h.render();
 t.mock.timers.tick(5000);await flush();assert.equal(h.context.testRun.status,'succeeded');
});

test('a temporary refresh failure preserves the running result and retries',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const h=harness();t.after(h.stop);
 h.load(async()=>{throw new Error('temporary fetch failure');});h.render();
 t.mock.timers.tick(5000);await flush();assert.equal(h.context.testRun.status,'running');
 h.load(async()=>h.snapshot([completed]));t.mock.timers.tick(5000);await flush();h.render();
 assert.equal(h.requests.length,2);assert.equal(h.context.testRun.status,'succeeded');
});

test('new-file polling waits for its queued job instead of selecting an unrelated old success',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const older=run('succeeded','old-run');
 const h=harness({scheduled:true,current:null as any,history:[older]});t.after(h.stop);
 h.context.queuedSourceRuns=['file-job'];h.load(async()=>h.snapshot([older]));h.render();
 t.mock.timers.tick(5000);await flush();h.render();
 assert.equal(h.context.testRun,null);assert.deepEqual(h.context.queuedSourceRuns,['file-job']);
 const actual=run('running','file-job');h.load(async()=>h.snapshot([older,actual]));
 t.mock.timers.tick(5000);await flush();h.render();
 assert.equal(h.context.testRun.id,'file-job');assert.equal(h.context.testRun.status,'running');
 assert.equal(h.context.queuedSourceRuns.length,0);
});

test('pinned history previews hydrate before polling replaces the answer and late details cannot overwrite another workflow',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const older={...run('succeeded','older-run'),answer:'전체 과거 요약 본문과 마지막 항목'};
 const preview={...older,answer:'축약된 과거 요약',public_projection:{history_preview:true}};
 const newer=run('succeeded','newest-run'),pending=deferred<{run:Run}>();let details=0;
 const h=harness({scheduled:true,current:older,history:[newer,older],pinned:true});t.after(h.stop);
 h.load(async path=>{
  if(path==='/api/agent-builder/run?id=older-run')return ++details===1?{run:older}:pending.promise;
  return h.snapshot([newer,preview]);
 });
 h.render();t.mock.timers.tick(5000);await flush();h.render();
 assert.deepEqual(h.requests,['/api/agent-builder/agent?id=manual-agent','/api/agent-builder/run?id=older-run']);
 assert.equal(h.context.testRun.id,'older-run');assert.equal(h.context.testRun.answer,older.answer);
 assert.equal(h.context.history.find((item:Run)=>item.id==='older-run').answer,older.answer);
 t.mock.timers.tick(5000);await flush();assert.equal(details,2);
 h.context.generation.current++;h.context.selected={...h.context.selected,id:'other-agent'};
 h.context.testRun={...run('succeeded','other-run'),answer:'현재 워크플로우의 결과'};h.context.history=[h.context.testRun];h.render();
 pending.resolve({run:{...older,answer:'늦게 도착한 과거 상세'}});await flush();
 assert.equal(h.context.selected.id,'other-agent');assert.equal(h.context.testRun.id,'other-run');
 assert.equal(h.context.testRun.answer,'현재 워크플로우의 결과');assert.equal(h.context.history[0].id,'other-run');
});
