import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
// Exercise the real component code with delayed responses, without a DOM dependency.
const sf=ts.createSourceFile('studio.tsx',readFileSync(new URL('../app/agent-studio.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=sf.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='AgentStudio');assert.ok(component?.body);
const statements=component.body.statements;
const explanation=sf.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='explainIncompleteWorkflow');if(!explanation)throw new Error('Missing workflow explanation');
const explanationText=explanation.getText(sf);
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
 const c:any={screen:'run',executionMode:'manual',crypto:{randomUUID:()=>'00000000-0000-4000-8000-000000000081'},AbortController,setTimeout,clearTimeout,URLSearchParams,URL,location:{search,href:'http://localhost/'+search},generation:{current:0},inFlight:{current:false},pinnedRun:{current:false},runDetailRequest:{current:0},selected:null,session:null,recovery:null,testRun:null,history:[],chosenSnapshots:[],schedule:null,fileInput:{current:null},sourceInput:{current:null},runFileInput:{current:null},asBase64:async()=>'',text:'새 업무',testText:'네, 승인합니다',credentialId:'account',model:'model',chosenRefs:[],runFiles:[],replyDraft:'',agentResources:[],input:{current:null},resultPanel:{current:null},requestAnimationFrame:()=>{},usesTool:()=>false,go:()=>{},window:{scrollTo:(options:any)=>{c.scrollTarget=options;},history:{replaceState:()=>{}}},refresh:async()=>{},localStorage:{getItem:(k:string)=>storage.get(k)||null,setItem:(k:string,v:string)=>storage.set(k,v),removeItem:(k:string)=>storage.delete(k)},useEffect:(f:()=>()=>void)=>{cleanup=f();}};
 c.queuedSourceRuns=[];c.freshFiles={session:'',ids:[]};c.externalPromptContext={current:''};c.externalMessages=[];
 c.sourceDirtyRef={current:false};c.scheduleDirtyRef={current:false};c.window.confirm=()=>true;
 c.setScheduleSourceDirty=(value:boolean)=>{c.scheduleSourceDirty=value;};
 for(const key of ['Selected','Session','AgentResources','RunInputs','Schedule','Source','Jobs','TestRun','History','Loading','Error','Busy','Text','PendingMessage','Recovery','TestText','SelectedNodeId','EditorPanel','InitialLoadFailed','ChosenRefs','ChosenSnapshots','RunFiles','ScheduleNotice','ReplyDraft','QueuedSourceRuns','SaveReviewOpen','ScheduleSettingsDirty','ClarificationDirty','FreshFiles','ExternalMessages']){const name=key[0].toLowerCase()+key.slice(1);c['set'+key]=(v:any)=>{c[name]=typeof v==='function'?v(c[name]):v;};}
 Object.defineProperty(c,'canStartRun',{get:()=>!!c.testText.trim()||!!c.selected?.config?.schedule});
 c.request=(path:string,body:any)=>{requests.push({path,body});return load(path,body);};createContext(c);
 runInContext(compile(explanationText+'\n'+['applyRunInputs','clearRunInputs','confirmSettingsExit','clearSettingsDirty','remember','recoverConversation','reloadConversation','resumeConversation','ensureSession','send','reset','openAgent','edit','save','resumeRun','runTest','attachResource','uploadRun','uploadSource','selectRun','refreshScheduleSource'].map(handler).join('\n')),c);

 return {c,storage,requests,load:(f:typeof load)=>{load=f;},mount:()=>runInContext(compile(restoreText),c),stop:()=>cleanup?.()};
}
test('explicit service overrides old conversation; persist selection only after successful load',async()=>{
 const h=harness('?service=B',{'a4b-builder-session':'old'});h.load(async()=>snapshot('B'));h.mount();await flush();assert.equal(h.c.selected.id,'B');assert.deepEqual(h.requests.map(r=>r.path),['/api/agent-builder/agent?id=B']);assert.equal(h.storage.has('a4b-builder-session'),false);assert.equal(h.storage.get('a4b-selected-agent'),'B');
});
test('clarification reply sends the browser timezone and authoritative session revision',async()=>{
 const h=harness();
 h.c.Intl={DateTimeFormat:()=>({resolvedOptions:()=>({timeZone:'America/New_York'})})};
 h.c.session={id:'conversation',revision:4,status:'needs_clarification'};
 h.load(async()=>({id:'conversation',revision:5,status:'ready',messages:[]}));
 await h.c.send(undefined,'JSON으로 해줘','a'.repeat(24));
 assert.deepEqual({...h.requests[0].body},{id:'conversation',revision:4,content:'JSON으로 해줘',userTimezone:'America/New_York',questionId:'a'.repeat(24),generationRequestId:'00000000-0000-4000-8000-000000000081'});
 assert.equal(h.c.session.revision,5);
});
test('Gmail to Slack request uses the agent builder conversation and keeps the selected channel ID',async()=>{
 const h=harness();
 h.c.Intl={DateTimeFormat:()=>({resolvedOptions:()=>({timeZone:'Asia/Seoul'})})};
 h.c.text='gmail에서 안읽은 메일 3건 요약 정리해서 slack에 올려줘';
 h.load(async path=>path==='/api/agent-builder/sessions'?{id:'gmail-slack',revision:0}:path==='/api/agent-builder/message'?{id:'gmail-slack',revision:1,status:'needs_clarification',messages:[]}:snapshot('A'));
 await h.c.send();
 assert.deepEqual(h.requests.map(item=>item.path),['/api/agent-builder/sessions','/api/agent-builder/message']);
 assert.equal(h.requests[1].body.content,'gmail에서 안읽은 메일 3건 요약 정리해서 slack에 올려줘');
 assert.equal(h.c.externalMessages.length,0);
 await h.c.send(undefined,'C0123456789','channel-question-id');
 assert.equal(h.requests[2].path,'/api/agent-builder/message');
 assert.equal(h.requests[2].body.content,'C0123456789');
 assert.equal(h.requests[2].body.questionId,'channel-question-id');
});
test('external service clarification keeps the original request and opens the generated workflow',async()=>{
 const h=harness();let opened='';h.c.onExternalFlow=(id:string)=>{opened=id;};
 h.c.text='Google Sheets 재고를 Slack으로 알려줘';
 h.load(async path=>path==='/api/workflows/from-request'?{summary:'시트 범위를 알려주세요.',questions:[{question:'어느 범위인가요?'}]}:snapshot('A'));
 await h.c.send();
 assert.equal(h.c.error,'');
 assert.equal(h.c.externalMessages.map((message:any)=>message.role).join(','),'user,assistant');
 assert.match(h.c.externalMessages[1].content,/어느 범위/);
 assert.match(h.c.externalPromptContext.current,/Google Sheets 재고/);
 h.c.text='재고!A1:D100';
 h.load(async path=>path==='/api/workflows/from-request'?{workflow:{id:'generated-flow'}}:snapshot('A'));
 await h.c.send();
 assert.match(h.requests[1].body.prompt,/추가 답변: 재고!A1:D100/);
 assert.equal(opened,'generated-flow');
 assert.equal(h.c.externalPromptContext.current,'');
 assert.equal(h.c.externalMessages.length,0);
});
test('external request without model questions keeps context and shows requirement details',async()=>{
 const h=harness();h.c.text='Google Sheets 재고를 Slack으로 알려줘';
 h.load(async path=>path==='/api/workflows/from-request'?{summary:'재고 흐름',questions:[],requirements:[{id:'sheet',description:'시트 범위 지정',status:'needs_input'}]}:snapshot('A'));
 await h.c.send();
 assert.equal(h.c.error,'');
 assert.match(h.c.externalMessages[1].content,/아직 정해지지 않은 내용/);
 assert.match(h.c.externalMessages[1].content,/시트 범위 지정/);
 assert.match(h.c.externalPromptContext.current,/Google Sheets 재고/);
 h.c.text='재고!A1:D100';
 h.load(async path=>path==='/api/workflows/from-request'?{workflow:{id:'generated-flow'}}:snapshot('A'));
 await h.c.send();
 assert.match(h.requests[1].body.prompt,/추가 답변: 재고!A1:D100/);
});
test('external clarification keeps each turn and explains missing versus unsupported work without repeating the summary',async()=>{
 const h=harness();h.c.text='Google Sheets 행을 Slack으로 정리해줘';
 h.load(async()=>({summary:'Google Sheets 행을 Slack으로 정리해줘',questions:[{question:'어느 채널에 보낼까요?'}],checklist:['지원 여부 확인 필요: 행에 별표 달기'],requirements:[
  {id:'channel',description:'Slack 채널 지정',status:'needs_input'},
  {id:'star',description:'행에 별표 달기',status:'unsupported'},
 ]}));
 await h.c.send();
 assert.equal(h.c.error,'');
 assert.match(h.c.externalMessages[1].content,/아직 정해지지 않은 내용:\n• Slack 채널 지정/);
 assert.match(h.c.externalMessages[1].content,/지원 여부를 확인할 작업:\n• 행에 별표 달기/);
 assert.match(h.c.externalMessages[1].content,/어느 채널에 보낼까요/);
 assert.equal(h.c.externalMessages[1].content.includes('지원 여부 확인 필요: 행에 별표 달기'),false);
 h.c.text='C0123456789';
 h.load(async()=>({summary:'아직 확인이 필요합니다.',questions:[{question:'메일 기간은 언제인가요?'}]}));
 await h.c.send();
 assert.equal(h.c.externalMessages.map((message:any)=>message.role).join(','),'user,assistant,user,assistant');
 assert.equal(h.c.externalMessages[2].content,'C0123456789');
 assert.match(h.c.externalMessages[3].content,/메일 기간은 언제인가요/);
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
 const h=harness();h.c.session={id:'draft',revision:1,status:'ready'};h.c.runFiles=[{id:'input'}];h.load(async()=>{throw Error('rejected');});await h.c.save();assert.equal(h.c.session.status,'ready');assert.equal(h.c.runFiles.length,1);assert.equal(h.requests.filter(r=>r.path.includes('/confirm')).length,1);
});
test('scheduled execution never submits hidden transient files or review drafts',async()=>{
 const h=harness();h.c.screen='schedule';h.c.selected={id:'A',config:{schedule:{frequency:'daily'}}};h.c.runFiles=[{id:'old'}];h.c.replyDraft='old';h.c.testText='';h.load(async()=>({id:'run'}));await h.c.runTest();assert.equal(h.requests[0].body.fileIds.length,0);assert.equal(h.requests[0].body.replyDraft,undefined);
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
for(const action of ['attachResource','uploadRun','uploadSource'])test(`${action} rejects oversized files before reading or sending`,async()=>{
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

test('starting a new workflow restores the page top without scrolling to the focused composer',()=>{
 const h=harness('?screen=workflow');let focusOptions:any;h.c.input.current={focus:(options:any)=>{focusOptions=options;}};h.c.requestAnimationFrame=(f:()=>void)=>f();h.c.reset();assert.equal(h.c.scrollTarget.top,0);assert.equal(focusOptions.preventScroll,true);assert.equal(h.c.editorPanel,'conversation');
});

test('new-file expansion queues its own jobs and cannot reuse an older successful result',async()=>{
 const h=harness();h.c.selected={id:'A',config:{schedule:{frequency:'hourly'}}};h.c.testText='';h.c.testRun={id:'old',status:'succeeded',answer:'예전 완료'};
 h.load(async path=>path.endsWith('/test')?{status:'expanded',enqueued_count:2,job_ids:['new-1','new-2']}:snapshot('A'));
 await h.c.runTest();
 assert.deepEqual(Array.from(h.c.queuedSourceRuns),['new-1','new-2']);assert.equal(h.c.testRun,null);
 assert.match(h.c.scheduleNotice,/새 파일 2개.*대기열/);assert.equal(h.c.inFlight.current,false);
 assert.equal(h.requests.filter(item=>item.path.endsWith('/test')).length,1);
});

test('a skipped new-file request shows the skip reason without treating old results as a new success',async()=>{
 const h=harness();h.c.selected={id:'A',config:{schedule:{frequency:'monthly'}}};h.c.testText='';h.c.testRun={id:'old',status:'succeeded'};
 h.load(async path=>path.endsWith('/test')?{status:'skipped',message:'새로 처리할 파일이 없습니다.'}:snapshot('A'));
 await h.c.runTest();assert.equal(h.c.testRun,null);assert.equal(h.c.queuedSourceRuns.length,0);
 assert.equal(h.c.scheduleNotice,'새로 처리할 파일이 없습니다.');
});


test('a scheduled workflow still accepts files and review drafts on its direct execution screen',async()=>{
 const h=harness();h.c.selected={id:'A',config:{schedule:{frequency:'daily'}}};h.c.runFiles=[{id:'manual-file'}];h.c.replyDraft='manual draft';h.load(async()=>({id:'run'}));
 await h.c.runTest();assert.deepEqual(Array.from(h.requests[0].body.fileIds),['manual-file']);assert.equal(h.requests[0].body.replyDraft,'manual draft');
});

test('new sessions do not send a preset execution mode, including legacy URL or state values',async()=>{
 for(const mode of ['manual','scheduled']){
  const h=harness('?executionMode='+mode);h.c.executionMode=mode;h.load(async()=>({id:'new',revision:0}));await h.c.ensureSession();assert.equal(h.requests[0].body.executionMode,undefined);
 }
});

test('lost message acknowledgement restores the committed session without replaying the message',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'waiting_confirmation'}]};
  return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[{role:'user',content:'새 업무'},{role:'assistant',content:'설정 확인'}],status:'ready'};
 });
 await h.c.send();assert.equal(h.c.session.revision,6);assert.equal(h.c.recovery,null);assert.equal(h.c.error,'');
 assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
});
test('a server-side generation failure is shown after refreshing the checkpoint',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('generic request failure');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'failed',reason:'모델 응답 길이 제한'}]};
  return {id:'s',revision:5,messages:[],status:'validation_failed'};
 });
 await h.c.send();assert.equal(h.c.session.status,'validation_failed');assert.equal(h.c.error,'모델 응답 길이 제한');assert.equal(h.c.recovery,null);
});
test('recovery waits for a running server request and prevents duplicate submissions',async()=>{
 const h=harness(),before={id:'s',revision:5,messages:[],status:'needs_clarification'};h.c.session=before;
 let running=true;
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:running?'running':'waiting_input'}]};
  return running?before:{id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[{role:'user',content:'새 업무'},{role:'assistant',content:'기준은 무엇인가요?'}],status:'needs_clarification'};
 });
 await h.c.send();assert.equal(h.c.recovery.running,true);await h.c.send();
 assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
 running=false;await h.c.reloadConversation();assert.equal(h.c.session.revision,6);assert.equal(h.c.recovery,null);assert.equal(h.c.error,'');
});
test('offline recovery retains the request and can resume with a read-only reload',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async()=>{throw Error('offline');});await h.c.send();
 assert.equal(h.c.recovery.running,false);assert.equal(h.c.recovery.content,'새 업무');assert.match(h.c.error,/다시 불러온 뒤/);
 h.load(async path=>path.includes('/generation_history')?{requests:[]}:{id:'s',revision:5,messages:[],status:'needs_clarification'});
 await h.c.reloadConversation();assert.equal(h.c.recovery,null);assert.equal(h.c.text,'새 업무');
 assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
});
test('an unrelated message committed in another tab does not discard this input',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('revision changed');
  if(path.includes('/generation_history'))return {requests:[]};
  return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000082',messages:[{role:'user',content:'다른 탭의 업무'}],status:'needs_clarification'};
 });
 await h.c.send();assert.equal(h.c.session.revision,6);assert.equal(h.c.text,'새 업무');assert.equal(h.c.error,'revision changed');
});
test('lost save acknowledgement opens the saved workflow without confirming twice',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'ready'};
 h.load(async path=>{
  if(path.endsWith('/confirm'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',workflow_id:'A',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'confirm',session_revision:5,status:'succeeded'}]};
  if(path.includes('/session?'))return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[],status:'saved',saved_agent_id:'A'};
  return snapshot('A');
 });
 await h.c.save();assert.equal(h.c.selected.id,'A');assert.equal(h.c.recovery,null);
 assert.equal(h.requests.filter(r=>r.path.endsWith('/confirm')).length,1);
});

for(const includeSchedule of [true,false])test('lost save acknowledgement preserves the explicit reservation choice: '+includeSchedule,async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'ready',draft:{schedule:{frequency:'daily',hour:9,minute:0}}};
 h.load(async path=>{
  if(path.endsWith('/confirm'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',workflow_id:'A',result_revision:6,action:'confirm',session_revision:5,status:'succeeded'}]};
  if(path.includes('/session?'))return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[],status:'saved',saved_agent_id:'A'};
  return snapshot('A');
 });
 await h.c.save(includeSchedule);
 const writes=h.requests.filter(r=>r.path.endsWith('/confirm'));
 assert.equal(writes.length,1);assert.equal(writes[0].body.includeSchedule,includeSchedule);
 assert.equal(writes[0].body.generationRequestId,'00000000-0000-4000-8000-000000000081');
 assert.equal(h.c.selected.id,'A');assert.equal(h.c.recovery,null);
});

test('typed clarification answers survive response recovery without a duplicate post or false error',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,action:'message',session_revision:5,status:'waiting_confirmation'}]};
  return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[{role:'user',content:'형식 → JSON'}],status:'ready'};
 });
 await assert.doesNotReject(h.c.send(undefined,'형식 → JSON',undefined,[{questionId:'a'.repeat(24),text:'형식',value:'JSON'}]));
 const writes=h.requests.filter(r=>r.path.endsWith('/message'));
 assert.equal(writes.length,1);assert.deepEqual(JSON.parse(JSON.stringify(writes[0].body.answers)),[{questionId:'a'.repeat(24),value:'JSON'}]);
 assert.equal(h.c.session.revision,6);assert.equal(h.c.error,'');
});

test('a committed session wins over a stale running generation record',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'running'}]};
  return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[{role:'user',content:'새 업무'},{role:'assistant',content:'완료한 초안을 확인하세요.'}],status:'ready'};
 });
 await h.c.send();assert.equal(h.c.session.revision,6);assert.equal(h.c.recovery,null);assert.equal(h.c.error,'');
});

test('the next answer uses the revision recovered from a lost response',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 let sent=0;
 h.load(async path=>{
  if(path.endsWith('/message')){if(++sent===1)throw Error('response lost');return {id:'s',revision:7,messages:[],status:'ready'};}
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'waiting_input'}]};
  return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[{role:'user',content:'새 업무'},{role:'assistant',content:'실행 주기를 알려주세요.'}],status:'needs_clarification'};
 });
 await h.c.send();h.c.text='매주';await h.c.send();
 const writes=h.requests.filter(r=>r.path.endsWith('/message'));
 assert.equal(writes.length,2);assert.equal(writes[1].body.revision,6);assert.equal(writes[1].body.content,'매주');
});
test('a saved workflow is recovered even when its audit still says running',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'ready'};
 h.load(async path=>{
  if(path.endsWith('/confirm'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',workflow_id:'A',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'confirm',session_revision:5,status:'running'}]};
  if(path.includes('/session?'))return {id:'s',revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[],status:'saved',saved_agent_id:'A'};
  return snapshot('A');
 });
 await h.c.save();assert.equal(h.c.selected.id,'A');assert.equal(h.c.recovery,null);
 assert.equal(h.requests.filter(r=>r.path.endsWith('/confirm')).length,1);
});

test('recovery releases the navigation lock after bounded running checks without resending',async()=>{
 const h=harness(),before={id:'s',revision:5,messages:[],status:'needs_clarification'};h.c.session=before;
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'running'}]};
  return before;
 });
 await h.c.send();
 for(let i=1;i<15;i++)await h.c.reloadConversation();
 assert.equal(h.c.recovery.running,false);assert.equal(h.c.text,'새 업무');assert.equal(h.c.inFlight.current,false);
 assert.match(h.c.error,/다른 화면으로 이동/);
 assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
 await h.c.send();assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
});

test('recovery timeout aborts a hung read and releases the busy state',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'needs_clarification'};
 let expire!:()=>void,cleared=false;
 h.c.setTimeout=(fn:()=>void)=>{expire=fn;return 1;};h.c.clearTimeout=()=>{cleared=true;};
 h.c.request=async(path:string,_body:any,_method:any,signal:AbortSignal)=>{
  if(path.endsWith('/message'))throw Error('response lost');
  return new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('read timeout')),{once:true}));
 };
 const pending=h.c.send();await flush();expire();await pending;
 assert.equal(h.c.busy,'');assert.equal(h.c.inFlight.current,false);assert.equal(h.c.recovery.running,false);
 assert.equal(h.c.text,'새 업무');assert.equal(cleared,true);
});

test('a completed reused-input reply recovers even before its running audit finalizes',async()=>{
 const h=harness(),before={id:'s',revision:5,messages:[{role:'user',content:'새 업무'}],status:'validation_failed'};h.c.session=before;
 h.load(async path=>{
  if(path.endsWith('/message'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',action:'message',session_revision:5,request_text:'새 업무',status:'running'}]};
  return {...before,revision:6,last_generation_request_id:'00000000-0000-4000-8000-000000000081',status:'ready',messages:[...before.messages,{role:'assistant',content:'초안을 확인하세요.'}]};
 });
 await h.c.send();assert.equal(h.c.text,'');assert.equal(h.c.recovery,null);assert.equal(h.c.error,'');
 assert.equal(h.requests.filter(r=>r.path.endsWith('/message')).length,1);
});

test('correlation: a saved result from another tab cannot complete this confirmation',async()=>{
 const h=harness();h.c.session={id:'s',revision:5,messages:[],status:'ready'};
 h.load(async path=>{
  if(path.endsWith('/confirm'))throw Error('response lost');
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',action:'confirm',session_revision:5,status:'running'}]};
  return {id:'s',revision:6,last_generation_request_id:'other-request',messages:[],status:'saved',saved_agent_id:'OTHER'};
 });
 await h.c.save();assert.equal(h.requests.some(call=>call.path.includes('/agent?')),false);
 assert.equal(h.c.selected,null);assert.equal(h.c.recovery.running,true);
});

test('correlation: each explicit submission gets a distinct id before transmission',async()=>{
 const h=harness(),ids=['00000000-0000-4000-8000-000000000091','00000000-0000-4000-8000-000000000092'];
 h.c.crypto.randomUUID=()=>ids.shift();h.c.session={id:'s',revision:5,messages:[],status:'ready'};
 h.load(async()=>({id:'s',revision:6,messages:[],status:'ready'}));
 await h.c.send();h.c.text='다른 요청';await h.c.send();
 const writes=h.requests.filter(call=>call.path.endsWith('/message'));
 assert.deepEqual(writes.map(call=>call.body.generationRequestId),['00000000-0000-4000-8000-000000000091','00000000-0000-4000-8000-000000000092']);

});
