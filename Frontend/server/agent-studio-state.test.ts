import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
// Exercise the real component code with delayed responses, without a DOM dependency.
const sf=ts.createSourceFile('studio.tsx',readFileSync(new URL('../app/agent-studio.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=sf.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='AgentStudio');assert.ok(component?.body);
const statements=component.body.statements;
const restore=statements.find(n=>ts.isExpressionStatement(n)&&ts.isCallExpression(n.expression)&&n.expression.expression.getText(sf)==='useEffect'&&n.getText(sf).includes('await refresh()'));assert.ok(restore);
const restoreText=restore.getText(sf);
function handler(name:string){const n=statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(n);return n.getText(sf);}
const compile=(s:string)=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const flush=()=>new Promise<void>(r=>setImmediate(r));
function deferred(){let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>{resolve=r;});return {promise,resolve};}
const snapshot=(id:string)=>({agent:{id},resources:[{id:'resource-'+id}],tests:[{id:'run-'+id}],schedule:null,source:null,jobs:[]});
function harness(search='',stored:Record<string,string>={}){
 const storage=new Map(Object.entries(stored)),requests:{path:string;body:any}[]=[];let cleanup:(()=>void)|undefined;
 let load:(p:string,b:any)=>Promise<any>=async()=>snapshot('A');
 const c:any={URLSearchParams,URL,location:{search,href:'http://localhost/'+search},generation:{current:0},inFlight:{current:false},pinnedRun:{current:false},runDetailRequest:{current:0},selected:null,session:null,testRun:null,history:[],chosenSnapshots:[],schedule:null,fileInput:{current:null},sourceInput:{current:null},runFileInput:{current:null},asBase64:async()=>'',text:'새 업무',testText:'네, 승인합니다',credentialId:'account',model:'model',chosenRefs:[],runFiles:[],replyDraft:'',agentResources:[],input:{current:null},resultPanel:{current:null},requestAnimationFrame:()=>{},usesTool:()=>false,go:()=>{},window:{history:{replaceState:()=>{}}},refresh:async()=>{},localStorage:{getItem:(k:string)=>storage.get(k)||null,setItem:(k:string,v:string)=>storage.set(k,v),removeItem:(k:string)=>storage.delete(k)},useEffect:(f:()=>()=>void)=>{cleanup=f();}};
 for(const key of ['Selected','Session','AgentResources','Schedule','Source','Jobs','TestRun','History','Loading','Error','Busy','Text','TestText','SelectedNodeId','ChosenRefs','ChosenSnapshots','RunFiles','ScheduleNotice','ReplyDraft']){const name=key[0].toLowerCase()+key.slice(1);c['set'+key]=(v:any)=>{c[name]=typeof v==='function'?v(c[name]):v;};}
 c.request=(path:string,body:any)=>{requests.push({path,body});return load(path,body);};createContext(c);
 runInContext(compile(['clearRunInputs','remember','ensureSession','send','reset','openAgent','edit','save','resumeRun','runTest','upload','uploadRun','uploadSource','selectRun'].map(handler).join('\n')),c);
 return {c,storage,requests,load:(f:typeof load)=>{load=f;},mount:()=>runInContext(compile(restoreText),c),stop:()=>cleanup?.()};
}
test('explicit service overrides old conversation; persist selection only after successful load',async()=>{
 const h=harness('?service=B',{'a4b-builder-session':'old'});h.load(async()=>snapshot('B'));h.mount();await flush();assert.equal(h.c.selected.id,'B');assert.deepEqual(h.requests.map(r=>r.path),['/api/agent-builder/agent?id=B']);assert.equal(h.storage.has('a4b-builder-session'),false);assert.equal(h.storage.get('a4b-selected-agent'),'B');
});
test('failed explicit link reports error and preserves conversation without fallback',async()=>{
 const h=harness('?service=B',{'a4b-builder-session':'old'});h.load(async()=>{throw Error('not found');});h.mount();await flush();assert.equal(h.c.selected,null);assert.equal(h.c.error,'not found');assert.equal(h.storage.get('a4b-builder-session'),'old');assert.equal(h.requests.length,1);
});
for(const action of ['reset','openAgent','send'])test(`delayed service restore cannot overwrite ${action}`,async()=>{
 const h=harness('?service=A'),pending=deferred();h.load(path=>path.endsWith('id=A')?pending.promise:Promise.resolve(path.includes('/sessions')?{id:'new-session',revision:0}:path.includes('/message')?{id:'new-session',revision:1}:snapshot('B')));h.mount();await flush();
 if(action==='openAgent')await h.c.openAgent({id:'B'});else if(action==='send')await h.c.send();else h.c.reset();pending.resolve(snapshot('A'));await flush();
 assert.equal(h.c.selected?.id,action==='openAgent'?'B':undefined);assert.notEqual(h.c.agentResources?.[0]?.id,'resource-A');if(action==='send')assert.equal(h.c.session.id,'new-session');
});
test('reset before bootstrap finishes prevents remembered session restoration',async()=>{
 const h=harness('',{'a4b-builder-session':'old'}),boot=deferred();h.c.refresh=()=>boot.promise;h.mount();h.c.reset();boot.resolve({});await flush();assert.equal(h.requests.length,0);assert.equal(h.c.session,null);assert.equal(h.c.loading,false);
});
test('unmounted restoration cannot change state or storage',async()=>{
 const h=harness('?service=A',{'a4b-builder-session':'old'}),pending=deferred();h.load(()=>pending.promise);h.mount();await flush();h.stop();pending.resolve(snapshot('A'));await flush();assert.equal(h.c.selected,null);assert.equal(h.storage.get('a4b-builder-session'),'old');
});
test('unsaved conversation, saved conversation, and last selected service restore',async()=>{
 const draft=harness('',{'a4b-builder-session':'draft'});draft.load(async()=>({id:'draft',status:'needs_clarification'}));draft.mount();await flush();assert.equal(draft.c.session.id,'draft');
 const saved=harness('',{'a4b-builder-session':'saved'});saved.load(async path=>path.includes('/session?')?{status:'saved',saved_agent_id:'A'}:snapshot('A'));saved.mount();await flush();assert.equal(saved.c.selected.id,'A');assert.equal(saved.storage.has('a4b-builder-session'),false);
 const agent=harness('',{'a4b-selected-agent':'A'});agent.mount();await flush();assert.equal(agent.c.selected.id,'A');
});
for(const status of ['succeeded','running','needs_input','failed'])test(`successful resume response (${status}) clears submitted answer`,async()=>{
 const h=harness();h.c.selected={id:'A',config:{}};h.c.testRun={id:'run',question:{}};h.load(async()=>({id:'run',status,question:status==='needs_input'?{}:null}));await h.c.resumeRun();assert.equal(h.c.testText,'');assert.equal(h.requests[0].body.content,'네, 승인합니다');await h.c.runTest();assert.equal(h.requests.length,1,'old answer must not start a new run');
});
test('resume network failure preserves answer for retry',async()=>{
 const h=harness();h.c.testRun={id:'run',question:{}};h.load(async()=>{throw Error('offline');});await h.c.resumeRun();assert.equal(h.c.testText,'네, 승인합니다');assert.equal(h.c.error,'offline');assert.equal(h.c.inFlight.current,false);
});

test('delayed unsaved session response cannot replace a new conversation',async()=>{
 const h=harness('',{'a4b-builder-session':'old'}),pending=deferred();
 h.load(path=>path.includes('/session?')?pending.promise:Promise.resolve(path.includes('/sessions')?{id:'new',revision:0}:{id:'new',revision:1}));
 h.mount();await flush();await h.c.send();pending.resolve({id:'old',status:'needs_clarification'});await flush();
 assert.equal(h.c.session.id,'new');assert.equal(h.storage.get('a4b-builder-session'),'new');
});
test('saved-session nested agent lookup cannot undo a reset',async()=>{
 const h=harness('',{'a4b-builder-session':'saved'}),pending=deferred();h.load(path=>path.includes('/session?')?Promise.resolve({status:'saved',saved_agent_id:'A'}):pending.promise);
 h.mount();await flush();assert.equal(h.requests.length,2);h.c.reset();pending.resolve(snapshot('A'));await flush();assert.equal(h.c.selected,null);assert.equal(h.storage.has('a4b-builder-session'),false);
});
test('obsolete restoration error cannot replace the current view error',async()=>{
 const h=harness('?service=A'),pending=deferred();h.load(async()=>{await pending.promise;throw Error('old failure');});h.mount();await flush();h.c.reset();h.c.error='current error';pending.resolve({});await flush();assert.equal(h.c.error,'current error');
});
test('service-only link initially opens the run screen; explicit screen remains respected',()=>{
 const state=statements.find(n=>ts.isVariableStatement(n)&&n.getText(sf).includes('[screen,setScreen]'));assert.ok(state);
 for(const [search,expected] of [['?service=A','run'],['?service=A&screen=workflow','workflow'],['?screen=nodes','nodes'],['','workflow']]){
  const c:any={URLSearchParams,location:{search},useState:(v:any)=>[v,()=>{}]};createContext(c);
  const declaration=statements.find(n=>ts.isVariableStatement(n)&&n.getText(sf).includes('const initialScreen='));assert.ok(declaration);
  assert.equal(runInContext(compile(declaration.getText(sf)+'\n'+state.getText(sf)+'\nscreen;'),c),expected);
 }
});

// Editing must clear the old service URL so a reload restores the new conversation.
test('edit removes the service URL while preserving the newly created editing session',async()=>{
 const h=harness('?screen=run&service=A');h.c.selected={id:'A',credential_id:'account',model:'model',config:{name:'A'}};
 let href='';h.c.window.history.replaceState=(_state:any,_title:string,url:URL)=>{href=url.toString();};
 h.load(async()=>({id:'editing',revision:0}));await h.c.edit();
 assert.equal(h.c.selected,null);assert.equal(h.c.session.id,'editing');assert.equal(h.storage.get('a4b-builder-session'),'editing');assert.equal(new URL(href).searchParams.has('service'),false);
});

test('saving succeeds even if unrelated list refresh fails',async()=>{
 const h=harness();h.c.session={id:'draft',revision:1,status:'ready'};h.c.runFiles=[{id:'stale'}];h.c.replyDraft='old';h.c.refresh=async()=>{throw Error('catalog offline');};
 h.load(async path=>path.includes('/confirm')?{id:'draft',status:'saved',saved_agent_id:'A'}:snapshot('A'));await h.c.save();
 assert.equal(h.c.selected.id,'A');assert.equal(h.c.runFiles.length,0);assert.equal(h.c.replyDraft,'');assert.match(h.c.error,/저장은 완료/);assert.equal(h.c.inFlight.current,false);
});
test('failed detail load after save can recover without confirming twice',async()=>{
 const h=harness();h.c.session={id:'draft',revision:1,status:'ready'};
 h.load(async path=>{if(path.includes('/confirm'))return {id:'draft',status:'saved',saved_agent_id:'A'};throw Error('offline');});await h.c.save();
 assert.equal(h.c.session.status,'saved');assert.equal(h.c.selected,null);await h.c.save();assert.equal(h.requests.filter(r=>r.path.includes('/confirm')).length,1);
 h.load(async()=>snapshot('A'));await h.c.openAgent({id:'A'});assert.equal(h.c.selected.id,'A');
});
test('confirm failure preserves draft and unsent inputs',async()=>{
 const h=harness();h.c.session={id:'draft',revision:1,status:'ready'};h.c.runFiles=[{id:'input'}];h.load(async()=>{throw Error('rejected');});await h.c.save();assert.equal(h.c.session.status,'ready');assert.equal(h.c.runFiles.length,1);assert.equal(h.requests.length,1);
});
test('scheduled execution never submits hidden transient files or review drafts',async()=>{
 const h=harness();h.c.selected={id:'A',config:{schedule:{frequency:'daily'}}};h.c.runFiles=[{id:'old'}];h.c.replyDraft='old';h.c.testText='';h.load(async()=>({id:'run'}));await h.c.runTest();assert.equal(h.requests[0].body.fileIds.length,0);assert.equal(h.requests[0].body.replyDraft,undefined);
});
test('manual execution keeps explicit files and review draft',async()=>{
 const h=harness();h.c.selected={id:'A',config:{}};h.c.runFiles=[{id:'new'}];h.c.replyDraft='new';h.load(async()=>({id:'run'}));await h.c.runTest();assert.equal(h.requests[0].body.fileIds[0],'new');assert.equal(h.requests[0].body.replyDraft,'new');
});
test('edit clears previous run inputs after new session creation',async()=>{
 const h=harness();h.c.selected={id:'A',config:{},credential_id:'c',model:'m'};h.c.runFiles=[{id:'old'}];h.c.replyDraft='old';h.load(async()=>({id:'edit'}));await h.c.edit();assert.equal(h.c.runFiles.length,0);assert.equal(h.c.replyDraft,'');assert.equal(h.c.testText,'');
});
for(const action of ['save','runTest','resumeRun','uploadRun'])test(`double ${action} cannot duplicate a pending mutation`,async()=>{
 const h=harness(),pending=deferred();h.c.session={id:'draft',status:'ready',revision:1};h.c.selected={id:'A',config:{}};h.c.testRun={id:'run'};h.load(()=>pending.promise);
 const first=h.c[action]({name:'data.csv',size:5});await h.c[action]({name:'data.csv',size:5});assert.equal(h.requests.length,1);
 pending.resolve(action==='save'?{id:'draft',status:'saved',saved_agent_id:'A'}:action==='uploadRun'?{asset:{id:'file'}}:{id:'run'});await first;
});
for(const action of ['upload','uploadRun','uploadSource'])test(`${action} rejects oversized files before reading or sending`,async()=>{
 const h=harness();h.c.selected={id:'A'};let reads=0;h.c.asBase64=async()=>{reads++;return '';};await h.c[action]({size:8*1024*1024+1});assert.equal(reads,0);assert.equal(h.requests.length,0);assert.match(h.c.error,/8MB/);
});
test('obsolete openAgent error cannot replace current view error',async()=>{
 const h=harness(),pending=deferred();h.load(async()=>{await pending.promise;throw Error('old');});const open=h.c.openAgent({id:'A'});h.c.reset();h.c.error='new';pending.resolve({});await open;assert.equal(h.c.error,'new');
});


test('late historical-run failure is ignored after switching service',async()=>{
 const h=harness(),pending=deferred();h.c.history=[{id:'old'}];h.load(async()=>{await pending.promise;throw Error('obsolete');});const reading=h.c.selectRun('old');h.c.generation.current++;h.c.error='current';pending.resolve({});await reading;assert.equal(h.c.error,'current');
});
test('out-of-order run details cannot replace the latest historical selection',async()=>{
 const h=harness(),old=deferred(),latest=deferred();h.c.history=[{id:'old'},{id:'latest'}];h.load(path=>path.endsWith('old')?old.promise:latest.promise);
 const a=h.c.selectRun('old'),b=h.c.selectRun('latest');latest.resolve({run:{id:'latest',answer:'new detail'}});await b;old.resolve({run:{id:'old',answer:'old detail'}});await a;assert.equal(h.c.testRun.id,'latest');assert.equal(h.c.testRun.answer,'new detail');
});
test('unpinning history or starting a mutation ignores pending detail errors',async()=>{
 for(const mode of ['unpin','mutation']){const h=harness(),pending=deferred();h.c.history=[{id:'old'}];h.load(async()=>{await pending.promise;throw Error('obsolete');});const reading=h.c.selectRun('old');h.c.error='current';if(mode==='unpin')h.c.pinnedRun.current=false;else h.c.inFlight.current=true;pending.resolve({});await reading;assert.equal(h.c.error,'current');}
});
