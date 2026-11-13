import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const FRONTEND_ROOT=resolve(fileURLToPath(new URL('..',import.meta.url)));

const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/agent-studio.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const entryCompiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/entry-app.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const nodeCompiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/node-library.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',write:false,logLevel:'silent'}).outputFiles[0].text;
const sourceCompiled=buildSync({stdin:{contents:"export {ScheduleLibrarySourceSettings} from './app/schedule-library-source'; export {AuthProvider} from './app/auth';",resolveDir:FRONTEND_ROOT,sourcefile:'schedule-source-test.tsx'},absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
function load(code:string){const module={exports:{} as any};new Function('require','module','exports',code)(require,module,module.exports);return module.exports;}
let dom:JSDOM;
const globals=['crypto','window','document','navigator','location','localStorage','sessionStorage','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','Node','NodeFilter','SVGElement','HTMLButtonElement','HTMLSelectElement','DocumentFragment','MutationObserver','Event','CustomEvent','File','FileReader','getComputedStyle','ResizeObserver','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];
const old=new Map(globals.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
// ReactDOM feature detection must see the DOM on first import.
function installDOM(search=''){
 dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost/'+search,pretendToBeVisual:true});
 for(const key of globals){if(key==='IS_REACT_ACT_ENVIRONMENT')Object.defineProperty(globalThis,key,{value:true,writable:true,configurable:true});else if(key in dom.window)Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],writable:true,configurable:true});}
 Object.defineProperty(dom.window.crypto,'randomUUID',{value:()=>'00000000-0000-4000-8000-000000000081',configurable:true});
 dom.window.localStorage.setItem('a4b-product-tour-v1','done');
 Object.defineProperty(globalThis,'ResizeObserver',{value:class{observe(){}disconnect(){}},configurable:true});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};dom.window.scrollTo=()=>{};
}
installDOM();
const {createElement,act,StrictMode}=require('react');
const {createRoot}=require('react-dom/client');
const EntryApp=load(entryCompiled).default;
const AgentStudio=load(compiled).default,NodeLibrary=load(nodeCompiled).NodeLibrary;
const {ScheduleLibrarySourceSettings:SourceSettings,AuthProvider:SourceAuthProvider}=load(sourceCompiled);
const originalFetch=globalThis.fetch;
const agent=(id:string,name=id,schedule:any=null)=>({id,version:1,config:{name,purpose:'동작 검증',instructions:'요청 처리',tool_ids:['read_table'],requirements:[],questions:[],unsupported:[],document_ids:[],workflow:null,schedule},model:'gpt-4.1-mini',credential_id:'cred'});
const A=agent('A','서비스 A'),B=agent('B','서비스 B');
function snapshot(a:any,tests:any[]=[]) {return {agent:a,resources:[],schedule:a.config.schedule?{active:false,spec:a.config.schedule,next_run_at:'2026-11-01T00:00:00Z'}:null,source:null,jobs:[],tests};}
const tick=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>{resolve=r;});return {promise,resolve};}
async function setup(search='',stored:Record<string,string>={},override?:(path:string,body:any,method:string)=>Promise<any>|any,component?:any){
 dom.window.close();installDOM(search);for(const [k,v] of Object.entries(stored))localStorage.setItem(k,v);
 const calls:{path:string;body:any}[]=[];
 globalThis.fetch=(async(input:any,init:any)=>{
  const path=String(input),body=init?.body?JSON.parse(init.body):undefined;calls.push({path,body});
  let value=override?await override(path,body,init?.method||'GET'):undefined;
  if(value===undefined){if(path==='/api/credentials')value={credentials:[{id:'cred',provider:'openai',name:'테스트 연결'}]};else if(path==='/api/workflows')value={workflows:[]};else if(path.endsWith('/agents'))value={agents:[A,B]};else if(path.endsWith('/schedules'))value={workflows:[]};else if(path.endsWith('/nodes'))value={nodes:[],defaults:[]};else if(path.endsWith('/catalog'))value={tools:[]};else if(path.startsWith('/api/agent-builder/schedule_source'))value={selection:null};else if(/\/library(?:\?|$)/.test(path))value={space:{id:'personal',kind:'personal',name:'내 자료'},folder_id:null,folders:[],assets:[],next:null};else if(path.includes('/connection'))value={connection:{model:'qwen3:8b-q4_K_M',credential:{id:'local-model',provider:'openai',name:'Ollama · Qwen'}},health:{status:'ready',message:'Qwen 서버 응답 확인 · 모델 준비됨'}};else if(path.includes('/agent?'))value=snapshot(path.endsWith('B')?B:A);else throw Error('Unexpected API '+path);}
  return value instanceof Response?value:new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 }) as any;
 const root=createRoot(document.getElementById('root'));await act(async()=>{root.render(component||createElement(AgentStudio));await tick();});
 const flush=()=>act(async()=>{await tick();});
 return {root,calls,flush,close:async()=>{await act(async()=>{root.unmount();await new Promise(resolve=>setTimeout(resolve,0));});globalThis.fetch=originalFetch;}};
}
const buttons=()=>Array.from(document.querySelectorAll('button')) as HTMLButtonElement[];

test('incomplete external workflow explains missing settings in the conversation',async()=>{
 const h=await setup('?screen=workflow',{},path=>path==='/api/workflows/from-request'?{
  summary:'재고 흐름',questions:[{question:'어느 시트 범위를 읽을까요?'}],
  requirements:[{id:'range',description:'원본 시트 범위 지정',status:'needs_input'}],
 }:undefined);
 try{
  await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'Google Sheets 재고를 Slack으로 알려줘');
  await submit(document.querySelector<HTMLFormElement>('form.as-composer')!);
  const messages=Array.from(document.querySelectorAll('.as-messages .as-message'));
  assert.equal(messages.length,2);
  assert.match(messages[0].textContent||'',/Google Sheets 재고를 Slack으로 알려줘/);
  assert.match(messages[1].textContent||'',/원본 시트 범위 지정/);
  assert.match(messages[1].textContent||'',/어느 시트 범위를 읽을까요/);
  assert.equal(document.querySelector('.as-global-error'),null);
 }finally{await h.close();}
});
test('Gmail unread summary offers connected Slack channels and sends the chosen ID',async()=>{
 const question={id:'slack-question',key:'slack.channel_id',text:'요약을 보낼 Slack 채널 ID를 알려주세요. (예: C0123456789)',response:{type:'text'}};
 const prompt='gmail에서 안읽은 메일 3건 요약 정리해서 slack에 올려줘';
 let messageCount=0;
 const h=await setup('?screen=workflow',{},(path)=>{
  if(path==='/api/agent-builder/sessions')return {id:'gmail-slack-session',revision:0};
  if(path==='/api/agent-builder/message'){
   messageCount++;
   return {id:'gmail-slack-session',revision:messageCount,status:messageCount===1?'needs_clarification':'ready',
    messages:messageCount===1?[{role:'user',content:prompt},{role:'assistant',content:question.text}]:[{role:'user',content:prompt},{role:'user',content:'C0123456789'},{role:'assistant',content:'Slack 채널에 보냅니다.'}],
    pending_questions:messageCount===1?[question]:[],draft:null,issues:[],trace:[],resources:[]};
  }
  if(path==='/api/service-connections/slack/channels')return {channels:[{id:'C0123456789',name:'reports',isPrivate:false},{id:'G0123456789',name:'team-private',isPrivate:true}],truncated:false};
 });
 try{
  await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,prompt);
  await submit(document.querySelector<HTMLFormElement>('form.as-composer')!);
  await h.flush();
  assert.equal(h.calls.some(call=>call.path==='/api/workflows/from-request'),false);
  assert.ok(document.querySelector('form[aria-label="Slack 채널 선택"]'));
  const select=document.querySelector<HTMLSelectElement>('select[aria-label="전송할 Slack 채널"]')!;
  assert.ok(select);assert.match(select.textContent!,/#reports/);
  await act(async()=>{select.value='C0123456789';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});
  await click(button('이 채널로 설정'));
  const messages=h.calls.filter(call=>call.path==='/api/agent-builder/message');
  assert.equal(messages.length,2);assert.equal(messages[1].body.content,'C0123456789');assert.equal(messages[1].body.questionId,question.id);
 }finally{await h.close();}
});
test('an older Slack connection asks for reconnection while preserving manual channel entry',async()=>{
 let navigation='';
 const question={id:'slack-question',key:'slack.channel_id',text:'요약을 보낼 Slack 채널 ID를 알려주세요. (예: C0123456789)',response:{type:'text'}};
 const saved={id:'slack-session',revision:1,status:'needs_clarification',messages:[{role:'user',content:'Gmail 메일을 Slack에 요약해줘'},{role:'assistant',content:question.text}],pending_questions:[question],draft:null,issues:[],trace:[],resources:[]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':'slack-session'},path=>{
  if(path==='/api/agent-builder/session?id=slack-session')return saved;
  if(path==='/api/service-connections/slack/channels')return new Response(JSON.stringify({code:'slack_scope_required',error:'Slack 채널 목록 권한을 추가하려면 Slack 계정을 다시 연결해주세요.'}),{status:409,headers:{'Content-Type':'application/json'}});
 },createElement(AgentStudio,{onNavigate:(next:string)=>{navigation=next;}}));
 try{
  await h.flush();
  assert.match(document.querySelector('form[aria-label="Slack 채널 선택"]')?.textContent||'',/다시 연결/);
  await click(button('서비스 연결 열기'));assert.equal(navigation,'connections');
  await click(document.querySelector<HTMLInputElement>('.slack-channel-picker-manual input')!);
  assert.ok(document.querySelector('input[aria-label="Slack 채널 ID 직접 입력"]'));
 }finally{await h.close();}
});

for(const linked of [true,false])test('source evidence: '+(linked?'run-scoped snapshot original':'ordinary personal original'),async()=>{
 const fileId='source-alias',runId='historical-source-run';
 const current={id:runId,status:'succeeded',answer:'자료 요약',steps:[],rows:[],artifact:null,agent_version:1,
  evidence:[{file_name:'근거문서.pdf',file_id:fileId,page:2,quote:'원문 근거'}],
  library_source_files:linked?[{space:'team',team_id:'team',asset_id:'original-team-id',execution_asset_id:fileId,name:'근거문서.pdf',version:'v1'}]:[],
 };
 const h=await setup('?screen=run&service=A',{},path=>path.includes('/agent?')?snapshot(A,[current]):undefined,createElement(EntryApp));
 try{
  await click(buttons().find(item=>item.textContent?.includes('근거문서.pdf')&&item.textContent?.includes('원문 보기'))!);
  const link=document.querySelector<HTMLAnchorElement>('[role="dialog"] a');assert.ok(link);
  assert.equal(link.getAttribute('href'),linked?'/api/agent-builder/source_original?run_id='+runId+'&asset_id='+fileId+'#page=2':'/api/agent-builder/original?id='+fileId+'#page=2');
 }finally{await h.close();}
});
function capturePicker(write:(blob:Blob)=>void,onPick?:(options:any)=>void) {
 Object.defineProperty(dom.window,'showSaveFilePicker',{configurable:true,value:(options:any)=>{
  onPick?.(options);
  return Promise.resolve({createWritable:async()=>({write,close:async()=>{},abort:async()=>{}})});
 }});
}
function button(text:string){const b=buttons().find(b=>b.textContent?.trim()===text);assert.ok(b,'Missing button '+text);return b;}
async function click(b:Element){await act(async()=>{(b as HTMLElement).click();await tick();});}
function field(label:string){const l=Array.from(document.querySelectorAll('label')).find(l=>l.textContent?.startsWith(label));assert.ok(l,'Missing field '+label);const input=l.querySelector('input,textarea,select')||l.control;assert.ok(input);return input as HTMLInputElement;}
async function input(el:HTMLInputElement|HTMLTextAreaElement,value:string){await act(async()=>{const proto=el.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});}
async function submit(form:HTMLFormElement){await act(async()=>{form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();});}

test('library: navigation keeps every queued file until all uploads finish',async()=>{
 const first=deferred(),uploaded:string[]=[];
 const h=await setup('?screen=workflows',{'a4b-builder-session':'keep'},(path,body)=>{
  if(path==='/api/agent-builder/library_upload'){
   uploaded.push(body.fileName);
   return uploaded.length===1?first.promise:{asset:{id:body.fileName,name:body.fileName}};
  }
 },createElement(EntryApp));
 const waitFor=async(predicate:()=>boolean)=>{
  for(let i=0;i<100&&!predicate();i++)await act(async()=>{await new Promise(resolve=>setTimeout(resolve,5));});
  assert.ok(predicate(),'Expected upload/navigation to settle');
 };
 try{
  await click(button('자료함'));
  const picker=document.querySelector<HTMLInputElement>('input[aria-label="자료함에 올릴 파일"]')!;
  Object.defineProperty(picker,'files',{configurable:true,value:['one.txt','two.txt','three.txt'].map(name=>new File(['내용'],name,{type:'text/plain'}))});
  await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  await waitFor(()=>uploaded.length===1);
  assert.equal(document.querySelector('.workspace-header [data-tour="create-workflow"]'),null);
  for(const target of [button('워크플로우')]){
   await click(target);
   assert.equal(location.search,'?screen=assets');
   assert.equal(document.querySelector('h1')?.textContent,'자료함');
   assert.equal(localStorage.getItem('a4b-builder-session'),'keep');
  }
  await act(async()=>{window.history.back();await new Promise(resolve=>setTimeout(resolve,30));});
  await waitFor(()=>location.search==='?screen=assets');
  assert.equal(document.querySelector('h1')?.textContent,'자료함');
  assert.deepEqual(uploaded,['one.txt']);
  const unload=new dom.window.Event('beforeunload',{cancelable:true});
  window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  assert.match(document.body.textContent!,/모든 파일의 업로드가 끝난 뒤/);
  await act(async()=>{first.resolve({asset:{id:'one.txt',name:'one.txt'}});await tick();});
  await waitFor(()=>uploaded.length===3&&document.body.textContent!.includes('3개 파일을 자료함에 올렸습니다.'));
  assert.deepEqual(uploaded,['one.txt','two.txt','three.txt']);
  const finished=new dom.window.Event('beforeunload',{cancelable:true});
  window.dispatchEvent(finished);assert.equal(finished.defaultPrevented,false);
  await click(button('워크플로우'));
  assert.equal(location.search,'?screen=workflow');
  await click(button('서비스 연결'));
  assert.equal(location.search,'?screen=connections');
 }finally{first.resolve({asset:{id:'one.txt',name:'one.txt'}});await h.close();}
});

test('DOM: service link overrides old session and renders the requested execution screen',async()=>{
 const h=await setup('?service=A',{'a4b-builder-session':'old'});try{assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.ok(document.querySelector('.screen-run'));assert.equal(localStorage.getItem('a4b-builder-session'),null);assert.equal(h.calls.some(c=>c.path.includes('/session?')),false);}finally{await h.close();}
});
test('DOM: a new Gmail connection closes the dialog while an existing connection stays manageable',async()=>{
 let connectionVersion=0;
 const endpoint='/api/agent-builder/gmail';
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!==endpoint)return undefined;
  if(method==='POST')return {authorizationUrl:'https://accounts.google.com/o/oauth2/v2/auth',connectionVersion,expiresAt:Date.now()+60000};
  return {configured:true,connected:true,connectionVersion,email:connectionVersion?'new-account@example.com':'connected-account@example.com'};
 },createElement(EntryApp));
 try{
  dom.window.open=()=>null;
  await click(button('연결 관리'));
  assert.match(document.querySelector('.service-connection-account')!.textContent!,/connected-account@example.com/);
  assert.match(document.querySelector('[role="dialog"]')!.textContent!,/connected-account@example.com/);
  assert.ok(button('연결 확인'));
  await click(button('계정 다시 연결'));
  assert.ok(document.querySelector('a[href="https://accounts.google.com/o/oauth2/v2/auth"]'));
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,1250));});
  assert.ok(button('연결 확인'));
  connectionVersion++;
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,1250));});
  assert.equal(document.querySelector('[role="dialog"]'),null);
  assert.match(document.querySelector('.service-connection-account')!.textContent!,/new-account@example.com/);
  assert.doesNotMatch(document.querySelector('.service-connection-account')!.textContent!,/connected-account@example.com/);
  assert.equal(location.search,'?screen=connections');
 }finally{await h.close();}
});
test('connections: the shared sidebar opens Gmail management and disconnect refreshes the card',async()=>{
 let connected=true;
 const h=await setup('?screen=workflows',{},(path,_body,method)=>{
  if(path==='/api/agent-builder/gmail'){
   if(method==='DELETE')connected=false;
   return {configured:true,connected,connectionVersion:1,email:connected?'team@example.com':null};
  }
 },createElement(EntryApp));
 try{
  assert.equal(document.querySelector('.workspace-header')?.textContent?.includes('Gmail'),false);
  await click(button('서비스 연결'));
  assert.equal(location.search,'?screen=connections');
  assert.equal(document.querySelector('h1')?.textContent,'서비스 연결');
  assert.match(document.querySelector('.service-connection-state')!.textContent!,/연결됨/);
  assert.match(document.querySelector('.service-connection-account')!.textContent!,/team@example.com/);
  await click(button('연결 관리'));
  assert.match(document.querySelector('.gmail-connected-account')!.textContent!,/team@example.com/);
  assert.equal(h.calls.some(call=>call.path.endsWith('/gmail/test')),false);
  await click(button('A4B 연결 해제'));
  assert.match(document.querySelector('.service-connection-state')!.textContent!,/연결 안 됨/);
  assert.ok(button('Google에서 연결'));
  assert.equal(document.querySelector('.service-connection-account'),null);
  assert.doesNotMatch(document.body.textContent!,/team@example.com/);
  assert.equal(h.calls.some(call=>call.path.endsWith('/test')),false);
 }finally{await h.close();}
});

test('connections: a slow account refresh cannot restore the address after disconnect',async()=>{
 const waiting=deferred();let reads=0;
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='DELETE')return {connected:false};
  if(++reads===2)return waiting.promise;
  return {configured:true,connected:true,connectionVersion:1,email:'previous-account@example.com'};
 },createElement(EntryApp));
 try{
  await click(button('연결 관리'));
  await click(button('A4B 연결 해제'));
  await act(async()=>{waiting.resolve({configured:true,connected:true,connectionVersion:1,email:'previous-account@example.com'});await tick();});
  assert.equal(document.querySelector('.service-connection-account'),null);
  assert.doesNotMatch(document.body.textContent!,/previous-account@example.com/);
  assert.ok(button('Google에서 연결'));
 }finally{await h.close();}
});

test('connections: an unconnected Gmail card starts Google authorization directly',async()=>{
 let begins=0;
 const authorizationUrl='https://accounts.google.com/o/oauth2/v2/auth?scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly';
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='POST'){begins++;return {authorizationUrl,connectionVersion:0,expiresAt:Date.now()+60000};}
  return {configured:true,connected:false,connectionVersion:0,email:null};
 },createElement(EntryApp));
 try{
  dom.window.open=()=>null;
  assert.equal(document.querySelector('[role="dialog"]'),null);
  const connect=document.querySelector<HTMLButtonElement>('.service-connection-card [aria-label="Gmail 계정 연결"]')!;
  assert.equal(connect.textContent,'Google에서 연결');assert.equal(connect.disabled,false);
  await click(connect);
  assert.ok(document.querySelector('[role="dialog"]'));
  assert.equal(document.querySelector('[role="dialog"] a')?.getAttribute('href'),authorizationUrl);
  assert.equal(begins,1);assert.equal(location.search,'?screen=connections');
 }finally{await h.close();}
});

test('connections: OAuth success replaces a pending pre-save Gmail status response',async()=>{
 const waiting=deferred();let reads=0,connected=false;
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='POST')return {authorizationUrl:'https://accounts.google.com/o/oauth2/v2/auth',connectionVersion:0,expiresAt:Date.now()+60000};
  if(++reads===2)return waiting.promise;
  return {configured:true,connected,connectionVersion:connected?1:0,email:connected?'new@example.com':null};
 },createElement(EntryApp));
 try{
  const popup={close(){},location:{replace(){}}} as unknown as Window;
  dom.window.open=()=>popup;
  await click(document.querySelector('.service-connection-card [aria-label="Gmail 계정 연결"]')!);
  assert.equal(reads,2);
  connected=true;
  await act(async()=>{
   const event=new dom.window.MessageEvent('message',{origin:location.origin,data:{type:'easyflow-oauth',ok:true}});
   Object.defineProperty(event,'source',{value:popup});window.dispatchEvent(event);await tick();
   waiting.resolve({configured:true,connected:false,connectionVersion:0,email:null});await tick();
  });
  assert.equal(document.querySelector('[role="dialog"]'),null);
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결됨');
  assert.match(document.querySelector('.service-connection-account')!.textContent!,/new@example.com/);
 }finally{waiting.resolve({configured:true,connected:false,connectionVersion:0,email:null});await h.close();}
});

test('connections: disconnect wins over a late reconnect popup success',async()=>{
 const waiting=deferred();let connected=true,closes=0;
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='POST')return {authorizationUrl:'https://accounts.google.com/o/oauth2/v2/auth',connectionVersion:1,expiresAt:Date.now()+60000};
  if(method==='DELETE')return waiting.promise;
  return {configured:true,connected,connectionVersion:1,email:connected?'old@example.com':null};
 },createElement(EntryApp));
 try{
  const popup={close(){closes++;},location:{replace(){}}} as unknown as Window;
  dom.window.open=()=>popup;
  await click(document.querySelector('.service-connection-card [aria-label="Gmail 연결 관리"]')!);
  await click(button('계정 다시 연결'));
  await click(button('A4B 연결 해제'));
  await act(async()=>{
   const event=new dom.window.MessageEvent('message',{origin:location.origin,data:{type:'easyflow-oauth',ok:true}});
   Object.defineProperty(event,'source',{value:popup});window.dispatchEvent(event);await tick();
   connected=false;waiting.resolve({connected:false});await tick();
  });
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결 안 됨');
  assert.equal(document.querySelector('.service-connection-account'),null);
  assert.doesNotMatch(document.body.textContent!,/old@example.com/);
  assert.ok(closes>0,'disconnect closes the previous authorization popup');
  assert.equal(sessionStorage.getItem('a4b-gmail-return'),null);
 }finally{connected=false;waiting.resolve({connected:false});await h.close();}
});

test('connections: a connected Gmail card shows one management action with verification and disconnect inside',async()=>{
 let connected=true;
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path==='/api/agent-builder/gmail/test')return {email:'verified@example.com'};
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='DELETE')connected=false;
  return {configured:true,connected,connectionVersion:1,email:connected?'saved@example.com':null};
 },createElement(EntryApp));
 try{
  assert.match(document.querySelector('.service-connection-account')!.textContent!,/saved@example.com/);
  assert.deepEqual(Array.from(document.querySelectorAll('.service-connection-card:first-child .service-connection-actions button')).map(item=>item.textContent?.trim()),['연결 관리']);
  await click(document.querySelector('.service-connection-card [aria-label="Gmail 연결 관리"]')!);
  assert.ok(document.querySelector('[role="dialog"]'));
  assert.ok(button('계정 다시 연결'));
  await click(button('연결 확인'));
  assert.match(document.querySelector('.service-connection-card')!.textContent!,/verified@example.com/);
  assert.match(document.querySelector('[role="dialog"]')!.textContent!,/Gmail 읽기 권한을 확인/);
  await click(button('A4B 연결 해제'));
  assert.equal(document.querySelector('.service-connection-account'),null);
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결 안 됨');
  assert.ok(document.querySelector('.service-connection-card [aria-label="Gmail 계정 연결"]'));
 }finally{await h.close();}
});

test('connections: Sheets and Slack show account identity with actions inside management',async()=>{
 let sheetsConnected=true;
 const h=await setup('?screen=connections',{},(path,body,method)=>{
  if(path==='/api/agent-builder/gmail')return {configured:true,connected:false,connectionVersion:0,email:null};
  if(path==='/api/service-connections')return {
   services:{googleSheets:{configured:true},slack:{configured:true}},
   credentials:[
    ...(sheetsConnected?[{id:'sheet-1',provider:'google',service:'googleSheets',name:'Google Sheets',updatedAt:'2026-11-13T00:00:00Z'}]:[]),
    {id:'slack-1',provider:'slack',service:'slack',name:'Slack',accountLabel:'skn33',updatedAt:'2026-11-13T00:00:00Z'},
   ],
  };
  if(path==='/api/credentials/test'){assert.deepEqual(body,{id:'sheet-1'});return {message:'연결이 정상입니다.'};}
  if(path==='/api/credentials?id=sheet-1'&&method==='DELETE'){sheetsConnected=false;return {};}
 },createElement(EntryApp));
 try{
  const sheet=document.querySelector('article[aria-label="Google Sheets 서비스"]')!;
  const slack=document.querySelector('article[aria-label="Slack 서비스"]')!;
  assert.match(sheet.querySelector('.service-connection-account')!.textContent!,/계정 이메일 확인 필요/);
  assert.doesNotMatch(sheet.querySelector('.service-connection-account')!.textContent!,/Google Sheets/);
  assert.match(slack.querySelector('.service-connection-account')!.textContent!,/skn33/);
  assert.deepEqual(Array.from(sheet.querySelectorAll('.service-connection-actions button')).map(item=>item.textContent?.trim()),['연결 관리']);
  assert.deepEqual(Array.from(slack.querySelectorAll('.service-connection-actions button')).map(item=>item.textContent?.trim()),['연결 관리']);
  await click(sheet.querySelector('.service-connection-actions button')!);
  assert.match(document.querySelector('[role="dialog"]')!.textContent!,/다시 연결하면 연결된 Google 계정의 이메일을 표시/);
  await click(button('연결 확인'));
  assert.match(document.querySelector('[role="dialog"]')!.textContent!,/연결이 정상입니다/);
  await click(button('A4B 연결 해제'));
  assert.equal(sheet.querySelector('.service-connection-account'),null);
  assert.equal(sheet.querySelector('.service-connection-actions button')?.textContent,'Google에서 연결');
  await click(slack.querySelector('.service-connection-actions button')!);
  assert.match(document.querySelector('[role="dialog"]')!.textContent!,/skn33/);
  assert.ok(button('계정 다시 연결'));
 }finally{await h.close();}
});

test('connections: a late Sheets status refresh cannot restore a disconnected account',async()=>{
 const stale=deferred(),removed=deferred();let reads=0,connected=true;
 const status=()=>({services:{googleSheets:{configured:true},slack:{configured:true}},credentials:connected?[{id:'sheet-1',provider:'google',service:'googleSheets',name:'Google Sheets',accountLabel:'old@example.com',updatedAt:'2026-11-13T00:00:00Z'}]:[]});
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path==='/api/agent-builder/gmail')return {configured:true,connected:false,connectionVersion:0,email:null};
  if(path==='/api/service-connections')return ++reads===2?stale.promise:status();
  if(path==='/api/credentials?id=sheet-1'&&method==='DELETE')return removed.promise;
 },createElement(EntryApp));
 try{
  const sheet=document.querySelector('article[aria-label="Google Sheets 서비스"]')!;
  assert.match(sheet.textContent!,/old@example.com/);
  await click(sheet.querySelector('.service-connection-actions button')!);
  await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});
  assert.equal(reads,2);
  await click(button('A4B 연결 해제'));
  await act(async()=>{connected=false;removed.resolve({});await tick();});
  assert.equal(sheet.querySelector('.service-connection-account'),null);
  await act(async()=>{stale.resolve({services:{googleSheets:{configured:true},slack:{configured:true}},credentials:[{id:'sheet-1',provider:'google',service:'googleSheets',name:'Google Sheets',accountLabel:'old@example.com',updatedAt:'2026-11-13T00:00:00Z'}]});await tick();});
  assert.equal(sheet.querySelector('.service-connection-account'),null);
  assert.equal(sheet.querySelector('.service-connection-actions button')?.textContent,'Google에서 연결');
 }finally{removed.resolve({});stale.resolve(status());await h.close();}
});

test('connections: returning to a visible window refreshes a stale Gmail card once',async()=>{
 const waiting=deferred();let reads=0;
 const h=await setup('?screen=connections',{},path=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(++reads===2)return waiting.promise;
  return {configured:true,connected:true,connectionVersion:1,email:'previous@example.com'};
 },createElement(EntryApp));
 try{
  assert.equal(reads,1);
  await act(async()=>{
   Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});
   document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('focus'));await tick();
  });
  assert.equal(reads,1,'a hidden page does not fetch the account');
  await act(async()=>{
   Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
   window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));await tick();
  });
  assert.equal(reads,2,'focus and visibility share the in-flight status request');
  await act(async()=>{waiting.resolve({configured:true,connected:false,connectionVersion:1,email:null});await tick();});
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결 안 됨');
  assert.doesNotMatch(document.body.textContent!,/previous@example.com/);
 }finally{waiting.resolve({configured:true,connected:false,connectionVersion:1,email:null});await h.close();}
});

test('connections: focus refresh cannot race an explicit Gmail disconnect',async()=>{
 const waiting=deferred();let reads=0;
 const h=await setup('?screen=connections',{},(path,_body,method)=>{
  if(path!=='/api/agent-builder/gmail')return undefined;
  if(method==='DELETE')return waiting.promise;
  reads++;return {configured:true,connected:true,connectionVersion:1,email:'old@example.com'};
 },createElement(EntryApp));
 try{
  await click(document.querySelector('.service-connection-card [aria-label="Gmail 연결 관리"]')!);
  const readsBeforeDisconnect=reads;
  await click(button('A4B 연결 해제'));
  await act(async()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));await tick();});
  assert.equal(reads,readsBeforeDisconnect);
  await act(async()=>{waiting.resolve({connected:false});await tick();});
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결 안 됨');
  assert.doesNotMatch(document.body.textContent!,/old@example.com/);
 }finally{waiting.resolve({connected:false});await h.close();}
});

test('connections: a saved Gmail account with an API error asks for verification and reconnect',async()=>{
 const message='연결된 Gmail 주소를 확인하지 못했어요. 상태를 새로고침하거나 계정을 다시 연결해주세요.';
 const h=await setup('?screen=connections',{},path=>path==='/api/agent-builder/gmail'?{configured:true,connected:true,connectionVersion:1,email:null,accountError:message}:undefined,createElement(EntryApp));
 try{
  assert.equal(document.querySelector('.service-connection-state')!.textContent,'연결 확인 필요');
  assert.equal(document.querySelector('.service-connection-state')!.classList.contains('connected'),false);
  assert.match(document.querySelector('.service-connection-card')!.textContent!,/상태를 새로고침하거나 계정을 다시 연결/);
  assert.equal(document.querySelector<HTMLButtonElement>('.service-connection-card [aria-label="Gmail 연결 관리"]')!.disabled,false);
  await click(document.querySelector('.service-connection-card [aria-label="Gmail 연결 관리"]')!);
  assert.ok(button('계정 다시 연결'));
  assert.equal(h.calls.some(call=>call.body),false,'checking an unhealthy account must not remove or reconnect it');
 }finally{await h.close();}
});

test('rename: saving updates the card, version and search without changing other workflows',async()=>{
 const renamed={...A,version:2,config:{...A.config,name:'월간 매출 정리'}};
 const h=await setup('?screen=workflows',{},(path,body)=>{
  if(path==='/api/agent-builder/rename_agent'){
   assert.deepEqual(body,{id:'A',version:1,name:'월간 매출 정리'});
   return {agent:renamed};
  }
 },createElement(EntryApp));
 try{
  await click(document.querySelector('button[aria-label="서비스 A 이름 변경"]')!);
  await input(field('워크플로우 이름'),'  월간 매출 정리  ');
  await click(button('이름 저장'));
  assert.equal(document.querySelector('[role=dialog]'),null);
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,2);
  assert.ok(document.querySelector('button[aria-label="월간 매출 정리 실행하기"]'));
  assert.match(document.querySelector('.entry-workflow-card .as-status')!.textContent!,/2/);
  await input(document.querySelector('input[aria-label="워크플로우 검색"]') as HTMLInputElement,'월간');
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,1);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/rename_agent')).length,1);
 }finally{await h.close();}
});

test('rename: cancel and version conflicts preserve the existing name and allow recovery',async()=>{
 const h=await setup('?screen=workflows',{},path=>{
  if(path==='/api/agent-builder/rename_agent')return new Response(JSON.stringify({error:'다른 곳에서 워크플로우가 수정됐어요. 목록을 새로고침하고 다시 시도해주세요.'}),{status:400});
 },createElement(EntryApp));
 try{
  await click(document.querySelector('button[aria-label="서비스 A 이름 변경"]')!);
  await input(field('워크플로우 이름'),'취소할 이름');await click(button('취소'));
  assert.equal(h.calls.some(call=>call.path.endsWith('/rename_agent')),false);
  await click(document.querySelector('button[aria-label="서비스 A 이름 변경"]')!);
  assert.equal(field('워크플로우 이름').value,'서비스 A');
  await input(field('워크플로우 이름'),'   ');assert.equal(button('이름 저장').disabled,true);
  await input(field('워크플로우 이름'),'새 이름');await click(button('이름 저장'));
  assert.match(document.querySelector('[role=alert]')!.textContent!,/새로고침/);
  assert.ok(document.querySelector('button[aria-label="서비스 A 이름 변경"]'));
  assert.equal(button('이름 저장').disabled,false);
 }finally{await h.close();}
});

test('DOM: historical XLSX chooses its destination before fetching the authenticated report and preserves unsent input',async()=>{
 const previous={id:'previous',status:'succeeded',answer:'완료',steps:[],config_snapshot:{name:'영수증 정리'},
  rows:[{field:'결제 금액',value:null,status:'not_found_or_unverified'}],report_path:'reports/previous.xlsx',
  artifact:{name:'0123456789abcdef0123456789abcdef.xlsx',mime:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',content:'STALE'}};
 const bytes=Uint8Array.from([80,75,3,4,255]);
 const h=await setup('?service=A&run=previous',{},path=>{
  if(path.includes('/agent?'))return snapshot(A,[{...previous,id:'latest'},previous]);
  if(path==='/api/agent-builder/report?id=previous')return new Response(bytes,{headers:{'Content-Type':previous.artifact.mime}});
 });
 try{
  await input(document.querySelector('textarea[data-tour="run-input"]') as HTMLTextAreaElement,'보존할 입력');
  let saved:Blob|undefined;
  capturePicker(blob=>{saved=blob;},options=>{
   assert.equal(options.suggestedName,'영수증 정리 결과.xlsx');assert.deepEqual(options.types[0].accept,{[previous.artifact.mime]:['.xlsx']});
   assert.equal(h.calls.some(c=>c.path.includes('/report?')),false);
  });
  await click(button('엑셀 파일 다운로드'));
  assert.ok(document.body.textContent?.includes('원문에서 확인하지 못해 빈칸으로 둔 항목: 결제 금액'));
  assert.ok(saved);assert.deepEqual(new Uint8Array(await saved.arrayBuffer()),bytes);
  assert.equal(location.search,'?service=A&run=previous');assert.equal((document.querySelector('textarea[data-tour="run-input"]') as HTMLTextAreaElement).value,'보존할 입력');
  Object.defineProperty(dom.window,'showSaveFilePicker',{value:async()=>{throw new dom.window.DOMException('cancel','AbortError');}});
  await click(button('엑셀 파일 다운로드'));
  assert.equal(h.calls.filter(c=>c.path.includes('/report?')).length,1);
  assert.equal(document.querySelector('[role=alert]'),null);
 }finally{await h.close();}
});
test('entry: a workflow card opens only its execution history',async()=>{
 const h=await setup('?screen=workflows',{},path=>{
  if(path.includes('/agent?')){
   const agent=path.includes('id=A')?A:B;
   return snapshot(agent,[{id:'run-'+agent.id,status:'succeeded',agent_version:1}]);
  }
 },createElement(EntryApp));
 try{
  await click(document.querySelector('[aria-label="서비스 A 실행 기록"]')!);
  await h.flush();
  assert.equal(new URL(location.href).searchParams.get('service'),'A');
  assert.deepEqual(Array.from(document.querySelectorAll('.entry-history-list h2'),h=>h.textContent),['서비스 A']);
  assert.ok(!h.calls.some(c=>c.path.includes('/agent?id=B')));
  await click(button('전체 실행 기록'));
  await h.flush();
  assert.equal(document.querySelectorAll('.entry-history-list article').length,2);
 }finally{await h.close();}
});

test('DOM: run history draws the saved version and design opens at reading size in either direction',async()=>{
 const flow=(label:string)=>({entry:'read',nodes:[
  {id:'read',tool:'read_document',label,settings:{},bindings:[]},
  {id:'out',tool:'create_spreadsheet',label:'엑셀 만들기',settings:{},bindings:[]}
 ],edges:[{source:'read',target:'out',branch:'next'}]});
 const current={...A,version:2,config:{...A.config,workflow:flow('새 자료 읽기')}};
 const previous={id:'previous',status:'succeeded',agent_version:1,answer:'확인할 항목이 하나 있어요.',rows:[],
  config_snapshot:{workflow:flow('이전 영수증 읽기')},steps:[{node:'read',status:'completed',label:'읽기'}]};
 const h=await setup('?service=A',{},path=>path.includes('/agent?')?snapshot(current,[previous]):undefined);
 try{
  const diagram=document.querySelector('.as-test-result [aria-label="워크플로우 흐름"]');
  assert.ok(diagram);
  assert.ok(diagram.textContent.includes('이전 영수증 읽기'));
  assert.ok(!diagram.textContent.includes('새 자료 읽기'));
  assert.ok(document.querySelector('.as-test-result')?.textContent?.includes(previous.answer));
  await click(button('설계'));
  const cards=()=>Array.from(document.querySelectorAll<HTMLElement>('.as-flow-canvas .as-flow-node'));
  assert.equal(cards()[0].style.left,cards()[1].style.left);
  assert.notEqual(cards()[0].style.top,cards()[1].style.top);
  assert.equal(document.querySelector('.as-zoom span')?.textContent,'100%');
  await click(button('가로로 보기'));
  assert.equal(cards()[0].style.top,cards()[1].style.top);
  assert.notEqual(cards()[0].style.left,cards()[1].style.left);
  await click(cards()[1]);
  assert.ok(document.querySelector('.as-node-inspector')?.textContent?.includes('엑셀 만들기'));
 }finally{await h.close();}
});
test('DOM: saved Gmail→Slack run uses its historical workflow to render mail cards',async()=>{
 const gmailSlack={entry:'mail',nodes:[
  {id:'mail',tool:'email.list',label:'안 읽은 메일 읽기',bindings:[],settings:{operation:'read_messages',arguments_json:'{"max_results":3,"query":"is:unread"}'}},
  {id:'summary',tool:'compose_answer',label:'메일 요약',bindings:[{input:'documents',node_id:'mail',output:'documents'}],settings:{}},
  {id:'post',tool:'slack.post_message',label:'Slack 전송',bindings:[{input:'text',node_id:'summary',output:'text'}],settings:{operation:'execute',arguments_json:'{"channel_id":"C0123456789"}'}},
 ],edges:[{source:'mail',target:'summary',branch:'next'},{source:'summary',target:'post',branch:'next'}]};
 const updated={...A,version:2,config:{...A.config,workflow:{entry:'other',nodes:[{id:'other',tool:'read_document',label:'다른 흐름',bindings:[],settings:{}}],edges:[]}}};
 const answer='조회한 안 읽은 메일 요약:\n\n제목: Slack 평가판 안내\n보낸 사람: Slack <no-reply@slack.com>\n평가판이 시작되었습니다.\n\n{"status":"accepted_by_provider","channel_id":"C0123456789","ts":"123"}';
 const previous={id:'gmail-slack-run',status:'succeeded',agent_version:1,answer,rows:[],steps:[],evidence:[],config_snapshot:{workflow:gmailSlack}};
 const h=await setup('?service=A',{},path=>path.includes('/agent?')?snapshot(updated,[previous]):undefined);
 try{
  assert.equal(document.querySelectorAll('.as-test-result .gmr-card').length,1);
  assert.equal(document.querySelector('.gmr-card h3')?.textContent,'Slack 평가판 안내');
  assert.equal(document.querySelector('.gmr-card-heading p')?.textContent,'Slack <no-reply@slack.com>');
  assert.doesNotMatch(document.querySelector('.gmr-card-content')!.textContent!,/channel_id|accepted_by_provider/);
  assert.equal(document.querySelector('.gmr-full')?.hasAttribute('open'),false);
  assert.equal(document.querySelector('.gmr-full p')?.textContent,answer.split('\n\n{')[0]);
  assert.doesNotMatch(document.querySelector('.as-test-result')!.textContent!,/accepted_by_provider|channel_id/);
 }finally{await h.close();}
});
test('DOM: fallback save lets users rename an inline CSV and downloads identical bytes without leaving the page',async()=>{
 const csv='\ufeff이름,값\r\n가,1';
 const run={id:'csv',status:'succeeded',answer:'완료',steps:[],rows:[],
  artifact:{name:'추출_결과.csv',mime:'text/csv',encoding:'base64',content:Buffer.from(csv).toString('base64')}};
 const h=await setup('?service=A',{},path=>path.includes('/agent?')?snapshot(A,[run]):undefined);
 try{
  const links:{url:string;name:string;target:string}[]=[];
  dom.window.HTMLAnchorElement.prototype.click=function(){links.push({url:this.href,name:this.download,target:this.target});};
  await click(button('CSV 파일 다운로드'));
  assert.equal(field('파일 이름').value,'추출_결과');await input(field('파일 이름'),'11월 영수증.csv');
  await click(button('저장'));
  assert.equal(links.length,1);assert.equal(links[0].name,'11월 영수증.csv');assert.equal(links[0].target,'_blank');
  assert.deepEqual(Buffer.from(await (await originalFetch(links[0].url)).arrayBuffer()),Buffer.from(csv));
  assert.equal(document.querySelector('[role=dialog]'),null);assert.equal(location.search,'?service=A');
 }finally{await h.close();}
});
for(const deleted of [false,true]) test(`DOM: a saved receipt workflow starts with just a file (${deleted?'deleted sample':'missing input'})`,async()=>{
 const receipt={...A,config:{...A.config,document_ids:deleted?['deleted-sample']:[],tool_ids:['read_document','extract_information','create_spreadsheet']}};
 const guidance=deleted?'기존 영수증 원본이 삭제됐습니다. 새 파일을 연결해 실행하세요.':'실행할 문서를 연결하세요.';
 const requirement={kind:'document',fields:[],required_before_start:true,ready:false,issues:[guidance]};
 const h=await setup('?service=A',{},path=>{
  if(path.includes('/agent?'))return {...snapshot(receipt),request_required:false,input_requirements:[requirement]};
  if(path.endsWith('/run_upload'))return {asset:{id:'receipt-file',name:'영수증.pdf',kind:'document'}};
  if(path.endsWith('/test'))return {id:'receipt-run',status:'succeeded',answer:'완료',steps:[],rows:[]};
 });
 try {
  const run=button('워크플로우 실행');
  assert.ok(field('추가 요청 (선택)'));assert.equal(run.disabled,true);
  assert.ok(document.querySelector('.as-run-files')?.textContent?.includes(guidance));
  const picker=document.querySelectorAll<HTMLInputElement>('input[type=file]')[2];
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['receipt'],'영수증.pdf')]});
  await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  const deadline=Date.now()+3000;
  while(run.disabled){assert.ok(Date.now()<deadline,'File did not enable Run');await h.flush();}
  assert.equal(h.calls.filter(c=>c.path.endsWith('/test')).length,0);
  await click(run);
  assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/test')).map(c=>c.body),
   [{id:'A',content:'',fileIds:['receipt-file']}]);
 }finally{await h.close();}
});
test('DOM: saved documents satisfy saved extraction inputs; an unbound inquiry or confirmation still needs an answer',async()=>{
 const requirement={kind:'document',fields:[],required_before_start:true,ready:true};
 const stored={...snapshot(A),request_required:false,input_requirements:[requirement]};
 const optional=await setup('?service=A',{},path=>path.includes('/agent?')?stored:undefined);
 try{assert.equal(button('워크플로우 실행').disabled,false);}finally{await optional.close();}
 const mixed={...stored,agent:{...A,config:{...A.config,tool_ids:['read_document','reply_review']}},
  request_required:true};
 const review=await setup('?service=A',{},path=>path.includes('/agent?')?mixed:undefined);
 try{
  assert.equal(button('워크플로우 실행').disabled,true);
  await input(field('고객 문의'),'환불이 가능한가요?');assert.equal(button('워크플로우 실행').disabled,false);
 }finally{await review.close();}
 const waiting={...stored,tests:[{id:'interrupted',status:'needs_input',answer:'승인할까요?',question:{question:'승인할까요?'},steps:[],rows:[]}]};
 const resume=await setup('?service=A',{},path=>path.includes('/agent?')?waiting:undefined);
 try{
  assert.equal(button('답변하고 이어 실행').disabled,true);
  await input(field('확인 질문에 답변'),'네');assert.equal(button('답변하고 이어 실행').disabled,false);
 }finally{await resume.close();}
});
test('DOM: an incompatible replacement blocks a receipt run until removed; a file with document capability works',async()=>{
 const saved={id:'original',kind:'document',name:'원래 영수증.pdf'};
 const requirement={kind:'document',required_before_start:true,ready:true,fields:[],file_ids:[saved.id]};
 const h=await setup('?service=A',{},(path,body)=>{
  if(path.includes('/agent?'))return {...snapshot(A),request_required:false,resources:[saved],input_requirements:[requirement]};
  if(path.endsWith('/run_upload'))return {asset:{id:body.fileName,kind:'table',name:body.fileName,
   capabilities:body.fileName==='분석된 표.xlsx'?['table','document']:['table']}};
 });
 try{
  const run=button('워크플로우 실행');
  const picker=document.querySelectorAll<HTMLInputElement>('input[type=file]')[2];
  async function attach(name:string) {
   Object.defineProperty(picker,'files',{configurable:true,value:[new dom.window.File(['table'],name)]});
   await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
   const deadline=Date.now()+3000;
   while(!document.querySelector('[aria-label="'+name+' 제거"]')){assert.ok(Date.now()<deadline);await h.flush();}
  }
  assert.match(document.querySelector('.as-run-files small')?.textContent||'',/원래 영수증.pdf/);
  await attach('표.csv');assert.equal(run.disabled,true);
  assert.match(document.querySelector('.as-run-files small')?.textContent||'',/문서를 첨부/);
  await click(document.querySelector('[aria-label="표.csv 제거"]')!);assert.equal(run.disabled,false);
  await attach('분석된 표.xlsx');assert.equal(run.disabled,false);
  assert.equal(h.calls.some(c=>c.path.endsWith('/test')),false);
 }finally{await h.close();}
});
test('DOM: design attachment offers a new upload or an existing file by id',async()=>{
 const asset={id:'mine',name:'기준표.csv',kind:'table',format:'csv',row_count:1,columns:['가격'],profile:{},preview:[]};
 const inspection={id:asset.id,name:asset.name,kind:'table',has_readable_text:true,columns:['가격'],fields:[],snippets:[],preview:[{가격:1300}]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return readySession();
  if(path.endsWith('/assets'))return {assets:[],next:'12'};
  if(path.endsWith('/assets?before=12'))return {assets:[asset],next:null};
  if(path.endsWith('/attach_asset'))return {...readySession(),revision:2,resources:[asset],file_inspection:[inspection]};
 });
 try{
  await click(button('자료 연결'));
  assert.ok(button('새 파일 올리기'));
  await click(button('자료함에서 선택'));
  await click(button('자료 더 보기'));
  await click(document.querySelector('[role=dialog] .as-resource')!);
  assert.deepEqual(h.calls.find(c=>c.path.endsWith('/attach_asset'))?.body,{id:'ready',revision:1,assetId:'mine'});
  assert.equal(h.calls.some(c=>c.path.endsWith('/upload')),false);
  assert.equal(document.querySelector('[role=dialog]'),null);
  assert.match(document.querySelector('.as-config')?.textContent||'',/기준표.csv/);
  const card=document.querySelector('details.as-inspected-source') as HTMLDetailsElement;
  assert.equal(card.open,false);await click(card.querySelector('summary')!);
  assert.equal(card.querySelector('td')?.textContent,'1300');
  const composer=document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement;
  await input(composer,'지난달 자료만 사용해 주세요.');await click(field('가격'));await click(button('요청에 추가'));
  assert.equal(composer.value,'지난달 자료만 사용해 주세요.\n기준표.csv에서 가격 항목을 추출해 주세요.');
  assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
 }finally{await h.close();}
});
test('DOM: a fresh file-first design opens real document excerpts and explains unreadable files on restore',async()=>{
 const files=[
  {id:'slides',name:'소개.pptx',kind:'document',has_readable_text:true,columns:[],fields:[{label:'담당자',value:'김담당'}],snippets:['담당자: 김담당'],preview:[]},
  {id:'photo',name:'흐린 사진.jpg',kind:'document',has_readable_text:false,partial:true,columns:[],fields:[],snippets:[],preview:[]},
 ];
 const text='담당자: 김담당\n'+'긴 본문 '.repeat(3000)+'마지막 문단';
 const session={...readySession(),messages:[],draft:null,status:'empty',file_inspection:files,
  resources:[{id:'slides',name:'소개.pptx',kind:'document',text}]};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>path.includes('/session?')?session:undefined);
 try{
  const cards=Array.from(document.querySelectorAll<HTMLDetailsElement>('details.as-inspected-source'));
  assert.deepEqual(cards.map(card=>card.open),[true,true]);assert.ok(!document.querySelector('.as-welcome'));
  assert.equal(cards[0].querySelector('blockquote')?.textContent,'담당자: 김담당');
  assert.match(cards[1].textContent||'',/읽어 낸 글자가 없습니다/);
  assert.equal(cards[1].querySelector('input[type=checkbox]'),null);
  await click(button('읽은 내용 전체 보기'));
  assert.equal(document.querySelector('.as-resource-dialog .as-preserve')?.textContent,text);
 }finally{await h.close();}
});
for(const drop of [false,true])test('DOM: first attachment shows file values before chat ('+(drop?'drop':'picker')+')',async()=>{
 const file={id:'uploaded',name:'접수.txt',kind:'document',has_readable_text:true,columns:[],fields:[{label:'접수번호',value:'A-120'}],snippets:['접수번호: A-120'],preview:[]};
 const h=await setup('',{},path=>{
  if(path.endsWith('/sessions'))return {id:'new',revision:0};
  if(path.endsWith('/upload'))return {...readySession(),id:'new',revision:1,messages:[],draft:null,status:'empty',file_inspection:[file]};
 });
 try{
  const files=[new dom.window.File(['접수번호: A-120'],'접수.txt')];
  if(drop){
   const event=new dom.window.Event('drop',{bubbles:true,cancelable:true});
   Object.defineProperty(event,'dataTransfer',{value:{files}});
   await act(async()=>{document.querySelector('.as-composer textarea')!.dispatchEvent(event);});
   assert.ok(event.defaultPrevented);
  }else{
   await click(button('자료 연결'));await click(button('새 파일 올리기'));
   const picker=document.querySelector('input[type=file]') as HTMLInputElement;
   Object.defineProperty(picker,'files',{value:files});
   await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  }
  const deadline=Date.now()+3000;
  while(!document.querySelector('.as-inspected-source')){assert.ok(Date.now()<deadline);await h.flush();}
  const card=document.querySelector('.as-inspected-source') as HTMLDetailsElement;assert.ok(card.open);
  // The upload itself only describes what was read and asks what to do; no model call, no question.
  assert.match(document.querySelector('.as-file-summary')?.textContent||'',/「접수.txt」 파일은 문서 자료예요\. 찾은 항목: 접수번호\..*이 파일로 어떤 작업을 할까요\?/);
  assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
  assert.match(card.textContent||'',/접수번호: A-120/);await click(field('접수번호'));await click(button('요청에 추가'));
  assert.equal((document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement).value,'접수.txt에서 접수번호 항목을 추출해 주세요.');
  assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
 }finally{await h.close();}
});
test('DOM: a file attached during the conversation gets its own summary until the next message',async()=>{
 const old={id:'old',name:'기존.csv',kind:'table',has_readable_text:true,columns:['금액'],fields:[],snippets:[],preview:[]};
 const added={id:'added',name:'추가.csv',kind:'table',has_readable_text:true,columns:['지역','금액'],fields:[],snippets:[],preview:[]};
 const base={...readySession(),status:'needs_clarification',resources:[{id:'old',name:'기존.csv',kind:'table',row_count:2,columns:['금액'],preview:[]}],file_inspection:[old]};
 const attached={...base,revision:2,resources:[...base.resources,{id:'added',name:'추가.csv',kind:'table',row_count:5,columns:['지역','금액'],preview:[]}],file_inspection:[old,added]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return base;
  if(path.endsWith('/upload'))return attached;
  if(path.endsWith('/message'))return {...attached,revision:3,messages:[...attached.messages,{role:'user',content:'추가 파일도 합쳐줘'}]};
 });
 try{
  assert.equal(document.querySelector('.as-file-summary'),null);  // a message was already sent: no summary yet
  await click(button('자료 연결'));await click(button('새 파일 올리기'));
  const picker=document.querySelector('input[type=file]') as HTMLInputElement;
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['지역,금액'],'추가.csv')]});
  await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  const deadline=Date.now()+3000;
  while(!document.querySelector('.as-file-summary')){assert.ok(Date.now()<deadline);await h.flush();}
  const summary=document.querySelector('.as-file-summary')?.textContent||'';
  assert.match(summary,/「추가\.csv」 파일은 표 자료예요\. 열 2개\(지역, 금액\), 5행을 읽었어요\./);
  assert.match(summary,/새로 연결한 파일을 지금 작업에 어떻게 쓸까요\?/);
  assert.ok(!summary.includes('기존.csv'));
  assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
  const composer=document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement;
  await input(composer,'추가 파일도 합쳐줘');await click(document.querySelector('button[aria-label="업무 설명 보내기"]')!);
  const sent=Date.now()+3000;
  while(document.querySelector('.as-file-summary')){assert.ok(Date.now()<sent);await h.flush();}
 }finally{await h.close();}
});
test('DOM: named sheet selections preserve their sheet names and the primary sheet is previewed once',async()=>{
 const sheets=['서울','부산'].map((name,index)=>({name,columns:['주문번호','금액'],preview:[{주문번호:index+1,금액:index*100+100}]}));
 const file={id:'sheet',name:'지점.xlsx',kind:'table',has_readable_text:true,columns:sheets[0].columns,
  preview:[{'주문번호':'1','금액':'100'}],tables:sheets,fields:[],snippets:[]};
 const session={...readySession(),messages:[],draft:null,file_inspection:[file]};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>path.includes('/session?')?session:undefined);
 try{
  assert.deepEqual(Array.from(document.querySelectorAll('.as-file-preview caption'),el=>el.textContent),['서울','부산']);
  const group=Array.from(document.querySelectorAll('fieldset')).find(el=>el.querySelector('legend')?.textContent==='부산의 열 선택')!;
  await click(group.querySelectorAll('input')[1]);await click(button('요청에 추가'));
  const composer=document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement;
  assert.equal(composer.value,'지점.xlsx에서 「부산」의 금액 항목을 추출해 주세요.');assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
 }finally{await h.close();}
});
test('DOM: equal sheet headers and equal first row do not hide a different set of source rows',async()=>{
 const file={id:'sheet',name:'배송.xlsx',kind:'table',has_readable_text:true,columns:['송장'],fields:[],snippets:[],
  preview:[{송장:'0001'},{송장:'0002'}],tables:[{name:'반송',columns:['송장'],preview:[{송장:'0001'},{송장:'0003'}]}]};
 const h=await setup('',{'a4b-builder-session':'ready'},p=>p.includes('/session?')?{...readySession(),file_inspection:[file]}:undefined);
 try{assert.deepEqual(Array.from(document.querySelectorAll('.as-file-preview tbody'),el=>el.textContent),
  ['00010002','00010003']);}finally{await h.close();}
});

test('DOM: historical and saved references can be deleted, live inputs stay protected and cleanup can retry',async()=>{
 const free={id:'free',name:'사본.jpg',format:'jpg',has_original:true};
 let tries=0,removed=false;
 const h=await setup('?screen=assets',{},path=>{
  if(path.endsWith('/library'))return {space:{id:'personal',kind:'personal',name:'내 자료'},folder_id:null,folders:[],assets:[
   {...free,usages:[{kind:'workflow',id:'wf',name:'월말 정산'},{kind:'run',id:'wf',name:'월말 정산'}]},
   {...free,id:'history',name:'실행.jpg',usages:[{kind:'run',id:'wf',name:'월말 정산'}]},
   {...free,id:'active',name:'처리중.csv',usages:[{kind:'running',id:'wf',name:'월말 정산'}]},
   {...free,id:'schedule',name:'예약.csv',usages:[{kind:'schedule',id:'wf',name:'월말 정산'}]}].filter(asset=>!removed||asset.id!=='free'),next:null};
  if(path.endsWith('/assets?id=free') && ++tries===1)
   return new Response(JSON.stringify({error:'삭제를 다시 시도하세요.',pending_delete:true}),{status:503});
  if(path.endsWith('/assets?id=free')){removed=true;return {deleted:true};}
 },createElement(EntryApp));
 try{
  assert.match(document.querySelector('[aria-label="자료 사용처"]')?.textContent||'',/월말 정산.*저장된 워크플로우/);
  assert.ok(button('실행 완료 후 삭제').disabled && button('예약 파일 교체 후 삭제').disabled);
  assert.equal((document.querySelector('[aria-label="실행.jpg 삭제"]') as HTMLButtonElement).disabled,false);
  window.confirm=message=>{assert.match(String(message),/기존 실행 결과는 보관/);return true;};
  await click(document.querySelector('[aria-label="사본.jpg 삭제"]')!);assert.ok(button('삭제 재시도'));
  await click(button('삭제 재시도'));assert.equal(document.querySelectorAll('article').length,3);
 }finally{await h.close();}
});

test('DOM: open my uploads from the designer, preview and confirm deletion',async()=>{
 const asset={id:'first',name:'영수증.jpg',format:'jpg',size_bytes:2048,has_original:true};
 let removed=false;
 const h=await setup('?screen=workflow',{},path=>{
  if(path.endsWith('/library'))return {space:{id:'personal',kind:'personal',name:'내 자료'},folder_id:null,folders:[],assets:removed?[]:[asset],next:null};
  if(path.includes('/asset_preview?'))return {text:'영수증 내용',columns:[],rows:[]};
  if(path.endsWith('/assets?id=first')){removed=true;return {deleted:true};}
 },createElement(EntryApp));
 try{
  await click(button('자료함'));assert.equal(document.querySelector('h1')?.textContent,'자료함');
  await click(button('내용 보기'));assert.match(document.querySelector('[role=dialog]')?.textContent||'',/영수증 내용/);
  assert.equal(document.querySelector('[role=dialog] a')?.getAttribute('href'),'/api/agent-builder/original?id=first');
  await click(document.querySelector('[role=dialog] [data-slot=dialog-close]')!);
  window.confirm=()=>false;await click(button('삭제'));assert.ok(document.querySelector('article'));
  window.confirm=()=>true;await click(button('삭제'));assert.equal(document.querySelector('article'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/assets?id=first')).length,1);
 }finally{await h.close();}
});
test('DOM: choosing B while initial A loads cannot be undone by late A response',async()=>{
 const pending=deferred();const h=await setup('?service=A',{},path=>path.endsWith('/agent?id=A')?pending.promise:undefined);
 try{const b=buttons().find(b=>b.textContent?.includes('서비스 B'));assert.ok(b);await click(b);assert.equal(document.querySelector('h1')?.textContent,'서비스 B');await act(async()=>{pending.resolve(snapshot(A));await tick();});assert.equal(document.querySelector('h1')?.textContent,'서비스 B');}finally{await h.close();}
});
test('DOM: execution confirmation is cleared after resume and cannot become a new request',async()=>{
 const waiting={id:'run',status:'needs_input',answer:'계속할까요?',question:{question:'계속할까요?'},steps:[],rows:[],artifact:null,agent_version:1};
 const h=await setup('?service=A',{},(path)=>path.includes('/agent?')?snapshot(A,[waiting]):path.endsWith('/resume_run')?{...waiting,status:'succeeded',question:null,answer:'완료'}:undefined);
 try{const el=field('확인 질문에 답변');await input(el,'네, 진행해요');await click(button('답변하고 이어 실행'));assert.equal((document.querySelector('.as-service-input textarea') as HTMLTextAreaElement).value,'');assert.equal(h.calls.filter(c=>c.path.endsWith('/resume_run')).length,1);}finally{await h.close();}
});
test('DOM: failed explicit service load leaves a visible error and keeps the old session',async()=>{
 const h=await setup('?service=A',{'a4b-builder-session':'draft'},path=>path.includes('/agent?')?new Response(JSON.stringify({error:'의도된 조회 실패'}),{status:503}):undefined);
 try{assert.match(document.querySelector('[role=alert]')?.textContent||'',/의도된 조회 실패/);assert.equal(localStorage.getItem('a4b-builder-session'),'draft');}finally{await h.close();}
});
const complex={id:'node',version:1,ref:'node:1',saved_at:'',spec:{kind:'processing',name:'복합 필터',purpose:'전체 조건 보존',tool:'filter_sort',model:null,instructions:'',reference:'',settings:{fields:[],query:'preserve'},execution_plan:{filters:[{field:'amount',operator:'gte',value:10,value_type:'number',compare_field:null},{field:'amount',operator:'lt',value:100,value_type:'number',compare_field:null}],sort:[{field:'amount',direction:'descending',value_type:'number'}],match:'all',missing_policy:'keep',duplicate_policy:'keep',duplicate_field:null,output_format:'json'}}};
function libraryProps(onSaved=async()=>{}){return {nodes:[complex],defaults:[],catalog:[],onSaved,onUse:()=>{},onToolUse:()=>{}};}
test('DOM: renaming complex node preserves all hidden filters and sorting in the submitted payload',async()=>{
 const h=await setup('',{},(_p,body)=>body?{node:{...complex,version:2}}:undefined,createElement(NodeLibrary,libraryProps()));
 try{await click(button('설정 수정'));await input(field('작업 이름'),'수정한 이름');await submit(document.querySelector('form')!);const body=h.calls.find(c=>c.body)?.body;assert.equal(body.spec.name,'수정한 이름');assert.equal(body.spec.execution_plan.filters.length,2);assert.deepEqual(body.spec.execution_plan.sort,complex.spec.execution_plan.sort);assert.equal(body.spec.execution_plan.missing_policy,'keep');assert.equal(body.spec.settings.query,'preserve');}finally{await h.close();}
});
test('DOM: node save pending disables form input; duplicate submit makes one write',async()=>{
 const pending=deferred();const h=await setup('',{},(_p,body)=>body?pending.promise:undefined,createElement(NodeLibrary,libraryProps()));
 try{await click(button('설정 수정'));await submit(document.querySelector('form')!);assert.equal((document.querySelector('fieldset') as HTMLFieldSetElement).disabled,true);await submit(document.querySelector('form')!);assert.equal(h.calls.length,1);await act(async()=>{pending.resolve({node:complex});await tick();});assert.equal(document.querySelector('form[aria-label="작업 설정"]'),null);}finally{await h.close();}
});
test('DOM: save success with list failure offers a read-only retry instead of another mutation',async()=>{
 let reads=0;const h=await setup('',{},()=>({node:complex}),createElement(NodeLibrary,libraryProps(async()=>{if(reads++===0)throw Error('목록 오류');})));
 try{await click(button('설정 수정'));await submit(document.querySelector('form')!);assert.match(document.querySelector('[role=alert]')?.textContent||'',/저장은 완료/);await click(button('저장한 작업 목록 다시 불러오기'));assert.equal(h.calls.length,1);assert.equal(document.querySelector('[role=alert]'),null);}finally{await h.close();}
});
test.after(()=>{dom.window.close();globalThis.fetch=originalFetch;for(const [key,descriptor] of old){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete (globalThis as any)[key];}});

test('DOM: startup API failure is visible on the node-library screen too',async()=>{
 const h=await setup('?screen=nodes',{},path=>path.endsWith('/catalog')?new Response(JSON.stringify({error:'카탈로그 연결 오류'}),{status:503}):undefined);
 try{assert.match(document.querySelector('[role=alert]')?.textContent||'',/카탈로그 연결 오류/);}finally{await h.close();}
});
function readySession(){return {id:'ready',revision:1,status:'ready',base_version:0,messages:[{role:'user',content:'합계를 계산해줘'},{role:'assistant',content:'설정 확인'}],draft:{...A.config,explanation:'설정 확인'},resources:[],trace:[],issues:[]};}
const receiptBrowserOptions=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul'}).resolvedOptions();
test('DOM: receipt clarification uploads a file and submits only chosen fields to its pending question',async t=>{
 t.mock.method(Intl.DateTimeFormat.prototype,'resolvedOptions',()=>receiptBrowserOptions);
 const attach={id:'a'.repeat(24),key:'receipt.attachment',text:'영수증을 첨부해 주세요.',action:{type:'attach_receipt'}};
 const fields={id:'b'.repeat(24),key:'receipt.fields',text:'어떤 항목을 추출할까요?',action:{type:'select_fields',options:['날짜','상호명','결제 금액']}};
 function waiting(question:typeof attach,revision:number){
  const session=readySession();
  const content=(question.key==='receipt.fields'?'영수증.pdf 파일을 연결했어요. ':'')+question.text;
  return {...session,revision,status:'needs_clarification',messages:[{role:'assistant',content}],
   draft:{...session.draft,questions:[question.text],question_options:[['나중에 결정']]},pending_questions:[question]};
 }
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return waiting(attach,1);
  if(path.endsWith('/upload'))return waiting(fields,2);
  if(path.endsWith('/message'))return readySession();
 });
 try{
  const picker=document.querySelector('input[type=file]') as HTMLInputElement;
  let opened=0;picker.addEventListener('click',()=>{opened++;});
  await click(button('영수증 파일 첨부'));
  assert.equal(opened,1);assert.ok(picker.accept.includes('.pdf')&&picker.accept.includes('.jpg'));assert.ok(!picker.accept.includes('.csv'));
  assert.equal(buttons().some(b=>b.textContent==='나중에 결정'),false);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/message')).length,0);
  async function pick(name:string) {
   Object.defineProperty(picker,'files',{configurable:true,value:[new dom.window.File(['receipt'],name)]});
   await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  }
  await pick('잘못된 파일.csv');
  assert.match(document.querySelector('[role=alert]')?.textContent||'',/영수증 사진이나 PDF/);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/upload')).length,0);
  await pick('영수증.pdf');
  const deadline=Date.now()+3000;
  while(!document.querySelector('.as-receipt-fields')){assert.ok(Date.now()<deadline,'Upload did not reach field selection');await h.flush();}
  assert.equal(h.calls.find(c=>c.path.endsWith('/upload'))?.body.fileName,'영수증.pdf');
  assert.equal(document.querySelector('.as-message.assistant p')?.textContent,'영수증.pdf 파일을 연결했어요.');
  await pick('재첨부.csv');
  assert.equal(h.calls.filter(c=>c.path.endsWith('/upload')).length,1);
  assert.equal(button('선택한 항목으로 계속').disabled,true);
  assert.equal(document.querySelector('.as-receipt-items'),null);
  await click(field('날짜'));await click(field('결제 금액'));await input(field('다른 항목 추가'),'승인번호');
  await click(button('선택한 항목으로 계속'));
  const answers=h.calls.filter(c=>c.path.endsWith('/message')).map(c=>c.body);
  assert.deepEqual(answers,[{id:'ready',revision:2,content:'영수증에서 추출할 항목: 날짜, 결제 금액, 승인번호',questionId:fields.id,userTimezone:'Asia/Seoul',generationRequestId:'00000000-0000-4000-8000-000000000081'}]);
 }finally{await h.close();}
});
for(const [name,withSummary,itemColumns] of [
 ['items only',false,['상품코드','금액']],['summary only',true,[]],['summary and items',true,['상품코드','상품명','단가','수량','금액']],
] as const) test('DOM: grounded Excel receipt submits '+name+' to the current question',async t=>{
 t.mock.method(Intl.DateTimeFormat.prototype,'resolvedOptions',()=>receiptBrowserOptions);
 const columns=['상품코드','상품명','단가','수량','금액'];
 const question={id:'c'.repeat(24),key:'receipt.fields',text:'엑셀에 담을 항목을 선택해 주세요.',
  action:{type:'select_fields',options:['날짜','결제 금액','승인번호'],detected_fields:[{label:'날짜',value:'2010-03-24'},{label:'승인번호'}],
   item_details:{label:'상품별 구매 내역',selected_by_default:true,options:columns,preview:[{'상품코드':'0123','상품명':null,'단가':'1,300','수량':'2','금액':'2,600'}]}}};
 const saved={...readySession(),status:'needs_clarification',pending_questions:[question]};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return saved;
  if(path.endsWith('/message'))return readySession();
 });
 try {
  const next=button('선택한 항목으로 계속');
  assert.ok(field('날짜').checked);assert.equal(field('결제 금액').checked,false);
  assert.match(field('승인번호').parentElement?.textContent||'',/파일별 값이 다름/);
  assert.match(field('결제 금액').parentElement?.textContent||'',/원본에서 확인 안 됨/);
  assert.ok(document.querySelector('.as-receipt-fields')?.textContent?.includes('2010-03-24'));
  assert.deepEqual(Array.from(document.querySelectorAll('.as-receipt-items tbody td'),cell=>cell.textContent),['0123','확인 안 됨','1,300','2','2,600']);
  if(!withSummary)await click(field('날짜'));else await input(field('다른 항목 추가'),'부가세');
  for(const column of columns){
   assert.ok(field(column).checked);
   if(!(itemColumns as readonly string[]).includes(column))await click(field(column));
  }
  await click(next);
  const lines=[...(withSummary?['영수증에서 추출할 항목: 날짜, 부가세']:[]),...(itemColumns.length?['영수증 품목 시트: '+itemColumns.join(', ')]:[])];
  assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/message')).map(c=>c.body),
   [{id:'ready',revision:1,questionId:question.id,content:lines.join('\n'),userTimezone:'Asia/Seoul',generationRequestId:'00000000-0000-4000-8000-000000000081'}]);
 }finally{await h.close();}
});
test('DOM: reopened receipt session replaces only superseded question lines on the active turn',async()=>{
 const oldFile='영수증 파일 형식을 알려주세요.', oldFields='추출할 항목을 알려주세요.';
 const oldReply='이전 답변입니다.\n'+oldFile;
 const question={id:'a'.repeat(24),key:'receipt.attachment',text:'영수증을 첨부해 주세요.',
  supersedes_texts:[oldFile,oldFields],action:{type:'attach_receipt'}};
 const stored={...readySession(),status:'needs_clarification',pending_questions:[question],
  messages:[{role:'assistant',content:oldReply},{role:'user',content:'영수증을 읽어 줘'},
            {role:'assistant',content:['제출 안내입니다.',oldFile,'• '+oldFields,'추가 안내도 확인하세요.'].join('\n')}]};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>path.includes('/session?')?stored:undefined);
 try{
  assert.ok(button('영수증 파일 첨부'));
  assert.deepEqual(Array.from(document.querySelectorAll('.as-message.assistant p'),p=>p.textContent),
   [oldReply,'제출 안내입니다.\n추가 안내도 확인하세요.']);
 }finally{await h.close();}
});
test('DOM: workflow save still enters execution when the list refresh alone fails',async()=>{
 let catalogReads=0;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return readySession();
  if(path.endsWith('/confirm'))return {...readySession(),status:'saved',saved_agent_id:'A'};
  if(path.endsWith('/catalog')&&catalogReads++>0)return new Response(JSON.stringify({error:'목록 갱신 오류'}),{status:503});
 });
 try{await click(button('저장'));assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.ok(document.querySelector('.screen-run'));assert.match(document.querySelector('[role=alert]')?.textContent||'',/저장은 완료/);}finally{await h.close();}
});
test('DOM: service-detail failure after saving offers reload without another confirm',async()=>{
 let details=0;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return readySession();
  if(path.endsWith('/confirm'))return {...readySession(),status:'saved',saved_agent_id:'A'};
  if(path.includes('/agent?')&&details++===0)return new Response(JSON.stringify({error:'서비스 조회 오류'}),{status:503});
 });
 try{await click(button('저장'));await click(button('저장한 워크플로우 다시 불러오기'));assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,1);}finally{await h.close();}
});
test('DOM: validation error preserves the node form for correction and retry',async()=>{
 let writes=0;
 const h=await setup('',{},()=>writes++===0?new Response(JSON.stringify({error:'기준 버전이 오래됐습니다'}),{status:409}):{node:complex},createElement(NodeLibrary,libraryProps()));
 try{await click(button('설정 수정'));await input(field('작업 이름'),'보존할 이름');await submit(document.querySelector('form')!);assert.equal(field('작업 이름').value,'보존할 이름');assert.match(document.querySelector('[role=alert]')?.textContent||'',/기준 버전/);await submit(document.querySelector('form')!);assert.equal(writes,2);}finally{await h.close();}
});

test('DOM: React StrictMode restoration keeps the requested service and correct storage',async()=>{
 const h=await setup('?service=B',{'a4b-builder-session':'old'},undefined,createElement(StrictMode,null,createElement(AgentStudio)));
 try{assert.equal(document.querySelector('h1')?.textContent,'서비스 B');assert.equal(localStorage.getItem('a4b-selected-agent'),'B');assert.equal(localStorage.getItem('a4b-builder-session'),null);}finally{await h.close();}
});
test('DOM: Korean composition Enter does not prematurely submit the design message',async()=>{
 const h=await setup();try{const text=document.querySelector('[aria-label="업무 설명"]') as HTMLTextAreaElement;assert.ok(text);await input(text,'한국어 입력');await act(async()=>text.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true})));assert.equal(h.calls.filter(c=>c.path.endsWith('/message')).length,0);}finally{await h.close();}
});
for(const succeeds of [true,false])test('DOM: sent text moves to chat while waiting and '+(succeeds?'reconciles on success':'returns to the composer on failure'),async()=>{
 const response=deferred();
 const content='첫 줄 업무 설명\n두 번째 줄 조건';
 const original='  '+content+'  ';
 const session={...readySession(),messages:[],draft:null,status:'empty'};
 const h=await setup('',{},path=>{
  if(path.endsWith('/sessions'))return session;
  if(path.endsWith('/message'))return response.promise;
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,original);
  await click(document.querySelector('button[aria-label="업무 설명 보내기"]')!);
  assert.equal(composer.value,'');
  assert.equal(document.querySelector('.as-messages .as-message.user p')?.textContent,content);
  assert.equal(document.querySelector('.as-welcome'),null);
  assert.match(document.querySelector('.as-busy')?.textContent||'',/설정을 구성/);
  await submit(composer.form!);
  assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/message')).map(c=>c.body.content),[content]);
  await act(async()=>{
   response.resolve(succeeds?{...session,revision:2,messages:[
    {role:'user',content},{role:'assistant',content:'조건을 확인했습니다.'},
   ]}:new Response(JSON.stringify({error:'전송 실패'}),{status:503}));
   await tick();
  });
  assert.equal(document.querySelector('.as-busy'),null);
  assert.equal(composer.value,succeeds?'':original);
  assert.equal(document.querySelectorAll('.as-messages .as-message.user').length,succeeds?1:0);
  if(succeeds)assert.equal(document.querySelector('.as-messages .as-message.assistant p')?.textContent,'조건을 확인했습니다.');
  else assert.match(document.querySelector('[role=alert]')?.textContent||'',/전송 실패/);
 }finally{await h.close();}
});
// Optional live-API pass; use the explicitly isolated review database only.
if(process.env.A4B_FRONTEND_REVIEW_URL&&process.env.A4B_FRONTEND_REVIEW_IDS){
 const base=process.env.A4B_FRONTEND_REVIEW_URL;assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);
 const ids=JSON.parse(require('node:fs').readFileSync(process.env.A4B_FRONTEND_REVIEW_IDS,'utf8'));
 const live=(path:string,body:any)=>originalFetch(base+path,{method:body===undefined?'GET':'POST',headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 async function until(predicate:()=>boolean){const deadline=Date.now()+20000;while(!predicate()){assert.ok(Date.now()<deadline,'Timed out waiting for real API / React DOM');await act(async()=>{await new Promise(r=>setTimeout(r,30));});}}
 test('DOM + real API: selected workflow computes actual sums and exposes a download',async()=>{
  const h=await setup('?service='+ids['브라우저 검증 - 합계'],{},live);
  try{await until(()=>document.querySelector('h1')?.textContent==='브라우저 검증 - 합계');const priorTitle=document.querySelector('.as-result-title')?.textContent;await click(button('워크플로우 실행'));await until(()=>!!document.querySelector('.as-result-title')&&!document.querySelector('.as-busy'));assert.notEqual(document.querySelector('.as-result-title')?.textContent,priorTitle);assert.match(document.querySelector('.as-result-title')?.textContent||'',/실행 완료/);const cells=Array.from(document.querySelectorAll('.as-test-result td')).map(c=>c.textContent);assert.ok(cells.includes('30'));assert.ok(cells.includes('40'));let saved:Blob|undefined;
   capturePicker(blob=>{saved=blob;});
   await click(button('JSON 파일 다운로드'));await until(()=>!!saved);
   const payload=JSON.parse(await saved!.text()) as any[];
   assert.equal(payload.length,2);assert.ok(payload.some(row=>Object.values(row).includes(30)));assert.ok(payload.some(row=>Object.values(row).includes(40)));}finally{await h.close();}
 });
 test('DOM + real API: missing input shows file guidance before execution',async()=>{
  const h=await setup('?service='+ids['브라우저 검증 - 파일 필요'],{},live);
  try{await until(()=>document.querySelector('h1')?.textContent==='브라우저 검증 - 파일 필요');assert.equal(button('워크플로우 실행').disabled,true);assert.match(document.querySelector('.as-run-files small')?.textContent||'',/자료|표|파일/);assert.equal(h.calls.filter(c=>c.path.endsWith('/test')).length,0);assert.equal(document.querySelector('.as-result-title'),null);}finally{await h.close();}
 });
 test('DOM + real API: paused schedule runs now using its managed source',async()=>{
  const h=await setup('?service='+ids['브라우저 검증 - 예약'],{},live);
  try{await until(()=>!!document.querySelector('.as-schedule-card'));assert.equal(document.querySelector('.as-run-files'),null);const priorTitle=document.querySelector('.as-result-title')?.textContent;await click(button('지금 실행'));await until(()=>!!document.querySelector('.as-result-title')&&!document.querySelector('.as-busy'));assert.notEqual(document.querySelector('.as-result-title')?.textContent,priorTitle);assert.match(document.querySelector('.as-result-title')?.textContent||'',/실행 완료/);const cells=Array.from(document.querySelectorAll('.as-test-result td')).map(c=>c.textContent);assert.ok(cells.includes('30'));assert.ok(cells.includes('40'));}finally{await h.close();}
 });
 test('DOM + real API: complex node creates a new version preserving both filters and sorting',async()=>{
  const h=await setup('?screen=nodes',{},live);
  try{await until(()=>!!Array.from(document.querySelectorAll('.nl-card')).find(c=>c.textContent?.includes('복합 필터 보존 검증')));const card=Array.from(document.querySelectorAll('.nl-card')).find(c=>c.textContent?.includes('복합 필터 보존 검증'))!;await click(card.querySelector('button')!);await input(field('작업 이름'),'복합 필터 보존 검증');await submit(document.querySelector('.nl-form')!);await until(()=>!!document.querySelector('.nl-notice')&&!(document.querySelector('fieldset') as HTMLFieldSetElement).disabled);const response=await originalFetch(base+'/api/agent-builder/nodes');const result=await response.json() as any;const saved=result.nodes.find((n:any)=>n.id===ids.complex_node);assert.equal(saved.spec.execution_plan.filters.length,2);assert.equal(saved.spec.execution_plan.sort[0].direction,'descending');assert.equal(saved.spec.execution_plan.missing_policy,'keep');assert.ok(saved.version>=2);}finally{await h.close();}
 });
}

test('DOM integration: new document review UI survives merging with workflow restoration fixes',async()=>{
 const analysis={contract_version:'a4b.document_analysis.v1',source_sha256:'a'.repeat(64),source_format:'csv',primary_processor:'csv',status:'succeeded',review_required:false,review_reasons:[],units:[],blocks:[],routes:[],coverage:{},warnings:[]};
 const resource={id:'doc',name:'검토 문서.csv',kind:'table',format:'csv',columns:['amount'],profile:{amount:{types:['number'],missing:0}},preview:[{amount:10}],row_count:1,text:'원래 본문',parser_metadata:{document_analysis:analysis}};
 const review={contract_version:'a4b.document_review.v1',revision:0,status:'pending',source_sha256:'a'.repeat(64),analysis_sha256:'b'.repeat(64),decisions:{},note:'',completed_at:null,updated_at:null};
 const h=await setup('?service=A&screen=workflow',{'a4b-builder-session':'old'},(path,body)=>{
  if(path.includes('/agent?'))return {...snapshot(A),resources:[resource]};
  if(path.includes('/document_review'))return {asset:body?{...resource,text:'검토된 본문',parser_metadata:{document_analysis:analysis,document_review:{...review,status:'completed'}}}:resource,review:body?{...review,revision:1,status:'completed',completed_at:'2026-10-29T00:00:00Z'}:review,groups:[],history:[]};
 });
 try{
  const settingsToggle=document.querySelector<HTMLButtonElement>('.as-config-toggle')!;
  assert.equal(settingsToggle.getAttribute('aria-expanded'),'false');
  await click(settingsToggle);
  assert.equal(document.querySelector('#workflow-settings-detail')?.hasAttribute('hidden'),false);
  const resourceButton=Array.from(document.querySelectorAll('.as-config .as-resource')).find(b=>b.textContent?.includes(resource.name));assert.ok(resourceButton);await click(resourceButton);
  assert.ok(document.querySelector('[aria-label="문서 내용 분석 결과"]'));
  await click(button('원본·대조 후보 검토'));
  const ack=document.querySelector('.dr-check input');assert.ok(ack);await click(ack);await click(button('검토 완료·저장'));
  assert.match(document.querySelector('[aria-label="문서 내용 분석 결과"]')?.textContent||'',/사람 검토 완료/);
  assert.equal(h.calls.filter(c=>c.path.includes('/document_review')&&c.body).length,1);
  assert.equal(localStorage.getItem('a4b-builder-session'),null);
  const accept=Array.from(document.querySelectorAll('input[type=file]')).map(i=>i.getAttribute('accept')||'').join(',');assert.match(accept,/\.hwpx/);assert.match(accept,/\.png/);
 }finally{await h.close();}
});


test('entry: service introduction is the default, then opens home and the workflow library',async()=>{
 const t=await setup('',{},undefined,createElement(EntryApp));
 try{
  assert.match(document.body.textContent||'',/반복 업무를/);
  assert.equal(t.calls.length,0,'landing must not require a backend or model');
  await click(button('A4B 시작하기'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'home');
  await click(document.querySelector('[data-tour="menu-workflows"]')!);await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  assert.ok(document.body.textContent?.includes('서비스 A'));
  assert.ok(document.body.textContent?.includes('서비스 B'));
  await input(document.querySelector('input[aria-label="워크플로우 검색"]')!,'없는 작업');
  assert.ok(document.body.textContent?.includes('검색 결과가 없습니다.'));
 }finally{await t.close();}
});
test('entry: introduction has one start action and uses the actual editor views without API calls',async()=>{
 const h=await setup('?screen=intro',{'a4b-builder-session':'kept'},undefined,createElement(EntryApp));
 try{
  assert.equal(h.calls.length,0);
  assert.equal(document.querySelectorAll('.a4b-landing-hero button').length,0);
  assert.ok(!document.querySelector('.a4b-landing-header')?.textContent?.match(/워크플로우 열기|제품 살펴보기/));
  assert.ok(!document.querySelector('.a4b-steps button'));
  assert.equal(buttons().filter(b=>b.textContent?.trim()==='A4B 시작하기').length,1);
  assert.ok(!document.body.textContent?.match(/FROM WORDS|MADE FOR|YOUR WORK|A CLEAR WAY/));
  assert.equal(document.querySelectorAll('.landing-studio-preview .as-flow-node').length,3);
  for(const node of document.querySelectorAll('.landing-studio-preview .as-flow-node'))assert.ok(node.querySelector('.as-node-kind svg'));
  await click(document.querySelectorAll('.landing-studio-preview .as-flow-node')[1]);
  assert.match(document.querySelector('.as-node-inspector')?.textContent||'',/부족한 재고 찾기/);
  assert.ok(document.querySelector('.as-node-inspector-title svg'));
  await click(button('직접 실행'));
  assert.ok(document.querySelector('.run-workflow-diagram'));
  assert.match(document.querySelector('.landing-preview-run')?.textContent||'',/키보드/);
  await click(document.getElementById('a4b-preview-tab-document')!);
  assert.match(document.querySelector('.landing-studio-preview')?.textContent||'',/반품 접수 기간/);
  assert.equal(h.calls.length,0,'samples and node inspection never call the backend');
  assert.equal(new URL(location.href).searchParams.get('screen'),'intro');
  await click(button('A4B 시작하기'));await h.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'home');
  assert.equal(localStorage.getItem('a4b-builder-session'),'kept');
 }finally{await h.close();}
});
test('entry: use cases select the matching sample while keyboard tabs keep ARIA references valid',async()=>{
 const h=await setup('?screen=intro',{},undefined,createElement(EntryApp));
 try{
  await click(document.querySelector('button[aria-label="응대 검수 예시 보기"]')!);
  assert.equal(document.getElementById('a4b-preview-tab-review')?.getAttribute('aria-selected'),'true');
  assert.match(document.querySelector('.landing-studio-preview')?.textContent||'',/응대 초안 검수/);
  const tab=document.getElementById('a4b-preview-tab-review')!;
  await act(async()=>{tab.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));});
  assert.equal(document.activeElement?.id,'a4b-preview-tab-document');
  for(const button of document.querySelectorAll('[role="tab"]'))assert.ok(document.getElementById(button.getAttribute('aria-controls')!));
  assert.equal(document.querySelectorAll('[role="tabpanel"]:not([hidden])').length,1);
  assert.equal(h.calls.length,0);
 }finally{await h.close();}
});
test('entry: list failures are recoverable and never displayed as an empty success',async()=>{
 let failed=true;
 const t=await setup('?screen=workflows',{},async path=>{if(path.endsWith('/agents')&&failed)throw Error('목록 연결 실패');},createElement(EntryApp));
 try{
  assert.match(document.querySelector('[role=alert]')?.textContent||'',/목록 연결 실패/);
  assert.ok(!document.body.textContent?.includes('첫 워크플로우를 만들어보세요.'));
  failed=false;await click(button('다시 시도'));await t.flush();
  assert.ok(document.body.textContent?.includes('서비스 A'));
 }finally{await t.close();}
});
test('entry: fresh editor starts from the list and browser history returns to the list',async()=>{
 const t=await setup('?screen=workflows',{},undefined,createElement(EntryApp));
 try{
  await click(button('워크플로우'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.ok(document.querySelector('.as-composer'));
  await click(button('목록으로'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  const libraryTitle=document.querySelector('.task-library-title > div')?.textContent;
  await click(button('작업 템플릿'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  assert.equal(document.querySelector('.task-library-title > div')?.textContent,libraryTitle);
  assert.equal(document.querySelector('.task-library-tabs [aria-current="page"]')?.textContent,'작업 템플릿');
  assert.ok(!buttons().some(b=>b.textContent?.trim()==='설계로 돌아가기'));
  await click(button('저장한 워크플로우'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  await act(async()=>{window.history.replaceState(null,'','?screen=intro');window.dispatchEvent(new dom.window.PopStateEvent('popstate'));await tick();});
  assert.ok(document.body.textContent?.includes('반복 업무를'));
 }finally{await t.close();}
});
test('entry: old model URLs redirect to workflows, and deep links keep their exact run',async()=>{
 const t=await setup('?screen=models',{},undefined,createElement(EntryApp));
 try{
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  assert.ok(!document.body.textContent?.includes('Qwen'));
  assert.equal(document.querySelectorAll('input[type=password]').length,0);
  assert.equal(document.querySelector('h1')?.textContent,'작업 라이브러리');
  assert.ok(!t.calls.some(call=>call.path==='/api/credentials'));
 }finally{await t.close();}
 const older={id:'old-run',status:'succeeded',answer:'이전 실행 결과',error:null,steps:[],rows:[],artifact:null,agent_version:1};
 const latest={...older,id:'latest-run',answer:'최신 실행 결과'};
 const r=await setup('?screen=run&service=A&run=old-run',{},async path=>path.includes('/agent?')?snapshot(A,[latest,older]):undefined,createElement(EntryApp));
 try{assert.ok(document.body.textContent?.includes('이전 실행 결과'));assert.ok(!document.body.textContent?.includes('최신 실행 결과'));}finally{await r.close();}
});

test('entry: legacy preview deep link is separate from real workflows, preserves storage, and has no API side effects',async()=>{
 const t=await setup('?screen=preview',{'a4b-selected-agent':'A','a4b-builder-session':'remembered'},undefined,createElement(EntryApp));
 try{
  assert.equal(document.querySelectorAll('.entry-preview-steps').length,0);
  assert.equal(document.querySelectorAll('.entry-preview-node:is(button)').length,1);
  assert.equal(new URL(location.href).searchParams.get('screen'),'preview');
  assert.ok(document.body.textContent?.includes('대화 제안 검토'));
  await click(button('AI 작업 설정 살펴보기'));await t.flush();
  assert.ok(document.body.textContent?.includes('AI 작업 설정'));
  assert.equal(localStorage.getItem('a4b-selected-agent'),'A');
  assert.equal(localStorage.getItem('a4b-builder-session'),'remembered');
  assert.equal(t.calls.length,0);
  await click(button('제안 검토로 돌아가기'));await t.flush();
  assert.equal(new URL(location.href).searchParams.has('previewNode'),false);
 }finally{await t.close();}
 const n=await setup('?screen=preview',{},undefined,createElement(EntryApp));
 try{await click(button('작업 추가'));await n.flush();assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');assert.ok(document.body.textContent?.includes('작업 라이브러리'));}finally{await n.close();}
});

test('entry: user help has no infrastructure controls and unsent input is guarded on exit',async()=>{
 const t=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{
  await click(button('사용 안내'));
  assert.equal(document.querySelectorAll('input[type=password]').length,0);
  assert.ok(document.body.textContent?.includes('화면 둘러보기'));
  assert.ok(!document.body.textContent?.includes('Qwen'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  const close=document.querySelector('[aria-label="안내 닫기"]');if(close)await click(close);
 }finally{await t.close();}
 const n=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{
  await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'아직 보내지 않은 요청');
  let prompts=0;window.confirm=()=>{prompts++;return false;};
  await click(button('작업 찾아보기'));
  const home=document.querySelector('[aria-label="A4B 홈"]')!;
  await click(home);assert.equal(prompts,1);
  assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  await click(button('설계로 돌아가기'));
  assert.equal(document.querySelector<HTMLTextAreaElement>('.as-composer textarea')?.value,'아직 보내지 않은 요청');
  await click(button('작업 찾아보기'));
  window.confirm=()=>true;await click(home);await n.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'home');
  assert.equal(document.querySelector('main h1')?.textContent,'안녕하세요');
 }finally{await n.close();}
});

test('navigation: internal back/forward preserves unsent workflow text and avoids duplicate startup loads',async()=>{
 const t=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 const travel=async(direction:'back'|'forward')=>act(async()=>{window.history[direction]();await new Promise(r=>setTimeout(r,35));});
 try{
  await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'탭 이동 후 유지할 업무');
  const loads=t.calls.filter(c=>c.path.endsWith('/catalog')).length;
  await click(button('작업 찾아보기'));assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');

  await click(button('설계로 돌아가기'));await click(button('직접 실행'));assert.equal(new URL(location.href).searchParams.get('screen'),'run');
  await travel('back');assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  await travel('back');assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  await travel('back');assert.equal((document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement)?.value,'탭 이동 후 유지할 업무');
  await travel('forward');assert.ok(document.body.textContent?.includes('작업 라이브러리'));
  assert.equal(t.calls.filter(c=>c.path.endsWith('/catalog')).length,loads);
 }finally{await t.close();}
});

test('navigation: explicit missing run never falls back to a different latest result',async()=>{
 const t=await setup('?screen=run&service=A&run=missing',{},async path=>path.includes('/agent?')?snapshot(A,[{id:'latest',status:'succeeded',answer:'다른 실행 결과',steps:[],rows:[],artifact:null}]):undefined,createElement(EntryApp));
 try{assert.match(document.querySelector('[role=alert]')?.textContent||'',/지정한 실행 기록/);assert.ok(!document.body.textContent?.includes('다른 실행 결과'));}finally{await t.close();}
});

test('navigation: node form survives internal tab switches and external exit can be cancelled',async()=>{
 const t=await setup('?screen=workflow',{},path=>path.endsWith('/nodes')?{nodes:[complex],defaults:[]}:undefined,createElement(EntryApp));
 try{
  await click(button('작업 찾아보기'));
  await click(button('설정 수정'));await input(field('작업 이름'),'미저장 작업');
  await click(button('설계로 돌아가기'));await click(button('작업 찾아보기'));
  assert.equal(field('작업 이름').value,'미저장 작업');
  window.confirm=()=>false;await click(button('워크플로우'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  assert.equal(field('작업 이름').value,'미저장 작업');
 }finally{await t.close();}
});

test('navigation: cancelling browser back from a dirty editor restores the URL and input',async()=>{
 const t=await setup('?screen=workflows',{},undefined,createElement(EntryApp));
 try{
  await click(button('워크플로우'));await t.flush();
  await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'뒤로 가기로 잃으면 안 되는 입력');
  let prompts=0;window.confirm=()=>{prompts++;return false;};
  await act(async()=>{window.history.back();await new Promise(r=>setTimeout(r,100));});
  assert.equal(prompts,1);assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.equal((document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement).value,'뒤로 가기로 잃으면 안 되는 입력');
  const event=new dom.window.Event('beforeunload',{cancelable:true});window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 }finally{await t.close();}
});

test('redesign: infrastructure controls and model fields are absent from all product menus',async()=>{
 for(const screen of ['workflows','schedules','workflow','nodes','history']){
  const h=await setup('?screen='+screen,{},undefined,createElement(EntryApp));
  try{assert.ok(!document.body.textContent?.includes('Qwen'));assert.equal(document.querySelectorAll('input[type=password]').length,0);assert.deepEqual(Array.from(document.querySelectorAll('.workspace-nav button')).map(b=>b.textContent),['홈','워크플로우','작업 라이브러리','예약 관리','실행 기록','자료함','서비스 연결']);assert.ok(!Array.from(document.querySelectorAll('label')).some(l=>l.textContent?.startsWith('모델')));}finally{await h.close();}

 }
});
test('redesign: changing a saved workflow can be cancelled without losing an unsent request',async()=>{
 const h=await setup('?screen=workflow');
 try{await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'보존할 입력');window.confirm=()=>false;await click(Array.from(document.querySelectorAll('.as-agent-link')).find(b=>b.querySelector('strong')?.textContent==='서비스 B')!);assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'보존할 입력');assert.ok(!h.calls.some(c=>c.path.includes('/agent?id=B')));}finally{await h.close();}
});
test('redesign: mobile panel choices keep the composer value and expose exactly the selected area',async()=>{
 const h=await setup('?screen=workflow');
 try{await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'편집 영역을 바꿔도 보존');const nav=document.querySelector('.as-panel-switcher')!;await click(Array.from(nav.querySelectorAll('button')).find(b=>b.textContent==='처리 흐름')!);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(Array.from(nav.querySelectorAll('button')).find(b=>b.textContent==='설정')!);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'편집 영역을 바꿔도 보존');}finally{await h.close();}
});
test('redesign: library hides manual creation and preserves saved-node edits',async()=>{
 const h=await setup('?screen=nodes',{},undefined,createElement(NodeLibrary,libraryProps()));
 try{assert.equal(document.querySelector('form'),null);assert.ok(!document.body.textContent?.includes('지원 기능'));await click(button('설정 수정'));await input(field('작업 이름'),'보존할 이름');window.confirm=()=>false;await click(button('설정 수정'));assert.equal(field('작업 이름').value,'보존할 이름');assert.ok(!Array.from(document.querySelectorAll('label')).some(l=>l.textContent?.startsWith('역할과 목적')));assert.equal(document.querySelector('[aria-label="작업 종류"]'),null);}finally{await h.close();}
});

test('workspace shell: only the workflow designer exposes the page create action',async()=>{
 let header='';
 for(const screen of ['home','workflows','schedules','history','nodes','workflow','run','assets','connections']){
  const h=await setup('?screen='+screen,{},undefined,createElement(EntryApp));
  try{const chrome=document.querySelector('.workspace-header')!.cloneNode(true) as HTMLElement;const current=chrome.innerHTML.replace(/radix-[^" ]+/g,'radix-id');if(header)assert.equal(current,header);else header=current;assert.equal(document.querySelectorAll('.workspace-brand').length,1);assert.equal(document.querySelector('.workspace-brand')?.tagName,'BUTTON');assert.equal(document.querySelector('.workspace-header [data-tour="help"]'),null);assert.ok(document.querySelector('.workspace-sidebar-footer [data-tour="help"]'));assert.doesNotMatch(document.querySelector('.workspace-sidebar-footer')!.textContent!,/서비스 소개/);assert.equal(document.querySelectorAll('.workspace-context [data-tour="create-workflow"]').length,screen==='workflow'?1:0);assert.equal(document.querySelectorAll('.workspace-nav [aria-current=page]').length,1);await click(button('사용 안내'));assert.ok(document.querySelector('[role=dialog]')?.textContent?.includes('화면 둘러보기'));}finally{await h.close();}
 }
});
test('workspace library: pending startup does not show zero counts or an editable empty form',async()=>{
 const waiting=deferred();const h=await setup('?screen=nodes',{},path=>path.endsWith('/catalog')?waiting.promise:undefined,createElement(EntryApp));
 try{assert.match(document.querySelector('.workspace-loading')!.textContent!,/불러오고/);assert.ok(!document.querySelector('form[aria-label="작업 설정"]'));assert.ok(!document.body.textContent?.includes('지원 기능 0종'));assert.equal(document.querySelector('.workspace-header [data-tour="create-workflow"]'),null);await act(async()=>{waiting.resolve({tools:[]});await tick();});assert.equal(document.querySelector('form[aria-label="작업 설정"]'),null);assert.equal(document.querySelector('.workspace-header [data-tour="create-workflow"]'),null);}finally{await h.close();}
});
test('workspace library: failed initial fetch is distinct from an empty library',async()=>{
 const h=await setup('?screen=nodes',{},path=>{if(path.endsWith('/catalog'))throw Error('카탈로그 불러오기 실패');},createElement(EntryApp));
 try{assert.match(document.querySelector('[role=alert]')!.textContent!,/불러오기 실패/);assert.match(document.querySelector('.workspace-loading')!.textContent!,/불러오지 못했습니다/);assert.ok(!document.querySelector('form[aria-label="작업 설정"]'));assert.ok(!document.body.textContent?.includes('저장한 작업이 없습니다.'));}finally{await h.close();}
});

test('product tour: next and previous follow real controls without changing an unsent request or executing work',async()=>{
 const h=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'안내 후에도 남아야 하는 요청');await click(button('사용 안내'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/1 \/ 5/);await click(button('다음'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/필요한 파일/);await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(button('이전'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'conversation');await click(button('다음'));await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');await click(button('다음'));await click(button('안내 마치기'));assert.equal(document.querySelector('[role=dialog]'),null);assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'안내 후에도 남아야 하는 요청');assert.ok(!h.calls.some(c=>c.body));assert.equal(localStorage.getItem('a4b-product-tour-v1'),'done');}finally{await h.close();}
});
test('product tour: first use opens once, skip is remembered, and help can restart it',async()=>{
 const h=await setup('?screen=workflows',{'a4b-product-tour-v1':''},undefined,createElement(EntryApp));
 try{assert.ok(document.querySelector('[role=dialog]'));await click(button('나중에 보기'));assert.equal(document.querySelector('[role=dialog]'),null);await click(button('워크플로우'));await click(button('작업 찾아보기'));await h.flush();assert.equal(document.querySelector('[role=dialog]'),null);await click(button('사용 안내'));assert.ok(document.querySelector('[role=dialog]')?.textContent?.includes('저장한 작업'));await click(document.querySelector('[aria-label="안내 닫기"]')!);assert.equal(document.querySelector('[role=dialog]'),null);}finally{await h.close();}
});
test('product tour: finishing the list tour starts an empty design and continues its controls, without a model request',async()=>{
 const h=await setup('?screen=workflows',{'a4b-product-tour-v1':''},undefined,createElement(EntryApp));
 try{for(let i=0;document.querySelector('.product-tour-card')?.getAttribute('data-tour-target')!=='menu-workflow';i++){assert.ok(i<8,'List tour should reach the workflow menu');await click(button('다음'));}await click(button('설계 시작'));await h.flush();assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');assert.match(document.querySelector('[role=dialog]')!.textContent!,/할 일과 원하는 결과/);assert.ok(!h.calls.some(c=>c.body));}finally{await h.close();}
});

test('product tour: a saved workflow explains review and explicit editing, without pretending its summary is editable',async()=>{
 const h=await setup('?screen=workflow&service=A',{},undefined,createElement(EntryApp));
 try{
  assert.deepEqual(Array.from(document.querySelectorAll('.as-panel-switcher button'),b=>b.textContent),['설계 요약','처리 흐름','설정']);
  assert.equal(document.querySelector('.as-progress'),null);
  await click(button('사용 안내'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/저장한 업무 내용을 확인/);assert.equal(document.querySelector('.as-composer'),null);await click(button('다음'));await click(button('다음'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/이 흐름이 사용하는 설정/);await click(button('다음'));assert.equal(document.querySelector('.product-tour-card')?.getAttribute('data-tour-target'),'edit-workflow');assert.ok(!h.calls.some(c=>c.body));}finally{await h.close();}
});

test('product tour: run guidance visits existing controls without submitting a run or changing its request',async()=>{
 const run={id:'kept-run',status:'succeeded',answer:'보존한 실행 결과',error:null,steps:[],rows:[],artifact:null,agent_version:1};
 const h=await setup('?screen=run&service=A',{},path=>path.includes('/agent?')?snapshot(A,[run]):undefined,createElement(EntryApp));
 try{
  const request=document.querySelector<HTMLTextAreaElement>('textarea[data-tour="run-input"]')!;
  await input(request,'안내 중 실행하지 않을 요청');await click(button('사용 안내'));
  const targets=['saved-workflows','run-input','run-submit','run-result'];
  for(let i=0;i<targets.length;i++){assert.equal(document.querySelector('.product-tour-card')?.getAttribute('data-tour-target'),targets[i]);if(i<targets.length-1)await click(button('다음'));}
  await click(button('안내 마치기'));
  assert.equal(request.value,'안내 중 실행하지 않을 요청');assert.ok(document.body.textContent?.includes('보존한 실행 결과'));assert.ok(!h.calls.some(c=>c.body));assert.equal(document.querySelector('[role=dialog]'),null);
 }finally{await h.close();}
});
test('product tour: library guidance points at the two distinct preset actions and never saves automatically',async()=>{
 const preset={id:'read',version:1,ref:'read:1',origin:'builtin',saved_at:'2026-10-30',spec:{kind:'processing',name:'문서 읽기',purpose:'본문 읽기',tool:'read_document',model:null,instructions:'',reference:'',settings:{},execution_plan:null}};
 const h=await setup('?screen=nodes',{},path=>path.endsWith('/nodes')?{nodes:[],defaults:[preset]}:undefined,createElement(EntryApp));
 try{
  await click(button('설정 수정'));await input(field('작업 이름'),'보존할 작업');await click(button('사용 안내'));
  const targets=['saved-tasks','preset-configure','preset-use'];
  for(let i=0;i<targets.length;i++){assert.equal(document.querySelector('.product-tour-card')?.getAttribute('data-tour-target'),targets[i]);if(i<targets.length-1)await click(button('다음'));}
  await click(button('안내 마치기'));assert.equal(field('작업 이름').value,'보존할 작업');assert.ok(!h.calls.some(c=>c.body));
 }finally{await h.close();}
});
test('product tour: an empty run page points to create rather than an empty saved list',async()=>{
 const h=await setup('?screen=run',{},path=>path.endsWith('/agents')?{agents:[]}:undefined,createElement(EntryApp));
 try{await click(button('사용 안내'));assert.equal(document.querySelector('.product-tour-card')?.getAttribute('data-tour-target'),'create-workflow');assert.match(document.querySelector('[role=dialog]')!.textContent!,/먼저 워크플로우/);assert.ok(!buttons().some(b=>b.textContent?.trim()==='다음'));}finally{await h.close();}
});
test('product tour: preview guidance explains that sample actions are not persisted',async()=>{
 const h=await setup('?screen=preview',{},undefined,createElement(EntryApp));
 try{await click(button('사용 안내'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/샘플이며 저장되거나 실행되지 않습니다/);assert.equal(h.calls.length,0);}finally{await h.close();}
});

test('product tour: Escape closes the guide and restores the original mobile panel',async()=>{
 const h=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{await click(button('처리 흐름'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(button('사용 안내'));await click(button('다음'));await click(button('다음'));await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');await act(async()=>{document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();});assert.equal(document.querySelector('[role=dialog]'),null);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');}finally{await h.close();}
});


test('run page: a pending workflow list must not announce that no saved workflow exists',async()=>{
 const waiting=deferred();
 const h=await setup('?screen=run',{},path=>path.endsWith('/agents')?waiting.promise:undefined);
 try{
  assert.match(document.querySelector('.as-loading[role="status"]')!.textContent!,/불러오고/);
  assert.ok(!document.body.textContent?.includes('저장한 워크플로우가 필요합니다.'));
  await act(async()=>{waiting.resolve({agents:[]});await tick();});
  assert.ok(document.body.textContent?.includes('저장한 워크플로우가 필요합니다.'));
 }finally{await h.close();}
});


test('simplified design starts with the request instead of a tool picker',async()=>{
 const h=await setup('?screen=workflow');
 try{assert.equal(document.querySelector('.nl-select'),null);assert.ok(document.querySelector('textarea[aria-label="업무 설명"]'));assert.ok(!document.body.textContent?.includes('사용할 작업 선택'));}finally{await h.close();}
});


test('simplified settings render saved filter rules in overview and node inspector',async()=>{
 const plan={filters:[{field:'amount',operator:'gte',value:100,compare_field:null,value_type:'number'}],sort:[],match:'all',missing_policy:'keep',duplicate_policy:'keep',duplicate_field:null,output_format:'csv'};
 const saved={...A,config:{...A.config,tool_ids:['filter_sort'],execution_plan:plan,workflow:{entry:'filter',nodes:[{id:'filter',label:'금액 선별',tool:'filter_sort',settings:{execution_plan:plan},bindings:[]}],edges:[]}}};
 const h=await setup('?service=A',{},path=>path.includes('/agent?')?snapshot(saved):undefined);
 try{await click(button('설계'));assert.match(document.querySelector('.as-rule-summary')?.textContent||'',/amount/);await click(document.querySelector('.as-flow-node')!);assert.match(document.querySelector('.as-node-inspector .as-rule-summary')?.textContent||'',/amount/);}finally{await h.close();}
});


test('DOM: canonical aggregate loads executed grouping and saves edited sum arguments',async()=>{
 const aggregate={...structuredClone(complex),spec:{...structuredClone(complex.spec),tool:'aggregate_table',name:'지점 합계',execution_plan:null,settings:{operation:'sum',arguments_json:JSON.stringify({group_by:['branch'],value_column:'amount',null_policy:'exclude'})}}};
 const h=await setup('',{},(_p,body)=>body?{node:{...aggregate,version:2}}:undefined,createElement(NodeLibrary,{...libraryProps(),nodes:[aggregate]}));
 try{
  await click(button('설정 수정'));
  assert.equal(field('그룹 열').value,'branch');assert.equal(field('합계 열').value,'amount');
  await input(field('그룹 열'),'channel');await input(field('합계 열'),'net_amount');
  await submit(document.querySelector('form')!);
  const body=h.calls.find(c=>c.body)?.body;
  assert.deepEqual(JSON.parse(body.spec.settings.arguments_json),{group_by:['channel'],value_column:'net_amount',null_policy:'exclude'});
 }finally{await h.close();}
});

const dailySchedule={frequency:'daily',weekday:null,hour:9,minute:0,timezone:'Asia/Seoul'};
const weeklySchedule={frequency:'weekly',weekday:1,hour:9,minute:30,timezone:'Asia/Seoul'};
function scheduleFixtures(){
 const daily=agent('A','일일 보고서',dailySchedule),weekly=agent('B','주간 보고서',weeklySchedule);
 const missing=agent('C','예약 설정 점검',dailySchedule);
 const west=agent('W','미국 지사 보고서',{...dailySchedule,hour:17,timezone:'America/Los_Angeles'});
 return [
  {agent:daily,schedule:{active:true,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'}},
  {agent:weekly,schedule:{active:false,spec:weeklySchedule,next_run_at:'2099-01-01T00:00:00Z'}},
  {agent:missing,schedule:null},
  {agent:west,schedule:{active:true,spec:west.config.schedule,next_run_at:'2026-11-11T01:00:00Z'}},
 ];
}
function scheduleRow(id:string){const row=document.querySelector('[data-schedule-id="'+id+'"]');assert.ok(row,'Missing scheduled workflow '+id);return row;}
async function chooseScheduleStatus(value:string){
 const select=document.querySelector<HTMLSelectElement>('select[aria-label="예약 상태 필터"]');assert.ok(select);
 await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype,'value')!.set!.call(select,value);select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});
}

test('schedules: shared navigation shows actual statuses and Seoul-standard next executions without writing',async()=>{
 const methods:string[]=[];
 const h=await setup('?screen=workflows',{},(path,_body,method)=>{methods.push(method);if(path.endsWith('/schedules'))return {workflows:scheduleFixtures()};},createElement(EntryApp));
 try{
  await click(button('예약 관리'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'schedules');
  assert.equal(document.querySelector('h1')?.textContent,'예약 관리');
  assert.equal(document.querySelector('.workspace-nav [aria-current=page]')?.textContent,'예약 관리');
  assert.equal(document.querySelectorAll('[data-schedule-id]').length,4);
  assert.match(scheduleRow('A').textContent!,/활성/);assert.match(scheduleRow('A').textContent!,/매일/);
  assert.match(scheduleRow('A').textContent!,/09:00/);assert.match(scheduleRow('A').textContent!,/서울 표준시/);
  assert.match(scheduleRow('A').querySelector('[data-label="다음 실행"]')!.textContent!,/11월 11일.*09:00/);
  assert.match(scheduleRow('W').textContent!,/서울 표준시/);
  assert.match(scheduleRow('W').querySelector('[data-label="다음 실행"]')!.textContent!,/11월 11일.*10:00/);

  assert.match(scheduleRow('B').textContent!,/중지/);assert.doesNotMatch(scheduleRow('B').textContent!,/2099/);
  assert.match(scheduleRow('C').textContent!,/설정 확인 필요/);
  assert.equal(methods.every(method=>method==='GET'),true);
 }finally{await h.close();}
});

test('schedules: search and state filters combine and never include unrelated manual workflows',async()=>{
 const h=await setup('?screen=schedules',{},path=>path.endsWith('/schedules')?{workflows:scheduleFixtures()}:undefined,createElement(EntryApp));
 try{
  assert.equal(document.querySelectorAll('[data-schedule-id]').length,4);
  assert.doesNotMatch(document.querySelector('main')!.textContent!,/서비스 A|서비스 B/);
  await chooseScheduleStatus('active');assert.equal(document.querySelectorAll('[data-schedule-id]').length,2);
  await input(document.querySelector<HTMLInputElement>('input[aria-label="예약 워크플로우 검색"]')!,'일일');
  assert.equal(document.querySelectorAll('[data-schedule-id]').length,1);assert.ok(scheduleRow('A'));
  await chooseScheduleStatus('paused');assert.equal(document.querySelectorAll('[data-schedule-id]').length,0);
  assert.doesNotMatch(document.body.textContent!,/등록한 예약이 없습니다\./);
  await input(document.querySelector<HTMLInputElement>('input[aria-label="예약 워크플로우 검색"]')!,'');
  assert.equal(document.querySelectorAll('[data-schedule-id]').length,1);assert.ok(scheduleRow('B'));
  await chooseScheduleStatus('unconfigured');assert.equal(document.querySelectorAll('[data-schedule-id]').length,1);assert.ok(scheduleRow('C'));
  await chooseScheduleStatus('all');assert.equal(document.querySelectorAll('[data-schedule-id]').length,4);
 }finally{await h.close();}
});

test('schedules: the new menu preserves an unsent design when leaving is cancelled',async()=>{
 const h=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{
  await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'예약 화면으로 이동해도 확인 없이 버리지 않을 입력');
  window.confirm=()=>false;await click(button('예약 관리'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'예약 화면으로 이동해도 확인 없이 버리지 않을 입력');
  assert.equal(h.calls.some(call=>call.path.endsWith('/schedules')),false);
  window.confirm=()=>true;await click(button('예약 관리'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'schedules');
  assert.match(document.body.textContent!,/등록한 예약이 없습니다\./);assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('schedules: a schedule tied to an older workflow version requires review instead of appearing active',async()=>{
 const row=scheduleFixtures()[0];
 const h=await setup('?screen=schedules',{},path=>path.endsWith('/schedules')?{workflows:[{...row,schedule:{...row.schedule!,agent_version:0}}]}:undefined,createElement(EntryApp));
 try{
  assert.match(scheduleRow('A').querySelector('[data-label="상태"]')!.textContent!,/설정 확인 필요/);
  assert.doesNotMatch(scheduleRow('A').querySelector('[data-label="다음 실행"]')!.textContent!,/11월 11일/);
  await chooseScheduleStatus('active');assert.equal(document.querySelectorAll('[data-schedule-id]').length,0);
  await chooseScheduleStatus('unconfigured');assert.ok(scheduleRow('A'));
 }finally{await h.close();}
});

test('schedules: pending and failed requests are not presented as an empty schedule list and retry recovers',async()=>{
 const waiting=deferred();let reads=0;
 const h=await setup('?screen=schedules',{},path=>{
  if(!path.endsWith('/schedules'))return undefined;
  return ++reads===1?waiting.promise:{workflows:scheduleFixtures().slice(0,1)};
 },createElement(EntryApp));
 try{
  assert.match(document.body.textContent!,/불러오고/);assert.doesNotMatch(document.body.textContent!,/등록한 예약이 없습니다\./);
  await act(async()=>{waiting.resolve(new Response(JSON.stringify({error:'예약 목록 연결 실패'}),{status:503}));await tick();});
  assert.match(document.querySelector('[role=alert]')!.textContent!,/예약 목록 연결 실패/);
  assert.doesNotMatch(document.body.textContent!,/등록한 예약이 없습니다\./);
  await click(button('다시 시도'));
  assert.equal(document.querySelector('[role=alert]'),null);assert.ok(scheduleRow('A'));assert.equal(reads,2);
 }finally{waiting.resolve({workflows:[]});await h.close();}
});

test('rename: design and reservation detail persist the title across screens',async()=>{
 let current=A;
 const h=await setup('?screen=workflow&service=A',{},(path,body)=>{
  if(path.includes('/agent?id=A'))return snapshot(current);
  if(path.endsWith('/agents'))return {agents:[current,B]};
  if(path.endsWith('/rename_agent')){
   assert.equal(body.version,current.version);
   current={...current,version:current.version+1,config:{...current.config,name:body.name}};
   return {agent:current};
  }
 },createElement(EntryApp));
 try{
  await click(document.querySelector('button[aria-label="워크플로우 이름 변경"]')!);
  await input(field('워크플로우 이름'),'설계에서 변경');
  await click(button('이름 저장'));
  assert.equal(document.querySelector('.as-page-title h1')?.textContent,'설계에서 변경');
  await click(button('작업 라이브러리'));
  await click(document.querySelector('button[aria-label="설계에서 변경 예약 관리"]')!);
  await click(document.querySelector('button[aria-label="워크플로우 이름 변경"]')!);
  await input(field('워크플로우 이름'),'예약에서 변경');
  await click(button('이름 저장'));
  assert.equal(document.querySelector('.as-page-title h1')?.textContent,'예약에서 변경');
  await click(button('작업 라이브러리'));
  assert.ok(document.querySelector('button[aria-label="예약에서 변경 설계 열기"]'));
 }finally{await h.close();}
});

test('rename: reservation list preserves active status after changing the name',async()=>{
 const workflows=scheduleFixtures();
 const initial=workflows.find(row=>row.agent.id==='A')!;
 const h=await setup('?screen=schedules',{},(path,body)=>{
  if(path.endsWith('/schedules'))return {workflows};
  if(path.endsWith('/rename_agent'))return {agent:{...initial.agent,version:initial.agent.version+1,config:{...initial.agent.config,name:body.name}}};
 },createElement(EntryApp));
 try{
  const row=scheduleRow('A');
  const status=row.querySelector('.schedule-status')?.textContent;
  await click(row.querySelector('button[title="이름 변경"]')!);
  await input(field('워크플로우 이름'),'새 예약 이름');
  await click(button('이름 저장'));
  assert.equal(scheduleRow('A').querySelector('h2')?.textContent,'새 예약 이름');
  assert.equal(scheduleRow('A').querySelector('.schedule-status')?.textContent,status);
 }finally{await h.close();}
});

test('schedules: an empty list offers a fresh workflow without changing an existing saved workflow',async()=>{
 const h=await setup('?screen=schedules',{'a4b-builder-session':'old','a4b-selected-agent':'A'},undefined,createElement(EntryApp));
 try{
  assert.match(document.body.textContent!,/등록한 예약이 없습니다\./);
  await click(button('새 예약 워크플로우'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.equal(localStorage.getItem('a4b-builder-session'),null);assert.equal(localStorage.getItem('a4b-selected-agent'),null);
  assert.ok(document.querySelector('textarea[aria-label="업무 설명"]'));
  assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('schedules: management opens the chosen workflow and leaving a pending list ignores its response',async()=>{
 const daily=agent('A','일일 보고서',dailySchedule),waiting=deferred();let reads=0;
 const h=await setup('?screen=schedules',{},path=>{
  if(path.endsWith('/schedules'))return ++reads===1?{workflows:scheduleFixtures()}:waiting.promise;
  if(path.includes('/agent?id=A'))return snapshot(daily);
 },createElement(EntryApp));
 try{
  const manage=Array.from(scheduleRow('A').querySelectorAll('button')).find(item=>item.textContent?.trim()==='상세 확인');assert.ok(manage);
  await click(manage);
  assert.equal(new URL(location.href).searchParams.get('screen'),'schedule');assert.equal(new URL(location.href).searchParams.get('service'),'A');
  assert.equal(document.querySelector('h1')?.textContent,'일일 보고서');assert.ok(document.querySelector('.as-schedule-card'));
  await click(button('예약 관리'));assert.match(document.body.textContent!,/불러오고/);
  await click(button('워크플로우'));
  await act(async()=>{waiting.resolve({workflows:scheduleFixtures()});await tick();});
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');assert.equal(document.querySelector('h1')?.textContent,'워크플로우 설계');
  assert.equal(localStorage.getItem('a4b-selected-agent'),null);
  assert.equal(document.querySelectorAll('[data-schedule-id]').length,0);assert.equal(h.calls.some(call=>call.body),false);
 }finally{waiting.resolve({workflows:[]});await h.close();}
});

for(const nextState of ['disconnected','account-error'])test('Gmail schedules: window return rechecks '+nextState+' before enabling execution',async()=>{
 const base=agent('G','전날 Gmail 요약',dailySchedule),gmail={...base,config:{...base.config,tool_ids:['email.list','compose_answer']}};
 let current='ready';
 const h=await setup('?screen=schedule&service=G',{},path=>{
  if(path.endsWith('/agents'))return {agents:[gmail]};
  if(path.includes('/agent?id=G'))return {...snapshot(gmail),source_required:false,required_connections:['gmail'],request_required:false,input_requirements:[]};
  if(path==='/api/agent-builder/gmail')return {configured:true,connected:current!=='disconnected',connectionVersion:1,email:current==='ready'?'scheduled@example.com':null,...(current==='account-error'?{accountError:'Gmail 읽기 권한을 다시 확인해주세요.'}:{})};
 },createElement(EntryApp));
 try{
  assert.equal(button('예약 켜기').disabled,false);assert.equal(button('예약 자료로 지금 실행').disabled,false);
  current=nextState;
  await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});
  assert.equal(button('예약 켜기').disabled,true);assert.equal(button('15초 후 시험 예약').disabled,true);assert.equal(button('예약 자료로 지금 실행').disabled,true);
  assert.equal(document.querySelector('.service-connection-state')!.textContent,nextState==='disconnected'?'연결 안 됨':'연결 확인 필요');
  assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('Gmail schedules: a connected account can activate, enqueue a test and run now without a managed file',async()=>{
 const base=agent('G','전날 Gmail 요약',dailySchedule);
 const gmail={...base,config:{...base.config,tool_ids:['email.list','compose_answer'],workflow:{entry:'mail',nodes:[
  {id:'mail',label:'전날 메일 읽기',tool:'email.list',bindings:[],settings:{operation:'read_messages',arguments_json:JSON.stringify({max_results:250})}},
  {id:'summary',label:'메일 요약',tool:'compose_answer',bindings:[{input:'documents',node_id:'mail',output:'documents'}],settings:{}},
 ],edges:[{source:'mail',target:'summary',branch:'next'}]}}};
 const completed={id:'gmail-now',status:'succeeded',answer:'전날 메일 요약 결과',agent_version:1,steps:[],rows:[],artifact:null,error:null};
 let active=false,latest:any[]=[];
 const schedule=()=>({active,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'});
 const h=await setup('?screen=schedule&service=G',{},(path,body)=>{
  if(path.endsWith('/agents'))return {agents:[gmail]};
  if(path.includes('/agent?id=G'))return {...snapshot(gmail,latest),schedule:schedule(),source_required:false,required_connections:['gmail'],request_required:false,input_requirements:[]};
  if(path==='/api/agent-builder/gmail')return {configured:true,connected:true,connectionVersion:1,email:'scheduled@example.com'};
  if(path.endsWith('/schedule')){assert.deepEqual(body,{id:'G',active:!active});active=body.active;return {schedule:schedule()};}
  if(path.endsWith('/schedule_test')){assert.deepEqual(body,{id:'G'});return {job:{id:'gmail-test-job',kind:'schedule_test',due_at:'2026-11-10T00:00:15Z'}};}
  if(path.endsWith('/test')){assert.deepEqual(body,{id:'G',content:'',fileIds:[],useSchedule:true});latest=[completed];return completed;}
 },createElement(EntryApp));
 try{
  assert.ok(document.querySelector('.as-schedule-card'));
  assert.match(document.querySelector('.as-schedule-card')!.textContent!,/Gmail/);
  assert.match(document.querySelector('.as-schedule-card')!.textContent!,/실행당 250건/);
  assert.equal(buttons().some(item=>item.textContent?.trim()==='파일 연결'),false);
  assert.equal(document.querySelector('.as-run-files'),null);
  assert.equal(document.querySelector('textarea[data-tour="run-input"]'),null);
  assert.equal(button('예약 켜기').disabled,false);assert.equal(button('15초 후 시험 예약').disabled,false);assert.equal(button('예약 자료로 지금 실행').disabled,false);
  assert.equal(h.calls.some(call=>call.body),false,'opening the schedule must not activate it');
  await click(button('예약 켜기'));
  assert.equal(button('예약 중지').disabled,false);assert.match(document.querySelector('.schedule-settings .schedule-status')!.textContent!,/활성/);
  await click(button('15초 후 시험 예약'));
  assert.match(document.querySelector('.as-schedule-card')!.textContent!,/시험 예약.*대기|대기.*시험 예약/);
  await click(button('예약 자료로 지금 실행'));
  assert.match(document.querySelector('.as-test-result')!.textContent!,/전날 메일 요약 결과/);
  assert.deepEqual(h.calls.filter(call=>call.body).map(call=>call.path),['/api/agent-builder/schedule','/api/agent-builder/schedule_test','/api/agent-builder/test']);
  assert.equal(h.calls.some(call=>call.path.endsWith('/source_upload')),false);
 }finally{await h.close();}
});

for(const required of [true,undefined])test(`Gmail schedules: file schedules keep managed-source requirements when source_required is ${required}`,async()=>{
 const fileAgent=agent('F','정기 재고 집계',dailySchedule);
 const h=await setup('?screen=schedule&service=F',{},path=>{
  if(path.endsWith('/agents'))return {agents:[fileAgent]};
  if(path.includes('/agent?id=F'))return {...snapshot(fileAgent),...(required===undefined?{}:{source_required:required}),request_required:false,input_requirements:[{kind:'table',required_before_start:true,ready:false,issues:['실행할 표 파일을 연결하세요.']}]};
 },createElement(EntryApp));
 try{
  assert.ok(button('파일 연결'));assert.equal(button('예약 켜기').disabled,true);assert.equal(button('15초 후 시험 예약').disabled,true);
  assert.equal(button('예약 자료로 지금 실행').disabled,true);
  assert.match(document.querySelector('.as-schedule-card')!.textContent!,/관리 데이터 소스/);

  assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('Gmail schedules: switching to a file schedule restores its file requirement instead of keeping the Gmail source mode',async()=>{
 const gmail=agent('G','전날 Gmail 요약',dailySchedule),fileAgent=agent('F','정기 재고 집계',dailySchedule);
 const h=await setup('?screen=schedule&service=G',{},path=>{
  if(path.endsWith('/agents'))return {agents:[gmail,fileAgent]};
  if(path.includes('/agent?id=G'))return {...snapshot(gmail),source_required:false,required_connections:['gmail'],request_required:false,input_requirements:[]};
  if(path.includes('/agent?id=F'))return {...snapshot(fileAgent),source_required:true,request_required:false,input_requirements:[{kind:'table',required_before_start:true,ready:false}]};
  if(path==='/api/agent-builder/gmail')return {configured:true,connected:true,connectionVersion:1,email:'scheduled@example.com'};
 },createElement(EntryApp));
 try{
  assert.equal(button('예약 켜기').disabled,false);
  const openFile=Array.from(document.querySelectorAll('.as-agent-link')).find(item=>item.querySelector('strong')?.textContent==='정기 재고 집계');assert.ok(openFile);
  await click(openFile);
  assert.equal(document.querySelector('h1')?.textContent,'정기 재고 집계');assert.ok(button('파일 연결'));
  assert.equal(button('예약 켜기').disabled,true);assert.equal(button('15초 후 시험 예약').disabled,true);assert.equal(button('예약 자료로 지금 실행').disabled,true);
  assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('source-free schedules: non-Gmail workflows can activate and run without requesting a file or Gmail account',async()=>{
 const scalar=agent('N','정기 숫자 계산',dailySchedule);
 const completed={id:'scalar-now',status:'succeeded',answer:'계산 결과 42',agent_version:1,steps:[],rows:[],artifact:null,error:null};
 let active=false;
 const schedule=()=>({active,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'});
 const h=await setup('?screen=schedule&service=N',{},(path,body)=>{
  if(path.endsWith('/agents'))return {agents:[scalar]};
  if(path.includes('/agent?id=N'))return {...snapshot(scalar),schedule:schedule(),source_required:false,required_connections:[],request_required:false,input_requirements:[]};
  if(path.endsWith('/schedule')){assert.deepEqual(body,{id:'N',active:!active});active=body.active;return {schedule:schedule()};}
  if(path.endsWith('/schedule_test')){assert.deepEqual(body,{id:'N'});return {job:{id:'scalar-test-job',kind:'schedule_test',due_at:'2026-11-10T00:00:15Z'}};}
  if(path.endsWith('/test')){assert.deepEqual(body,{id:'N',content:'',fileIds:[],useSchedule:true});return completed;}
 },createElement(EntryApp));
 try{
  assert.doesNotMatch(document.querySelector('.as-schedule-card')!.textContent!,/Gmail/);
  assert.equal(buttons().some(item=>item.textContent?.trim()==='파일 연결'),false);
  assert.equal(button('예약 켜기').disabled,false);assert.equal(button('15초 후 시험 예약').disabled,false);assert.equal(button('예약 자료로 지금 실행').disabled,false);
  await click(button('예약 켜기'));assert.ok(button('예약 중지'));
  await click(button('15초 후 시험 예약'));await click(button('예약 자료로 지금 실행'));
  assert.match(document.querySelector('.as-test-result')!.textContent!,/계산 결과 42/);
  assert.equal(h.calls.some(call=>call.path==='/api/agent-builder/gmail'),false);
  assert.deepEqual(h.calls.filter(call=>call.body).map(call=>call.path),['/api/agent-builder/schedule','/api/agent-builder/schedule_test','/api/agent-builder/test']);
 }finally{await h.close();}
});

test('Gmail schedules: disconnecting blocks new work while still allowing an active schedule to pause',async()=>{
 const gmail=agent('G','전날 Gmail 요약',dailySchedule);let connected=true,active=true;
 const schedule=()=>({active,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'});
 const h=await setup('?screen=schedule&service=G',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[gmail]};
  if(path.includes('/agent?id=G'))return {...snapshot(gmail),schedule:schedule(),source_required:false,required_connections:['gmail'],request_required:false,input_requirements:[]};
  if(path==='/api/agent-builder/gmail'){
   if(method==='DELETE')connected=false;
   return {configured:true,connected,connectionVersion:1,email:connected?'scheduled@example.com':null};
  }
  if(path.endsWith('/schedule')){assert.deepEqual(body,{id:'G',active:false});active=false;return {schedule:schedule()};}
 },createElement(EntryApp));
 try{
  assert.equal(button('예약 자료로 지금 실행').disabled,false);
  await click(button('연결 관리'));await click(button('A4B 연결 해제'));
  assert.equal(button('15초 후 시험 예약').disabled,true);assert.equal(button('예약 자료로 지금 실행').disabled,true);
  assert.equal(button('예약 중지').disabled,false);
  const close=document.querySelector('[role="dialog"] button[data-slot="dialog-close"]');
  if(close)await click(close);
  await click(button('예약 중지'));
  assert.equal(button('예약 켜기').disabled,true);
  assert.deepEqual(h.calls.filter(call=>call.body).map(call=>call.path),['/api/agent-builder/schedule']);
 }finally{await h.close();}
});

test('history preview: a historical URL loads its detailed answer and explains bounded evidence',async()=>{
 const latest={id:'latest',status:'succeeded',agent_version:1,answer:'가장 최근 실행',steps:[],rows:[],artifact:null};
 const preview={...latest,id:'older',answer:'과거 실행의 축약 미리보기',public_projection:{history_preview:true,evidence_returned:0,evidence_total:120,evidence_truncated:true,quote_truncated:false}};
 const detail={...preview,answer:'상세 메일 요약 '.repeat(600)+'전체 본문의 마지막 메일',evidence:[{file_name:'메일 1',quote:'확인한 첫 메일'}],public_projection:{evidence_returned:1,evidence_total:120,evidence_truncated:true,quote_truncated:false}};
 const h=await setup('?screen=run&service=A&run=older',{},path=>{
  if(path.includes('/agent?id=A'))return snapshot(A,[latest,preview]);
  if(path==='/api/agent-builder/run?id=older')return {run:detail};
 },createElement(EntryApp));
 try{
  assert.equal(h.calls.filter(call=>call.path==='/api/agent-builder/run?id=older').length,1);
  assert.match(document.querySelector('.as-test-result')!.textContent!,/전체 본문의 마지막 메일/);
  assert.doesNotMatch(document.querySelector('.as-test-result')!.textContent!,/축약 미리보기/);
  assert.match(document.querySelector('.as-test-result')!.textContent!,/근거 120개 중 1개를 표시/);
  assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="실행 기록 선택"]')!.value,'older');
  assert.equal(h.calls.some(call=>call.body),false);
 }finally{await h.close();}
});

test('history preview: a delayed historical detail cannot replace a subsequently selected workflow',async()=>{
 const pending=deferred();
 const preview={id:'older',status:'succeeded',agent_version:1,answer:'과거 축약 본문',steps:[],rows:[],public_projection:{history_preview:true,evidence_returned:0,evidence_total:120,evidence_truncated:true,quote_truncated:false}};
 const current={id:'current-B',status:'succeeded',agent_version:1,answer:'선택한 서비스 B의 현재 결과',steps:[],rows:[],artifact:null};
 const h=await setup('?screen=run&service=A&run=older',{},path=>{
  if(path.includes('/agent?id=A'))return snapshot(A,[preview]);
  if(path.includes('/agent?id=B'))return snapshot(B,[current]);
  if(path==='/api/agent-builder/run?id=older')return pending.promise;
 },createElement(EntryApp));
 try{
  assert.ok(h.calls.some(call=>call.path==='/api/agent-builder/run?id=older'));
  const chooseB=Array.from(document.querySelectorAll('.as-agent-link')).find(item=>item.querySelector('strong')?.textContent==='서비스 B');assert.ok(chooseB);await click(chooseB);
  await act(async()=>{pending.resolve({run:{...preview,answer:'늦게 도착한 상세 본문',public_projection:undefined}});await tick();});
  assert.equal(document.querySelector('h1')?.textContent,'서비스 B');
  assert.match(document.querySelector('.as-test-result')!.textContent!,/선택한 서비스 B의 현재 결과/);
  assert.doesNotMatch(document.querySelector('.as-test-result')!.textContent!,/늦게 도착한 상세 본문|과거 축약 본문/);
  assert.equal(localStorage.getItem('a4b-selected-agent'),'B');
 }finally{pending.resolve({run:preview});await h.close();}
});

async function sourceSettles(predicate:()=>boolean){
 for(let attempt=0;attempt<40&&!predicate();attempt++)await act(async()=>{await tick();});
 assert.ok(predicate(),'Source settings did not settle');
}
async function chooseSource(label:string,value:string){
 const select=document.querySelector<HTMLSelectElement>('select[aria-label="'+label+'"]');assert.ok(select);
 await act(async()=>{select.value=value;select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});
 await sourceSettles(()=>!document.querySelector('.schedule-library-source')?.textContent?.includes('자료 목록을 불러오고 있습니다.'));
}
const sourceFolders=[{id:'folder-1',name:'월별 보고서',parent_id:null}];
const sourceAssets=[{id:'asset-1',name:'매출.csv',kind:'table',format:'csv'}];
const sourceReader=[{id:'read',label:'매출 표 읽기',kind:'table'}];
function sourceLibraryResponse(){return {folders:sourceFolders,assets:sourceAssets,next:null};}

for(const space of ['personal','team'] as const)for(const mode of ['fixed','latest','new','all'] as const)
test('schedule source: '+space+' '+mode+' submits the exact folder, reader and revision',async()=>{
 let saved=0;const writes:any[]=[];
 const h=await setup('?screen=run',{},(path,body,method)=>{
  if(path==='/api/auth/me')return {enabled:true,authenticated:true,user:{id:'member',email:'member@example.test',name:'사용자',role:'member'}};
  if(path==='/api/teams')return {teams:[{id:'team-1',name:'영업팀',role:'editor'}]};
  if(path==='/api/team-invitations')return {invitations:[]};
  if(path.startsWith('/api/agent-builder/schedule_source')){
   if(method==='GET')return {selection:null};
   writes.push(body);return {selection:{...body.selection,revision:1}};
  }
  if(path.includes('/library'))return sourceLibraryResponse();
 },createElement(SourceAuthProvider,null,createElement(SourceSettings,{workflowId:'flow',readers:sourceReader,disabled:false,onSaved:async()=>{saved++;}})));
 try{
  await sourceSettles(()=>!!document.querySelector('select[aria-label="예약 자료 공간"] option[value="team-1"]'));
  if(space==='team')await chooseSource('예약 자료 공간','team-1');
  await chooseSource('예약 자료 폴더','folder-1');await chooseSource('예약 자료 읽기 방식',mode);
  if(mode==='fixed'){const checkbox=document.querySelector('.schedule-source-files input');assert.ok(checkbox);await click(checkbox);}
  if(mode==='all'){
   assert.match(document.querySelector('.schedule-library-source')!.textContent!,/파일을 매번 모두 읽어 하나의 실행/);
   assert.match(document.querySelector('.schedule-library-source')!.textContent!,/같은 열 구조의 표들을 합쳐/);
   assert.match(document.querySelector('.schedule-library-source')!.textContent!,/하위 폴더 제외/);
   assert.equal(document.querySelector('.schedule-source-files'),null);
  }
  await click(button('자료함 연결 저장'));
  assert.deepEqual(writes,[{id:'flow',base_revision:0,selection:{mode,space,team_id:space==='team'?'team-1':null,folder_id:'folder-1',asset_ids:mode==='fixed'?['asset-1']:[],target_node_id:'read'}}]);
  assert.equal(saved,1);assert.match(document.querySelector('.schedule-source-notice')!.textContent!,/예약이 중지/);
  const expected=space==='team'?'/api/teams/team-1/library/library?folder_id=folder-1':'/api/agent-builder/library?folder_id=folder-1';
  assert.ok(h.calls.some(call=>call.path===expected));
 }finally{await h.close();}
});

test('schedule source: saved folder-wide documents keep their mode and explain a single combined report',async()=>{
 const selection={mode:'all',space:'personal',team_id:null,folder_id:'folder-1',asset_ids:[],target_node_id:'read',revision:3};
 const h=await setup('?screen=run',{},(path,_body,method)=>{
  if(path.startsWith('/api/agent-builder/schedule_source')&&method==='GET')return {selection};
  if(path.includes('/library'))return {folders:sourceFolders,assets:[{id:'doc-1',name:'주간업무.txt',kind:'document'}],next:null};
 },createElement(SourceSettings,{workflowId:'flow',readers:[{id:'read',label:'업무보고 읽기',kind:'document'}],disabled:false,onSaved:async()=>{}}));
 try{
  await sourceSettles(()=>!!document.querySelector('select[aria-label="예약 자료 읽기 방식"]'));
  assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')!.value,'all');
  assert.match(document.querySelector('.schedule-source-saved')!.textContent!,/폴더 전체 함께 읽기/);
  assert.match(document.querySelector('.schedule-library-source')!.textContent!,/여러 문서를 함께 읽어.*종합 보고서/);
  assert.match(document.querySelector('.schedule-library-source')!.textContent!,/이미 처리한 파일도 다시 읽습니다/);
  assert.match(document.querySelector('.schedule-library-source')!.textContent!,/최대 100개 파일/);
  assert.match(document.querySelector('.schedule-library-source')!.textContent!,/합계 45,000자/);
  assert.equal(document.querySelector('.schedule-source-files'),null);
  assert.equal(button('자료함 연결 저장').disabled,true);
 }finally{await h.close();}
});

test('folder source: a manual workflow configures a folder and runs one report without a reservation',async()=>{
 const flow=agent('manual-folder','팀 보고서');flow.config.tool_ids=['read_document'];
 flow.config.workflow={entry:'read',edges:[],nodes:[{id:'read',tool:'read_document',label:'업무보고 읽기',settings:{file_ids:[]},bindings:[]}]} as any;
 let selection:any=null;
 const h=await setup('?screen=run&service=manual-folder',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow]};
  if(path.includes('/agent?id=manual-folder'))return {...snapshot(flow),library_source:selection,request_required:false,input_requirements:[{node_id:'read',kind:'document',required_before_start:true,ready:false,issues:[]}]};
  if(path.startsWith('/api/agent-builder/schedule_source')){
   if(method==='GET')return {selection};
   selection={...body.selection,revision:1};return {selection};
  }
  if(path.includes('/library'))return {folders:sourceFolders,assets:[],next:null};
  if(path.endsWith('/test'))return {id:'combined-report',status:'succeeded',agent_version:1,answer:'두 업무보고를 합친 팀 보고서입니다.',steps:[],rows:[],artifact:null};
 },createElement(EntryApp));
 try{
  await sourceSettles(()=>!!document.querySelector('select[aria-label="예약 자료 읽기 방식"]'));
  assert.equal(document.querySelectorAll('.schedule-library-source').length,1);
  const sourceSettings = document.querySelector('.schedule-library-source')!.closest('details')!;
  assert.equal(sourceSettings.open,false);
  assert.ok(button('워크플로우 실행').compareDocumentPosition(sourceSettings)&dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  sourceSettings.open=true;
  assert.equal(button('워크플로우 실행').disabled,true);
  await chooseSource('예약 자료 폴더','folder-1');await chooseSource('예약 자료 읽기 방식','all');
  await click(button('자료함 연결 저장'));
  assert.equal(button('워크플로우 실행').disabled,false);
  assert.match(document.querySelector('.as-run-files small')!.textContent!,/연결한 자료함에서 읽습니다/);
  await click(button('워크플로우 실행'));
  assert.deepEqual(h.calls.find(call=>call.path.endsWith('/test'))!.body,{id:'manual-folder',content:'',fileIds:[]});
  assert.equal(h.calls.some(call=>call.path.endsWith('/schedule_configure')),false);
  assert.match(document.querySelector('.as-test-result')!.textContent!,/두 업무보고를 합친 팀 보고서/);
 }finally{await h.close();}
});

test('folder source: manual attachments override the folder and still require the correct file kind',async()=>{
 const flow=agent('manual-folder','팀 보고서');flow.config.tool_ids=['read_document'];
 flow.config.workflow={entry:'read',edges:[],nodes:[{id:'read',tool:'read_document',label:'업무보고 읽기',settings:{file_ids:[]},bindings:[]}]} as any;
 const selection={mode:'all',space:'personal',team_id:null,folder_id:'folder-1',asset_ids:[],target_node_id:'read',revision:1};
 const h=await setup('?screen=run&service=manual-folder',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow]};
  if(path.includes('/agent?id=manual-folder'))return {...snapshot(flow),library_source:selection,request_required:false,input_requirements:[{node_id:'read',kind:'document',required_before_start:true,ready:false,issues:[]}]};
  if(path.startsWith('/api/agent-builder/schedule_source')&&method==='GET')return {selection};
  if(path.includes('/library'))return {folders:sourceFolders,assets:[],next:null};
  if(path.endsWith('/run_upload'))return {asset:{id:body.fileName,name:body.fileName,kind:body.fileName.endsWith('.csv')?'table':'document'}};
  if(path.endsWith('/test'))return {id:'override-report',status:'succeeded',agent_version:1,answer:'이번 실행 문서 결과',steps:[],rows:[],artifact:null};
 },createElement(EntryApp));
 try{
  const picker=document.querySelectorAll<HTMLInputElement>('input[type=file]')[2];
  async function attach(name:string){
   Object.defineProperty(picker,'files',{configurable:true,value:[new dom.window.File(['내용'],name)]});
   await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
   await sourceSettles(()=>!!document.querySelector('[aria-label="'+name+' 제거"]'));
  }
  assert.equal(button('워크플로우 실행').disabled,false);
  await attach('다른표.csv');assert.equal(button('워크플로우 실행').disabled,true);
  assert.match(document.querySelector('.as-run-files small')!.textContent!,/문서를 첨부/);
  await click(document.querySelector('[aria-label="다른표.csv 제거"]')!);
  await attach('이번보고.txt');assert.equal(button('워크플로우 실행').disabled,false);
  assert.match(document.querySelector('.as-run-files small')!.textContent!,/첨부한 파일을 사용.*자료함 연결은 유지/);
  await click(button('워크플로우 실행'));
  assert.deepEqual(h.calls.find(call=>call.path.endsWith('/test'))!.body,{id:'manual-folder',content:'',fileIds:['이번보고.txt']});
  assert.equal(h.calls.some(call=>call.path.endsWith('/schedule_source')&&call.body),false);
 }finally{await h.close();}
});

test('folder source: unsaved manual settings block execution and survive cancelled navigation',async()=>{
 const flow=agent('manual-folder','팀 보고서');flow.config.tool_ids=['read_document'];
 flow.config.workflow={entry:'read',edges:[],nodes:[{id:'read',tool:'read_document',label:'업무보고 읽기',settings:{file_ids:[]},bindings:[]}]} as any;
 const other=agent('other-folder','다른 보고서');
 let selection:any={mode:'all',space:'personal',team_id:null,folder_id:'folder-1',asset_ids:[],target_node_id:'read',revision:1};
 const h=await setup('?screen=workflow&service=manual-folder',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow,other]};
  if(path.includes('/agent?id=other-folder'))return {...snapshot(other),request_required:false};

  if(path.includes('/agent?id=manual-folder'))return {...snapshot(flow),library_source:selection,request_required:false,input_requirements:[{node_id:'read',kind:'document',required_before_start:true,ready:false,issues:[]}]};
  if(path.startsWith('/api/agent-builder/schedule_source')){
   if(method==='GET')return {selection};
   selection={...body.selection,revision:selection.revision+1};return {selection};
  }
  if(path.includes('/library'))return {folders:sourceFolders,assets:[],next:null};
 },createElement(EntryApp));
 try{
  const tab=(text:string)=>Array.from(document.querySelectorAll('.studio-tabs button')).find(item=>item.textContent===text)!;
  await click(tab('직접 실행'));
  await sourceSettles(()=>!!document.querySelector('select[aria-label="예약 자료 읽기 방식"]'));
  await chooseSource('예약 자료 읽기 방식','latest');
  assert.equal(button('워크플로우 실행').disabled,true);
  assert.match(document.querySelector('.schedule-library-source')!.textContent!,/저장한 뒤 실행/);
  await click(button('워크플로우 실행'));assert.equal(h.calls.some(call=>call.path.endsWith('/test')),false);
  let prompts=0;window.confirm=()=>{prompts++;return false;};
  for(const target of [tab('설계'),tab('예약 관리'),button('작업 라이브러리'),button('설정 수정'),button('홈'),button('워크플로우')]){
   await click(target);assert.equal(new URL(location.href).searchParams.get('screen'),'run');
   assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')!.value,'latest');
  }
  const otherLink=()=>Array.from(document.querySelectorAll<HTMLButtonElement>('.as-agent-link')).find(item=>item.querySelector('strong')?.textContent==='다른 보고서')!;
  await click(otherLink());assert.equal(new URL(location.href).searchParams.get('service'),'manual-folder');
  assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')!.value,'latest');
  assert.equal(h.calls.some(call=>call.path.includes('/agent?id=other-folder')),false);
  await act(async()=>{window.history.back();await new Promise(resolve=>setTimeout(resolve,100));});
  assert.equal(new URL(location.href).searchParams.get('screen'),'run');assert.ok(prompts>=6);
  const unload=new dom.window.Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  window.confirm=()=>{prompts++;return true;};
  await click(tab('설계'));assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  await click(tab('직접 실행'));
  await sourceSettles(()=>document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')?.value==='all');
  assert.equal(button('워크플로우 실행').disabled,false);
  assert.equal(h.calls.some(call=>call.path.endsWith('/schedule_source')&&call.body),false);
  await chooseSource('예약 자료 읽기 방식','latest');assert.equal(button('워크플로우 실행').disabled,true);
  await click(button('자료함 연결 저장'));assert.equal(button('워크플로우 실행').disabled,false);
  const count=prompts;await click(tab('설계'));assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');assert.equal(prompts,count);
  await click(tab('직접 실행'));
  await sourceSettles(()=>document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')?.value==='latest');
  await chooseSource('예약 자료 읽기 방식','all');assert.equal(button('워크플로우 실행').disabled,true);
  const beforeSwitch=prompts;await click(otherLink());
  await sourceSettles(()=>document.querySelector('h1')?.textContent==='다른 보고서');
  assert.equal(prompts,beforeSwitch+1,'Changing workflows asks once before discarding source edits');
  assert.equal(new URL(location.href).searchParams.get('service'),'other-folder');
  assert.equal(button('워크플로우 실행').disabled,false,'Old source edits must not disable another workflow');

 }finally{await h.close();}
});

test('folder source: failed workflow selection preserves the source draft and execution block',async()=>{
 const flow=agent('manual-folder','팀 보고서');flow.config.tool_ids=['read_document'];
 flow.config.workflow={entry:'read',edges:[],nodes:[{id:'read',tool:'read_document',label:'업무보고 읽기',settings:{file_ids:[]},bindings:[]}]} as any;
 const other=agent('other-folder','다른 보고서');
 let selection:any={mode:'all',space:'personal',team_id:null,folder_id:'folder-1',asset_ids:[],target_node_id:'read',revision:1};
 const h=await setup('?screen=run&service=manual-folder',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow,other]};
  if(path.includes('/agent?id=manual-folder'))return {...snapshot(flow),library_source:selection,request_required:false,input_requirements:[{node_id:'read',kind:'document',required_before_start:true,ready:false,issues:[]}]};
  if(path.includes('/agent?id=other-folder'))return new Response(JSON.stringify({error:'다른 워크플로우 조회 실패'}),{status:503});
  if(path.startsWith('/api/agent-builder/schedule_source')){
   if(method==='GET')return {selection};
   selection={...body.selection,revision:selection.revision+1};return {selection};
  }
  if(path.includes('/library'))return {folders:sourceFolders,assets:[],next:null};
 },createElement(EntryApp));
 try{
  await sourceSettles(()=>document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')?.value==='all');
  await chooseSource('예약 자료 읽기 방식','latest');
  assert.equal(button('워크플로우 실행').disabled,true);
  let prompts=0;window.confirm=()=>{prompts++;return true;};
  const otherLink=Array.from(document.querySelectorAll<HTMLButtonElement>('.as-agent-link')).find(item=>item.querySelector('strong')?.textContent==='다른 보고서')!;
  await click(otherLink);
  await sourceSettles(()=>!!document.querySelector('.as-global-error'));
  assert.equal(prompts,1);
  assert.equal(document.querySelector('h1')?.textContent,'팀 보고서');
  assert.equal(new URL(location.href).searchParams.get('service'),'manual-folder');
  assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')!.value,'latest');
  assert.equal(button('워크플로우 실행').disabled,true,'A failed switch must not permit running the saved source while its draft is unsaved');
  assert.equal(button('자료함 연결 저장').disabled,false);
  assert.equal(h.calls.some(call=>call.path.endsWith('/test')),false);
  await click(button('자료함 연결 저장'));
  assert.equal(button('워크플로우 실행').disabled,false);

 }finally{await h.close();}
});

test('schedule source: version conflict preserves the draft and does not refresh the parent as a success',async()=>{
 let writes=0,saved=0;
 const selection={mode:'fixed',space:'personal',team_id:null,folder_id:null,asset_ids:['asset-1'],target_node_id:'read',revision:7};
 const h=await setup('?screen=run',{},(path,body,method)=>{
  if(path.startsWith('/api/agent-builder/schedule_source')){
   if(method==='GET')return {selection};
   assert.equal(body.base_revision,7);writes++;
   return new Response(JSON.stringify({error:'예약 자료 설정이 다른 화면에서 변경되었습니다.'}),{status:409});
  }
  if(path.includes('/library'))return sourceLibraryResponse();
 },createElement(SourceSettings,{workflowId:'flow',readers:sourceReader,disabled:false,onSaved:async()=>{saved++;}}));
 try{
  await sourceSettles(()=>!!document.querySelector('.schedule-source-files input'));
  await chooseSource('예약 자료 읽기 방식','latest');await click(button('자료함 연결 저장'));
  assert.equal(writes,1);assert.equal(saved,0);
  assert.equal(document.querySelector<HTMLSelectElement>('select[aria-label="예약 자료 읽기 방식"]')!.value,'latest');
  assert.match(document.querySelector('[role=alert]')!.textContent!,/다른 화면/);
  assert.equal(document.querySelector('.schedule-source-notice'),null);
 }finally{await h.close();}
});

test('schedule source: a pending save blocks duplicate writes and refreshes once after completion',async()=>{
 const pending=deferred();let writes=0,saved=0;const busy:boolean[]=[];
 const h=await setup('?screen=run',{},(path,_body,method)=>{
  if(path.startsWith('/api/agent-builder/schedule_source')){if(method==='GET')return {selection:null};writes++;return pending.promise;}
  if(path.includes('/library'))return sourceLibraryResponse();
 },createElement(SourceSettings,{workflowId:'flow',readers:sourceReader,disabled:false,onSaved:async()=>{saved++;},onBusyChange:(value:boolean)=>busy.push(value)}));
 try{
  await sourceSettles(()=>!!document.querySelector('.schedule-source-files input'));
  await chooseSource('예약 자료 읽기 방식','new');await click(button('자료함 연결 저장'));
  assert.equal(document.querySelector<HTMLFieldSetElement>('.schedule-source-fields')!.disabled,true);
  await click(button('저장 중…'));assert.equal(writes,1);assert.equal(saved,0);
  await act(async()=>{pending.resolve({selection:{mode:'new',space:'personal',team_id:null,folder_id:null,asset_ids:[],target_node_id:'read',revision:1}});await tick();});
  assert.equal(saved,1);assert.deepEqual(busy,[true,false]);
 }finally{pending.resolve({selection:null});await h.close();}
});

test('schedule source: saving refreshes the actual paused schedule before enabling it again',async()=>{
 const flow=agent('F','월별 매출 보고',dailySchedule);flow.config.workflow={entry:'read',edges:[],nodes:[{id:'read',tool:'read_table',label:'매출 표 읽기',settings:{file_ids:[]},bindings:[]}]} as any;
 let active=true,selection:any=null;
 const h=await setup('?screen=schedule&service=F',{},(path,body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow]};
  if(path.includes('/agent?id=F'))return {...snapshot(flow),schedule:{active,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'},source_required:true,required_connections:[],library_source:selection,input_requirements:[{node_id:'read',kind:'table',required_before_start:true,ready:false,issues:[]}],request_required:false};
  if(path.startsWith('/api/agent-builder/schedule_source')){if(method==='GET')return {selection};selection={...body.selection,revision:1};active=false;return {selection};}
  if(path.includes('/library'))return sourceLibraryResponse();
 },createElement(EntryApp));
 try{
  await sourceSettles(()=>!!document.querySelector('.schedule-source-files input'));
  assert.ok(button('예약 중지'));
  await chooseSource('예약 자료 읽기 방식','latest');await click(button('자료함 연결 저장'));
  assert.equal(active,false);assert.ok(button('예약 켜기'));assert.ok(selection);
  assert.ok(h.calls.filter(call=>call.path.includes('/agent?id=F')).length>=2);
 }finally{await h.close();}
});

test('schedule source: skipped new-file runs remain empty after polling instead of reviving an old success',async t=>{
 t.mock.timers.enable({apis:['setInterval']});
 const flow=agent('F','새 파일 집계',dailySchedule);
 const selection={mode:'new',space:'personal',team_id:null,folder_id:null,asset_ids:[],target_node_id:'read',revision:1};
 const oldRun={id:'old-success',status:'succeeded',answer:'과거 파일의 성공 결과입니다.',agent_version:1,steps:[],rows:[],artifact:null,error:null};
 const h=await setup('?screen=schedule&service=F',{},(path,_body,method)=>{
  if(path.endsWith('/agents'))return {agents:[flow]};
  if(path.includes('/agent?id=F'))return {...snapshot(flow,[oldRun]),source_required:true,required_connections:[],library_source:selection,input_requirements:[{node_id:'read',kind:'table',required_before_start:true,ready:false,issues:[]}],request_required:false};
  if(path.startsWith('/api/agent-builder/schedule_source')&&method==='GET')return {selection};
  if(path.includes('/library'))return sourceLibraryResponse();
  if(path.endsWith('/test'))return {status:'skipped',message:'새로 처리할 파일이 없습니다.'};
 },createElement(EntryApp));
 try{
  assert.match(document.body.textContent!,/과거 파일의 성공 결과/);
  await click(button('예약 자료로 지금 실행'));assert.match(document.body.textContent!,/새로 처리할 파일이 없습니다/);
  assert.doesNotMatch(document.querySelector('.as-test-result')?.textContent||'',/과거 파일의 성공 결과/);
  await act(async()=>{t.mock.timers.tick(5000);await tick();});
  assert.doesNotMatch(document.querySelector('.as-test-result')?.textContent||'',/과거 파일의 성공 결과/);
  assert.match(document.body.textContent!,/새로 처리할 파일이 없습니다/);
 }finally{await h.close();}
});


test('request-led scheduling: creation has no execution selector and sends no preset mode',async()=>{
 const session={id:'mode-session',revision:0,messages:[],draft:null,status:'empty',issues:[],trace:[],resources:[]};
 const h=await setup('?screen=workflow',{},(path,body)=>{
  if(path.endsWith('/sessions'))return session;
  if(path.endsWith('/message'))return {...session,revision:1,messages:[{role:'user',content:body.content}]};
 },createElement(EntryApp));
 try{
  assert.equal(document.querySelector('input[name="execution-mode"]'),null);
  await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'매일 오전에 보고서를 만들어줘');
  await submit(document.querySelector<HTMLFormElement>('form.as-composer')!);
  assert.equal(h.calls.find(call=>call.path.endsWith('/sessions'))?.body.executionMode,undefined);
  assert.equal(document.querySelector('input[name="execution-mode"]'),null);
 }finally{await h.close();}
});

for(const includeSchedule of [true,false])test('request-led scheduling: save review explicitly '+(includeSchedule?'includes':'omits')+' the proposed reservation',async()=>{
 const spec={frequency:'daily',weekday:null,hour:9,minute:0,timezone:'Asia/Seoul'};
 const config={...A.config,name:'재고 현황 보고서',schedule:spec,report_destination:'reports'};
 const session={id:'intent-session',revision:4,messages:[{role:'user',content:'매일 오전 9시에 재고 현황을 정리해줘.'}],draft:config,status:'ready',issues:[],trace:[],resources:[]};
 const savedAgent={...A,config:{...config,schedule:includeSchedule?spec:null}};
 const h=await setup('?screen=workflow',{'a4b-builder-session':session.id},(path,body)=>{
  if(path.includes('/session?'))return session;
  if(path.endsWith('/confirm'))return {...session,status:'saved',saved_agent_id:'A',draft:savedAgent.config};
  if(path.includes('/agent?id=A'))return {...snapshot(savedAgent),source_required:false,schedule_request_required:false};
 });
 try{
  assert.equal(document.querySelector('input[name="execution-mode"]'),null);
  await click(button('저장'));
  assert.match(document.querySelector('[role="dialog"]')?.textContent||'',/매일 09:00/);
  assert.match(document.querySelector('[role="dialog"]')?.textContent||'',/서울 표준시/);
  assert.match(document.querySelector('[role="dialog"]')?.textContent||'',/꺼진 상태로 저장/);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/confirm')).length,0);
  assert.equal(document.querySelector('[role="dialog"] input[type="time"]'),null,'known times are shown rather than asked again');
  if(!includeSchedule)await click(document.querySelector('input[name="save-schedule"][value="omit"]')!);
  await click(button(includeSchedule?'예약과 함께 저장':'워크플로우만 저장'));
  assert.equal(h.calls.filter(call=>call.path.endsWith('/confirm')).length,1);
  assert.equal(h.calls.find(call=>call.path.endsWith('/confirm'))!.body.includeSchedule,includeSchedule);
  assert.equal(new URL(location.href).searchParams.get('screen'),includeSchedule?'schedule':'run');
 }finally{await h.close();}
});

for(const sample of [
 {name:'문서 검토',spec:{frequency:'weekly',weekday:2,hour:14,minute:30,timezone:'Asia/Seoul'},label:'매주 수요일 14:30'},
 {name:'월말 비용 정리',spec:{frequency:'monthly',weekday:null,hour:18,minute:0,timezone:'Asia/Seoul',month_end:true},label:'매월 말일 18:00'},
 {name:'서비스 상태 확인',spec:{frequency:'hourly',weekday:null,hour:null,minute:15,timezone:'Asia/Seoul'},label:'매시간 15분'},
])test('request-led scheduling: review displays the existing '+sample.name+' recurrence without another question',async()=>{
 const session={id:'generic-review',revision:2,messages:[],draft:{...A.config,name:sample.name,schedule:sample.spec},status:'ready',issues:[],trace:[],resources:[]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':session.id},path=>path.includes('/session?')?session:undefined);
 try{
  await click(button('저장'));
  assert.match(document.querySelector('[role="dialog"]')?.textContent||'',new RegExp(sample.label));
  assert.equal(document.querySelectorAll('[role="dialog"] input').length,2,'review has save choices only');
  await click(button('돌아가기'));
  assert.equal(h.calls.filter(call=>call.path.endsWith('/confirm')).length,0);
 }finally{await h.close();}
});

test('clarification: numeric pending question sends zero with its exact question id',async()=>{
 const q={id:'hour-question',key:'schedule.hour',text:'몇 시에 실행할까요?',response:{type:'number',minimum:0,maximum:23,step:1,unit:'시'}};
 const session={id:'numeric-session',revision:2,messages:[{role:'assistant',content:q.text}],draft:{...A.config,questions:[q.text],question_options:[[]]},status:'needs_clarification',issues:[],trace:[],resources:[],pending_questions:[q]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':session.id},(path,body)=>{
  if(path.includes('/session?'))return session;
  if(path.endsWith('/message'))return {...session,revision:3,draft:{...session.draft,questions:[]},pending_questions:[],messages:[...session.messages,{role:'user',content:body.content},{role:'assistant',content:'오전 0시를 반영했습니다.'}]};
 },createElement(EntryApp));
 try{
  const numeric=document.querySelector<HTMLInputElement>('.clarification-form input[type="number"]')!;assert.ok(numeric);assert.equal(numeric.min,'0');assert.equal(numeric.max,'23');
  await input(numeric,'0');assert.equal(h.calls.some(call=>call.body),false);await click(button('답변 적용'));
  const sent=h.calls.find(call=>call.path.endsWith('/message'))!.body;assert.deepEqual(sent.answers,[{questionId:q.id,value:0}]);assert.equal(sent.content,q.text+' → 0');
  assert.match(document.body.textContent!,/오전 0시를 반영했습니다/);
 }finally{await h.close();}
});

test('clarification: a legacy choice requires confirmation and submits its question context',async()=>{
 const q='결과 파일 형식을 선택하세요.';
 const session={id:'choice-session',revision:1,messages:[{role:'assistant',content:q}],draft:{...A.config,questions:[q],question_options:[['CSV','JSON']]},status:'needs_clarification',issues:[],trace:[],resources:[]};
 const h=await setup('?screen=workflow',{'a4b-builder-session':session.id},(path,body)=>{
  if(path.includes('/session?'))return session;
  if(path.endsWith('/message'))return {...session,revision:2,draft:{...session.draft,questions:[]},messages:[...session.messages,{role:'user',content:body.content}]};
 },createElement(EntryApp));
 try{
  await click(document.querySelector<HTMLInputElement>('.clarification-form input[value="JSON"]')!);
  assert.equal(h.calls.some(call=>call.body),false);await click(button('답변 적용'));
  const sent=h.calls.find(call=>call.path.endsWith('/message'))!.body;assert.equal(sent.content,q+' → JSON');assert.equal(sent.questionId,undefined);
 }finally{await h.close();}
});


test('execution separation: a manual workflow gains a reservation while direct input stays independent',async()=>{
 let reservation:any={active:false,agent_version:1,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'};
 const finished={id:'separate-run',status:'succeeded',answer:'분리 실행 완료',agent_version:1,steps:[],rows:[],artifact:null};
 const h=await setup('?screen=schedule&service=A',{},(path,body)=>{
  if(path.includes('/agent?id=A'))return {...snapshot(A),schedule:reservation,request_required:true,schedule_request_required:false,source_required:false,input_requirements:[]};
  if(path.endsWith('/test'))return finished;
  if(path.endsWith('/schedule_remove')){reservation=null;return {schedule:null};}
 },createElement(EntryApp));
 try{
  assert.equal(button('예약 켜기').disabled,false);assert.equal(button('예약 자료로 지금 실행').disabled,false);
  await click(button('예약 자료로 지금 실행'));
  let sent=h.calls.filter(call=>call.path.endsWith('/test'));assert.deepEqual(sent[0].body,{id:'A',content:'',fileIds:[],useSchedule:true});
  await click(button('직접 실행'));assert.ok(document.querySelector('textarea[data-tour="run-input"]'));
  assert.equal(button('워크플로우 실행').disabled,true);await input(document.querySelector<HTMLTextAreaElement>('textarea[data-tour="run-input"]')!,'이번에는 다른 요청');
  await click(button('워크플로우 실행'));sent=h.calls.filter(call=>call.path.endsWith('/test'));assert.deepEqual(sent[1].body,{id:'A',content:'이번에는 다른 요청',fileIds:[]});
  await click(Array.from(document.querySelectorAll('.studio-tabs button')).find(item=>item.textContent==='예약 관리')!);await click(button('예약 해제'));
  assert.equal(document.querySelector('.schedule-settings .schedule-status')?.textContent,'예약 없음');
  assert.equal(document.querySelector('h1')?.textContent,A.config.name);assert.equal(button('예약 자료로 지금 실행').disabled,true);
 }finally{await h.close();}
});


test('reservation edits survive cancelled tab, browser-back and new-workflow navigation',async()=>{
 let reservation:any={active:false,agent_version:1,spec:dailySchedule,next_run_at:'2026-11-11T00:00:00Z'},prompts=0;
 const h=await setup('?screen=run&service=A',{},(path,body)=>{
  if(path.includes('/agent?id=A'))return {...snapshot(A),schedule:reservation,request_required:true,schedule_request_required:false,source_required:false,input_requirements:[]};
  if(path.endsWith('/schedule_configure')){reservation={...reservation,spec:body.spec};return {schedule:reservation};}
 },createElement(EntryApp));
 try{
  const tab=(text:string)=>Array.from(document.querySelectorAll('.studio-tabs button')).find(item=>item.textContent===text)!;
  await click(tab('예약 관리'));await click(button('일정 수정'));await input(field('실행 시간'),'10:00');
  window.confirm=()=>{prompts++;return false;};await click(tab('직접 실행'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'schedule');assert.equal(field('실행 시간').value,'10:00');
  await act(async()=>{window.history.back();await new Promise(resolve=>setTimeout(resolve,100));});
  assert.equal(new URL(location.href).searchParams.get('screen'),'schedule');assert.equal(field('실행 시간').value,'10:00');
  await click(button('홈'));assert.equal(field('실행 시간').value,'10:00');assert.ok(prompts>=3);
  await click(button('일정 저장'));const count=prompts;await click(tab('직접 실행'));assert.equal(new URL(location.href).searchParams.get('screen'),'run');assert.equal(prompts,count);
  assert.equal(h.calls.filter(call=>call.body).length,1);
 }finally{await h.close();}
});

test('home: typed request waits for submission then starts a fresh design',async()=>{
 const h=await setup('?screen=home',{},path=>path.endsWith('/agents')?{agents:[]}:path.endsWith('/sessions')?{...readySession(),draft:null,status:'empty'}:path.endsWith('/message')?readySession():undefined,createElement(EntryApp));
 try{
  const arrow=document.querySelector<HTMLButtonElement>('.overview-composer button[type=submit]')!;
  assert.ok(arrow.disabled);
  await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="자동화할 업무"]')!,'재고표에서 기준 수량보다 재고가 적은 품목만 골라 엑셀로 정리해 줘.');
  assert.equal(location.search,'?screen=home');
  assert.match(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="자동화할 업무"]')!.value,/재고표에서/);
  assert.ok(!arrow.disabled);
  await click(arrow);
  assert.equal(location.search,'?screen=workflow');
  await h.flush();
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'');
  const messages=h.calls.filter(call=>call.path.endsWith('/message'));
  assert.equal(messages.length,1);
  assert.match(messages[0].body.content,/재고표에서/);
 }finally{await h.close();}
});

test('home: all sidebar destinations return to home and recent entries retain their IDs',async()=>{
 const run={id:'stored-run',status:'succeeded',agent_version:1,steps:[],rows:[],created_at:'2026-11-10T01:00:00Z'};
 const h=await setup('?screen=home',{},path=>path.includes('/agent?id=A')?snapshot(A,[run]):undefined,createElement(EntryApp));
 const nav=(id:string)=>document.querySelector('.workspace-nav [data-tour="menu-'+id+'"]')!;
 const goHome=()=>click(document.querySelector('.workspace-brand')!);
 try{
  const workflow=document.querySelector('#home-workflows')!.closest('section')!.querySelector('.overview-row')!;
  await click(workflow);
  assert.equal(location.search,'?screen=workflow&service=A');
  await goHome();
  assert.ok(!document.querySelector('#home-runs'));
  const library=document.querySelector('#home-library')!.closest('section')!;
  await click(library.querySelectorAll('.overview-row')[1]);
  assert.equal(location.search,'?screen=nodes');
  await goHome();
  const pages = {
   workflow:'워크플로우 설계',workflows:'작업 라이브러리',schedules:'예약 관리',
   history:'실행 기록',assets:'자료함',connections:'서비스 연결',
  };
  for (const [page,title] of Object.entries(pages)){
   await click(nav(page));
   assert.equal(location.search,'?screen='+page);
   assert.equal(document.querySelector('main h1')?.textContent,title);
   assert.equal(nav(page).getAttribute('aria-current'),'page');
   assert.ok(document.querySelector('.workspace-header .ai-status-badge'));
   assert.equal(document.querySelector('.workspace-header .a4b-account-local'),null);
   assert.ok(document.querySelector('.workspace-sidebar-footer [data-tour="help"] + .workspace-sidebar-account .a4b-account-local'));
   await click(nav('home'));
   assert.equal(location.search,'?screen=home');
   assert.equal(nav('home').getAttribute('aria-current'),'page');
   await click(nav(page));
   if (page==='workflows'){
    await click(button('작업 템플릿'));
    assert.equal(location.search,'?screen=nodes');
    assert.equal(nav(page).getAttribute('aria-current'),'page');
    await click(button('저장한 워크플로우'));
    assert.equal(location.search,'?screen=workflows');
   }
   if (page==='history'){
    await click(button('결과 보기'));
    const url=new URL(location.href);
    assert.equal(url.searchParams.get('screen'),'run');
    assert.equal(url.searchParams.get('service'),'A');
    assert.equal(url.searchParams.get('run'),'stored-run');
   }
   await goHome();
   assert.equal(location.search,'?screen=home');
   assert.equal(nav('home').getAttribute('aria-current'),'page');
  }
  await click(nav('assets'));
  await act(async()=>{window.history.back();await new Promise(resolve=>setTimeout(resolve,35));});
  assert.equal(nav('home').getAttribute('aria-current'),'page');
  await act(async()=>{window.history.forward();await new Promise(resolve=>setTimeout(resolve,35));});
  assert.equal(nav('assets').getAttribute('aria-current'),'page');
 }finally{await h.close();}
});

test('home: linked detail pages keep the selected workflow and return to home',async()=>{
 const scheduled=agent('A','일일 보고서',dailySchedule);
 const old={id:'older-run',status:'succeeded',agent_version:1,steps:[],rows:[],answer:'선택한 실행 결과',created_at:'2026-11-10T01:00:00Z'};
 const latest={...old,id:'latest-run',answer:'다른 실행 결과',created_at:'2026-11-10T02:00:00Z'};
 const asset={id:'preview-file',name:'영수증.jpg',format:'jpg',size_bytes:2048,has_original:true};
 const methods:string[]=[];
 const h=await setup('?screen=home',{},(path,_body,method)=>{
  methods.push(method);
  if(path.endsWith('/agents'))return {agents:[scheduled,B]};
  if(path.endsWith('/schedules'))return {workflows:scheduleFixtures()};
  if(path.includes('/agent?id=A'))return snapshot(scheduled,[latest,old]);
  if(path.endsWith('/library'))return {space:{id:'personal',kind:'personal',name:'내 자료'},folder_id:null,folders:[],assets:[asset],next:null};
  if(path.includes('/asset_preview?'))return {text:'영수증 미리보기 내용',columns:[],rows:[]};
  if(path.endsWith('/gmail'))return {configured:true,connected:true,connectionVersion:1,email:'team@example.com'};
 },createElement(EntryApp));
 const route=(screen:string,service:string|null=null,run:string|null=null)=>{
  const params=new URLSearchParams(location.search);
  assert.equal(params.get('screen'),screen);
  assert.equal(params.get('service'),service);
  assert.equal(params.get('run'),run);
 };
 const backHome=async()=>{await click(document.querySelector('.workspace-brand')!);route('home');};
 const closeDialog=async()=>{await click(document.querySelector('[role=dialog] [data-slot=dialog-close]')!);assert.equal(document.querySelector('[role=dialog]'),null);};
 try{
  await click(button('예약 관리'));
  await click(scheduleRow('A').querySelector('[aria-label="일일 보고서 예약 자료·실행 확인"]')!);
  route('schedule','A');
  assert.ok(document.querySelector('.as-schedule-card'));
  await click(button('설계'));
  route('workflow','A');
  assert.equal(document.querySelector('main h1')?.textContent,'일일 보고서');
  await click(button('목록으로'));
  route('workflows');
  await click(document.querySelector('[aria-label="일일 보고서 실행 기록"]')!);
  route('history','A');
  const records=document.querySelectorAll('.entry-history-list article');
  assert.equal(records.length,2);
  await click(records[1].querySelector('button')!);
  route('run','A','older-run');
  assert.match(document.querySelector('.as-test-result')!.textContent!,/선택한 실행 결과/);
  assert.doesNotMatch(document.querySelector('.as-test-result')!.textContent!,/다른 실행 결과/);
  await backHome();

  await click(button('자료함'));
  await click(button('내용 보기'));
  assert.match(document.querySelector('[role=dialog]')!.textContent!,/영수증 미리보기 내용/);
  await closeDialog();
  route('assets');
  await backHome();

  await click(button('서비스 연결'));
  await click(button('연결 관리'));
  assert.match(document.querySelector('[role=dialog]')!.textContent!,/team@example.com/);
  await closeDialog();
  route('connections');
  await backHome();
  assert.ok(methods.every(method=>method==='GET'));
 }finally{await h.close();}
});

test('DOM: a lost message response restores the server question without resending',async()=>{
 const before={...readySession(),status:'needs_clarification'};
 const prompt='주기를 매주로 바꿔줘';
 const after={...before,revision:2,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[...before.messages,{role:'user',content:prompt},{role:'assistant',content:'매주 무슨 요일에 실행할까요?'}]};
 let committed=false;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return committed?after:before;
  if(path.endsWith('/message')){committed=true;throw Error('응답 연결이 끊겼습니다.');}
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:2,action:'message',session_revision:1,request_text:prompt,status:'waiting_input'}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  assert.match(document.body.textContent||'',/매주 무슨 요일에 실행할까요/);
  assert.equal(composer.value,'');assert.equal(composer.disabled,false);
  assert.equal(document.querySelector('[role="alert"]'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});

test('DOM: recovery polls a running request and enables the next answer after completion',async()=>{
 const before={...readySession(),status:'needs_clarification'},prompt='한국 시간으로 해줘';
 let committed=false,running=true;
 const after={...before,revision:2,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[...before.messages,{role:'user',content:prompt},{role:'assistant',content:'실행할 요일을 알려주세요.'}]};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return committed&&!running?after:before;
  if(path.endsWith('/message')){committed=true;throw Error('연결 유실');}
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:2,action:'message',session_revision:1,request_text:prompt,status:running?'running':'waiting_input'}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  assert.equal(composer.disabled,true);assert.ok(button('대화 다시 불러오기'));
  running=false;
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,2200));await tick();});
  assert.match(document.body.textContent||'',/실행할 요일을 알려주세요/);
  assert.equal(composer.disabled,false);assert.equal(document.querySelector('[role="alert"]'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});


for(const destination of ['자료함','홈','새 워크플로우'])test('navigation: starts clean after '+destination,async()=>{
 const session=readySession();
 const h=await setup('?screen=workflow',{'a4b-builder-session':'ready'},path=>path.includes('/session?')?session:undefined,createElement(EntryApp));
 try{
  assert.match(document.querySelector('.as-messages')?.textContent||'',/합계를 계산해줘/);
  await click(button(destination));
  if(destination!=='새 워크플로우')await click(button('워크플로우'));
  assert.equal(localStorage.getItem('a4b-builder-session'),null);
  assert.equal(localStorage.getItem('a4b-selected-agent'),null);
  assert.equal(h.calls.filter(call=>call.path.includes('/session?')).length,1);
  assert.doesNotMatch(document.querySelector('.as-messages')?.textContent||'',/합계를 계산해줘/);
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')?.value,'');
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')).length,0);
 }finally{await h.close();}
});

test('home submission starts generation once after loading and does not resend after remount',async()=>{
 const prompt='매출 합계를 계산해줘';
 const result={...readySession(),messages:[{role:'user',content:prompt},{role:'assistant',content:'처리 기준을 확인할게요.'}]};
 const h=await setup('?screen=home',{},path=>{
  if(path.endsWith('/sessions'))return {...readySession(),status:'empty',draft:null};
  if(path.endsWith('/message')||path.includes('/session?'))return result;
 },createElement(StrictMode,null,createElement(EntryApp)));
 try{
  localStorage.setItem('a4b-builder-session','previous-session');
  localStorage.setItem('a4b-selected-agent','A');
  await input(document.querySelector('textarea[aria-label="자동화할 업무"]')!,prompt);
  await submit(document.querySelector('.overview-composer')!);
  await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')).length,1);
  const messages=h.calls.filter(call=>call.path.endsWith('/message'));
  assert.equal(messages.length,1);
  assert.equal(messages[0].body.content,prompt);
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')?.value,'');
  assert.match(document.querySelector('.as-messages')?.textContent||'',/매출 합계를 계산해줘/);
  assert.equal(window.history.state.initialPrompt,undefined);
  assert.equal(typeof window.history.state.a4bIndex,'number');
  window.confirm=()=>true;
  await click(button('자료함'));await click(button('워크플로우'));await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});

for(const status of ['waiting_input','waiting_confirmation','failed','running','unrelated','stale'])test('review: reused input recovery checks audit and revision: '+status,async()=>{
 const prompt='마지막 행만 남겨줘';
 const before={...readySession(),revision:2,status:'validation_failed',messages:[{role:'user',content:prompt}]};
 const after={...before,revision:status==='stale'?2:3,last_generation_request_id:status==='unrelated'?'other-request':'00000000-0000-4000-8000-000000000081',status:'ready',messages:[...before.messages,{role:'assistant',content:'중복 처리 조건을 반영했습니다.'}]};
 let committed=false,auditRunning=true;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return committed?(status==='running'&&auditRunning?{...after,status:'validation_failed',messages:before.messages}:after):before;
  if(path.endsWith('/message')){committed=true;throw Error('응답 연결 유실');}
  if(path.includes('/generation_history'))return {requests:[{request_id:status==='unrelated'?'00000000-0000-4000-8000-000000000082':'00000000-0000-4000-8000-000000000081',result_revision:3,action:'message',session_revision:2,request_text:status==='unrelated'?'다른 요청':prompt,status:status==='stale'||status==='unrelated'?'waiting_confirmation':status==='running'&&!auditRunning?'waiting_confirmation':status}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  if(status!=='running')assert.match(document.body.textContent||'',/중복 처리 조건을 반영했습니다/);
  const delivered=['waiting_input','waiting_confirmation'].includes(status);
  assert.equal(composer.value,delivered?'':prompt);
  assert.equal(document.querySelector('[role="alert"]')===null,delivered);
  if(status==='running'){
   assert.equal(composer.disabled,true);
   auditRunning=false;await click(button('대화 다시 불러오기'));
   assert.equal(composer.value,'');assert.equal(composer.disabled,false);
   assert.equal(document.querySelector('[role="alert"]'),null);
  }
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});

for(const status of ['failed','blocked'])test('fresh review: preserve input when a new user message was persisted but generation '+status,async()=>{
 const prompt='매출이 10000을 초과하는 행만 골라줘';
 const before=readySession();
 const after={...before,revision:2,status:'validation_failed',messages:[...before.messages,{role:'user',content:prompt}]};
 let sent=false;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return sent?after:before;
  if(path.endsWith('/message')){sent=true;throw Error('응답 연결 유실');}
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:2,action:'message',session_revision:1,request_text:prompt,status,reason:'모델 검사를 완료하지 못했습니다.'}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  assert.match(document.querySelector('[role="alert"]')!.textContent!,/모델 검사를 완료하지 못했습니다/);
  assert.equal(composer.value,prompt,'failed generation must retain its input for retry');
 }finally{await h.close();}
});


test('recovery: a published new message waits for running audit before clearing input',async()=>{
 const prompt='매출을 내림차순으로 정렬해줘',before=readySession();
 const after={...before,revision:2,last_generation_request_id:'00000000-0000-4000-8000-000000000081',messages:[...before.messages,{role:'user',content:prompt},{role:'assistant',content:'정렬 조건을 반영했습니다.'}]};
 let sent=false,running=true;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return sent?(running?{...after,status:'validation_failed',messages:[...before.messages,{role:'user',content:prompt}]}:after):before;
  if(path.endsWith('/message')){sent=true;throw Error('응답 연결 유실');}
  if(path.includes('/generation_history'))return {requests:[{request_id:'00000000-0000-4000-8000-000000000081',result_revision:2,action:'message',session_revision:1,request_text:prompt,status:running?'running':'waiting_confirmation'}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  assert.equal(composer.value,prompt);assert.equal(composer.disabled,true);
  running=false;await click(button('대화 다시 불러오기'));
  assert.equal(composer.value,'');assert.equal(composer.disabled,false);
  assert.equal(document.querySelector('[role="alert"]'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});

for(const scenario of ['redacted','other-tab','completed-before-other-tab'])test('correlation: lost response '+scenario,async()=>{
 const own='00000000-0000-4000-8000-000000000081',other='00000000-0000-4000-8000-000000000082';
 const prompt='Bearer example-test-secret가 있는 행을 골라줘';
 const before={...readySession(),status:'validation_failed',messages:[{role:'user',content:prompt}]};
 const after={...before,revision:scenario==='completed-before-other-tab'?3:2,status:'ready',last_generation_request_id:scenario==='redacted'?own:other,
  messages:[...before.messages,{role:'assistant',content:scenario==='redacted'?'요청한 조건을 반영했습니다.':'다른 탭의 정렬 조건을 반영했습니다.'}]};
 let sent=false;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return sent?after:before;
  if(path.endsWith('/message')){sent=true;throw Error('응답 연결 유실');}
  if(path.includes('/generation_history'))return {requests:[
   ...(scenario==='redacted'?[]:[{request_id:other,action:'message',session_revision:1,request_text:prompt,status:'waiting_confirmation',result_revision:2}]),
   {request_id:own,action:'message',session_revision:1,request_text:'Bearer [보호된 값]',status:scenario==='other-tab'?'running':'waiting_confirmation',...(scenario!=='other-tab'?{result_revision:2}:{})}]};
 });
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  if(scenario==='other-tab'){
   assert.equal(composer.value,prompt);assert.equal(composer.disabled,true);
   assert.ok(document.querySelector('[role="alert"]'));
  }else{
   assert.equal(composer.value,'');assert.equal(composer.disabled,false);
   assert.equal(document.querySelector('[role="alert"]'),null);
  }
  assert.equal(h.calls.find(call=>call.path.endsWith('/message'))?.body.generationRequestId,own);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,1);
 }finally{await h.close();}
});

for(const recoveryResult of ['recovered','active','offline'])test('resume: a stopped generation can continue in the same conversation: '+recoveryResult,async()=>{
 const own='00000000-0000-4000-8000-000000000081',retry='00000000-0000-4000-8000-000000000082';
 const prompt='매출이 10000을 초과하는 행만 골라줘',before=readySession();
 const after={...before,revision:2,messages:[...before.messages,{role:'user',content:prompt},{role:'assistant',content:'조건을 반영했습니다.'}]};
 let sent=0,recovered=false;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return before;
  if(path.endsWith('/message')){if(++sent===1)throw Error('응답 유실');return after;}
  if(path.includes('/generation_history'))return {requests:[{request_id:own,action:'message',session_revision:1,request_text:prompt,status:recovered?'failed':'running',reason:recovered?'이전 작업이 종료됐습니다.':null}]};
  if(path.endsWith('/recover_generation')){
   if(recoveryResult==='offline')throw Error('연결 실패');
   recovered=recoveryResult==='recovered';return {recovered,reason:recovered?undefined:'active'};
  }
 });
 let id=0;Object.defineProperty(crypto,'randomUUID',{configurable:true,value:()=>[own,retry][id++]});
 try{
  const composer=document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer,prompt);await submit(composer.closest('form')!);
  for(let i=1;i<15;i++)await click(button('대화 다시 불러오기'));
  assert.equal(composer.disabled,true);assert.equal(button('홈').disabled,false);
  await click(button('이 대화 이어가기'));
  assert.deepEqual(h.calls.find(call=>call.path.endsWith('/recover_generation'))?.body,{id:'ready',revision:1,generationRequestId:own});
  assert.equal(composer.value,prompt);
  if(recoveryResult==='recovered'){
   assert.equal(composer.disabled,false);await submit(composer.closest('form')!);
   const writes=h.calls.filter(call=>call.path.endsWith('/message'));
   assert.equal(writes.length,2);assert.equal(writes[1].body.id,'ready');
   assert.equal(writes[1].body.generationRequestId,retry);
   assert.match(document.querySelector('.as-messages')?.textContent||'',/조건을 반영했습니다/);
   assert.equal(document.querySelector('[role="alert"]'),null);
  }else{
   assert.equal(composer.disabled,true);assert.equal(button('홈').disabled,false);
   assert.ok(button('이 대화 이어가기'));assert.equal(sent,1);
  }
 }finally{await h.close();}
});

for(const repairResult of ['saved','active','changed'])test('save recovery: persisted workflow with an unadvanced session: '+repairResult,async()=>{
 const own='00000000-0000-4000-8000-000000000081',before=readySession();
 let repaired=false,repairRequested=false;
 const saved={...before,revision:2,status:'saved',saved_agent_id:'A',last_generation_request_id:own};
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return repaired?saved:repairRequested&&repairResult==='changed'?{...before,revision:2,status:'needs_clarification'}:before;
  if(path.endsWith('/confirm'))throw Error('저장 응답 유실');
  if(path.includes('/generation_history'))return {requests:[{request_id:own,action:'confirm',session_revision:1,status:'succeeded',workflow_id:'A'}]};
  if(path.endsWith('/recover_generation')){repairRequested=true;repaired=repairResult==='saved';return {recovered:repaired,reason:repaired?undefined:repairResult};}
 });
 try{
  await click(button('저장'));
  assert.equal(h.calls.filter(call=>call.path.endsWith('/confirm')).length,1);
  assert.deepEqual(h.calls.find(call=>call.path.endsWith('/recover_generation'))?.body,{id:'ready',revision:1,generationRequestId:own});
  if(repairResult==='saved'){
   assert.equal(document.querySelector('main h1')?.textContent,'서비스 A');
   assert.ok(document.querySelector('.screen-run'));assert.equal(document.querySelector('[role="alert"]'),null);
  }else{
   assert.equal(h.calls.some(call=>call.path.includes('/agent?id=A')),false);
   assert.ok(document.querySelector('[role="alert"]'));assert.equal(button('저장').disabled,true);
   assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.disabled,repairResult==='active');
  }

 }finally{await h.close();}
});

test('home automatic submission waits for the design connection and preserves input on session failure',async()=>{
 const prompt='입고 내역을 정리해줘';
 const pending=deferred();
 const h=await setup('?screen=home',{},path=>{
  if(path.includes('/connection'))return pending.promise;
  if(path.endsWith('/sessions'))throw Error('대화를 시작하지 못했습니다');
 },createElement(EntryApp));
 try{
  await input(document.querySelector('textarea[aria-label="자동화할 업무"]')!,prompt);
  await submit(document.querySelector('.overview-composer')!);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')).length,0);
  await act(async()=>{pending.resolve({connection:{model:'preview',credential:{id:'demo',provider:'openai',name:'연결'}},health:{status:'ready'}});await tick();});
  await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')).length,1);
  assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')?.value,prompt);
  assert.match(document.querySelector('[role="alert"]')?.textContent||'',/대화를 시작하지 못했습니다/);
  await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')).length,1);
 }finally{await h.close();}
});


test('designer page new workflow protects unsent input and clears the old conversation after confirmation',async()=>{
 const h=await setup('?screen=workflow',{'a4b-builder-session':'ready'},path=>path.includes('/session?')?readySession():undefined);
 try{
  const composer=()=>document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!;
  await input(composer(),'보존할 입력');
  window.confirm=()=>false;
  await click(document.querySelector('.workspace-context [data-tour="create-workflow"]')!);
  assert.equal(composer().value,'보존할 입력');
  assert.equal(localStorage.getItem('a4b-builder-session'),'ready');
  window.confirm=()=>true;
  await click(document.querySelector('.workspace-context [data-tour="create-workflow"]')!);
  assert.equal(composer().value,'');
  assert.equal(localStorage.getItem('a4b-builder-session'),null);
  assert.equal(localStorage.getItem('a4b-selected-agent'),null);
  assert.equal(document.querySelectorAll('.as-flow-node').length,0);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/sessions')||call.path.endsWith('/message')).length,0);
 }finally{await h.close();}
});

test('direct run keeps an optional request editor open while clearing and after navigation',async()=>{
 const h=await setup('?screen=run&service=A',{},path=>{
  if(path.includes('/agent?'))return {...snapshot(A),request_required:false};
 });
 try{
  const requestField=()=>document.querySelector<HTMLTextAreaElement>('textarea[data-tour="run-input"]')!;
  assert.equal(requestField().closest('details')?.open,false);
  requestField().closest('details')!.open=true;
  await input(requestField(),'이번 실행의 추가 요청');
   await input(requestField(),'');
   assert.equal(requestField().closest('details')?.open,true);
   await input(requestField(),'이번 실행의 추가 요청');
  await click(button('설계'));
  await click(button('직접 실행'));
  assert.equal(requestField().value,'이번 실행의 추가 요청');
  assert.equal(requestField().closest('details')?.open,true);
 }finally{await h.close();}
});

test('home attachment is uploaded before the initial design message',async()=>{
 const prompt='첨부 문서를 요약해줘';
 const h=await setup('?screen=home',{},(path,body)=>{
  if(path.endsWith('/sessions'))return {...readySession(),revision:1,status:'empty',draft:null,resources:[]};
  if(path.endsWith('/upload'))return {...readySession(),revision:2,status:'empty',draft:null,resources:[{id:'home-file',name:'문서.txt',kind:'document'}]};
  if(path.endsWith('/message'))return {...readySession(),messages:[{role:'user',content:prompt}]};
 },createElement(EntryApp));
 try{
  await input(document.querySelector('textarea[aria-label="자동화할 업무"]')!,prompt);
  const picker=document.querySelector<HTMLInputElement>('.overview-composer input[type="file"]')!;
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['내용'],'문서.txt')]});
  await act(async()=>picker.dispatchEvent(new dom.window.Event('change',{bubbles:true})));
  assert.match(document.querySelector('.overview-attachment')?.textContent||'',/문서.txt/);
  await submit(document.querySelector('.overview-composer')!);await h.flush();
  const calls=h.calls.filter(call=>/\/(upload|message)$/.test(call.path));
  assert.deepEqual(calls.map(call=>call.path.split('/').pop()),['upload','message']);
  assert.equal(calls[1].body.revision,2);
  assert.equal(window.history.state.initialFile,undefined);
 }finally{await h.close();}
});

test('home upload failure retains text and does not send a design request',async()=>{
 const prompt='첨부 파일을 정리해줘';
 const h=await setup('?screen=home',{},path=>{
  if(path.endsWith('/sessions'))return {...readySession(),status:'empty',draft:null,resources:[]};
  if(path.endsWith('/upload'))return new Response(JSON.stringify({error:'업로드 실패'}),{status:500});
 },createElement(StrictMode,null,createElement(EntryApp)));
 try{
  await input(document.querySelector('textarea[aria-label="자동화할 업무"]')!,prompt);
  const picker=document.querySelector<HTMLInputElement>('.overview-composer input[type="file"]')!;
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['내용'],'문서.txt')]});
  await act(async()=>picker.dispatchEvent(new dom.window.Event('change',{bubbles:true})));
  await submit(document.querySelector('.overview-composer')!);await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/upload')).length,1);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,0);
  assert.equal(document.querySelector<HTMLTextAreaElement>('[aria-label="업무 설명"]')?.value,prompt);
  assert.match(document.body.textContent||'',/업로드 실패/);
 }finally{await h.close();}
});

test('home allows a file without a prompt and uploads it once in StrictMode',async()=>{
 const h=await setup('?screen=home',{},path=>{
  if(path.endsWith('/sessions'))return {...readySession(),revision:1,status:'empty',draft:null,resources:[]};
  if(path.endsWith('/upload'))return {...readySession(),revision:2,status:'empty',draft:null,resources:[{id:'home-file',name:'문서.txt',kind:'document'}]};
 },createElement(StrictMode,null,createElement(EntryApp)));
 try{
  const picker=document.querySelector<HTMLInputElement>('.overview-composer input[type="file"]')!;
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['내용'],'문서.txt')]});
  await act(async()=>picker.dispatchEvent(new dom.window.Event('change',{bubbles:true})));
  assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="업무 입력 확인"]')!.disabled,false);
  await submit(document.querySelector('.overview-composer')!);await h.flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/upload')).length,1);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/message')).length,0);
  assert.match(document.body.textContent||'',/문서.txt/);
 }finally{await h.close();}
});

test('canvas supports wheel zoom, zoom buttons, drag navigation and reset without changing nodes',async()=>{
 const flow={entry:'read',nodes:[
  {id:'read',tool:'read_document',label:'문서 읽기',bindings:[],settings:{}},
  {id:'out',tool:'finish',label:'완료',bindings:[],settings:{}}
 ],edges:[{source:'read',target:'out',branch:'next'}]};
 const current={...A,config:{...A.config,workflow:flow}};
 const h=await setup('?screen=workflow&service=A',{},path=>path.includes('/agent?')?snapshot(current):undefined);
 try{
  const canvas=document.querySelector<HTMLElement>('.as-desktop-canvas')!;
  const percent=()=>document.querySelector('.as-zoom span')?.textContent;
  assert.equal(percent(),'100%');
  const normal=new dom.window.WheelEvent('wheel',{deltaY:100,bubbles:true,cancelable:true});
  await act(async()=>canvas.dispatchEvent(normal));
  assert.equal(normal.defaultPrevented,false);assert.equal(percent(),'100%');
  const zoom=new dom.window.WheelEvent('wheel',{ctrlKey:true,deltaY:-100,bubbles:true,cancelable:true,clientX:100,clientY:100});
  await act(async()=>canvas.dispatchEvent(zoom));
  assert.equal(zoom.defaultPrevented,true);assert.equal(percent(),'122%');
  const zoomAt=async(deltaY:number,metaKey=false)=>{
   await act(async()=>canvas.dispatchEvent(new dom.window.WheelEvent('wheel',{deltaY,ctrlKey:!metaKey,metaKey,bubbles:true,cancelable:true,clientX:100,clientY:100})));
  };
  await zoomAt(-10000,true);assert.equal(percent(),'200%');
  assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="캔버스 확대"]')!.disabled,true);
  await zoomAt(10000);assert.equal(percent(),'35%');
  assert.equal(document.querySelector<HTMLButtonElement>('[aria-label="캔버스 축소"]')!.disabled,true);
  await click(button('가로로 보기'));assert.equal(percent(),'100%');
  await zoomAt(-100);assert.equal(percent(),'122%');
  await click(document.querySelector('[aria-label="캔버스 확대"]')!);assert.equal(percent(),'132%');
  await click(document.querySelector('[aria-label="캔버스 축소"]')!);assert.equal(percent(),'122%');
  const captures=new Set<number>();
  canvas.setPointerCapture=id=>{captures.add(id);};
  canvas.hasPointerCapture=id=>captures.has(id);
  canvas.releasePointerCapture=id=>{captures.delete(id);};
  const pointer=async(type:string,x:number,y:number)=>{
   const event=new dom.window.MouseEvent(type,{button:0,clientX:x,clientY:y,bubbles:true});
   Object.defineProperty(event,'pointerId',{value:7});
   await act(async()=>canvas.dispatchEvent(event));
  };
  const size=document.querySelector<HTMLElement>('.as-canvas-size')!;
  const before=size.style.translate;
  await pointer('pointerdown',50,50);await pointer('pointermove',110,90);
  assert.notEqual(size.style.translate,before);
  assert.ok(canvas.classList.contains('is-dragging'));
  await pointer('pointerup',110,90);assert.equal(canvas.classList.contains('is-dragging'),false);
  assert.equal(captures.size,0);
  await pointer('pointerdown',50,50);await pointer('pointercancel',50,50);
  assert.equal(canvas.classList.contains('is-dragging'),false);
  assert.equal(captures.size,0);
  canvas.scrollLeft=120;canvas.scrollTop=90;
  await click(document.querySelector('[aria-label="화면에 맞추기"]')!);
  assert.equal(canvas.scrollLeft,0);assert.equal(canvas.scrollTop,0);assert.equal(size.style.translate,'0px 0px');
  assert.equal(document.querySelectorAll('.as-flow-node').length,2);
  await click(document.querySelectorAll('.as-flow-node')[1]);
  assert.match(document.querySelector('.as-node-inspector')?.textContent||'',/완료/);
 }finally{await h.close();}
});

test('optional run request can be collapsed with text retained',async()=>{
 const h=await setup('?screen=run&service=A',{},path=>path.includes('/agent?')?{...snapshot(A),request_required:false}:undefined);
 try{
  const field=document.querySelector<HTMLTextAreaElement>('[data-tour="run-input"]')!;
  await input(field,'보존할 조건');
  const details=field.closest('details')!;
  assert.equal(details.open,true);
  await act(async()=>{details.open=false;details.dispatchEvent(new dom.window.Event('toggle'));});
  await h.flush();
  assert.equal(details.open,false);
  assert.equal(field.value,'보존할 조건');
  for(let i=0;i<3;i++){
   await act(async()=>{details.open=true;details.dispatchEvent(new dom.window.Event('toggle'));});
   await act(async()=>{details.open=false;details.dispatchEvent(new dom.window.Event('toggle'));});
   await h.flush();
   assert.equal(details.open,false);assert.equal(field.value,'보존할 조건');
  }
 }finally{await h.close();}
});

test('home greets the member once and limits recent work to three entries',async()=>{
 const overviewCode=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/workspace-overview.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
 const Overview=load(overviewCode).WorkspaceOverview;
 const workflows=Array.from({length:5},(_,index)=>({id:String(index),updated_at:'2026-11-13T00:00:00Z',config:{name:'작업 '+index}}));
 const navigations:any[]=[];
 const h=await setup('',{},undefined,createElement(Overview,{memberName:'정재',workflows,loading:false,error:'',onStart:()=>{},onNavigate:(...args:any[])=>navigations.push(args),onRetry:()=>{}}));
 try{
  assert.equal(document.querySelector('h1')?.textContent,'안녕하세요, 정재님');
  assert.equal(document.querySelector('.overview-greeting'),null);
  assert.equal(document.querySelectorAll('.overview-recent-grid li').length,3);
  await click(document.querySelectorAll('.overview-recent-grid button')[2]);
  assert.deepEqual(navigations,[['workflow','2']]);
 }finally{await h.close();}
});

test('workflow sidebar contains only navigation while the upper selector preserves switching',async()=>{
 const h=await setup('?screen=workflow&service=A');
 try{
  assert.equal(document.querySelector('.workspace-sidebar [data-tour="saved-workflows"]'),null);
  assert.ok(!document.querySelector('.workspace-sidebar')?.textContent?.includes('내 워크플로우'));
  const selector=document.querySelector<HTMLDetailsElement>('.as-workflow-switcher')!;
  assert.equal(selector.open,false);
  selector.open=true;
  const choice=Array.from(selector.querySelectorAll('button')).find(button=>button.textContent?.includes('서비스 B'))!;
  await click(choice);await h.flush();
  assert.equal(new URL(location.href).searchParams.get('service'),'B');
 }finally{await h.close();}
});

test('workflow folders: create, select, move, filter, rename and delete preserve workflow actions',async()=>{
 let organization:any={revision:0,folders:[],assignments:{}};
 const h=await setup('?screen=workflows',{},(path,body)=>{
  if(path.endsWith('/agents'))return {agents:[A,B],organization};
  if(path.endsWith('/workflow_folders')){
   assert.equal(body.revision,organization.revision);
   const folders=organization.folders.map((f:any)=>({...f})),assignments={...organization.assignments};
   if(body.operation==='create')folders.push({id:'folder-1',name:body.name});
   if(body.operation==='rename')folders[0].name=body.name;
   if(body.operation==='move')for(const id of body.agent_ids)assignments[id]=body.folder_id;
   if(body.operation==='delete'){folders.splice(0);for(const id of Object.keys(assignments))delete assignments[id];}
   organization={revision:organization.revision+1,folders,assignments};return {organization};
  }
 },createElement(EntryApp));
 try{
  await click(button('폴더 만들기'));await input(field('폴더 이름'),'매일 업무');await submit(document.querySelector('.workflow-folder-form')!);
  await click(document.querySelector('input[aria-label="서비스 A 선택"]')!);await click(button('선택한 워크플로우 이동 (1)'));
  await act(async()=>{const el=field('대상 폴더');el.value='folder-1';el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  await submit(document.querySelector('.workflow-folder-form')!);await act(async()=>{const el=document.querySelector<HTMLSelectElement>('.workflow-folder-scope select')!;el.value='folder-1';el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,1);
  assert.ok(document.querySelector('button[aria-label="서비스 A 실행하기"]'));
  assert.ok(document.querySelector('button[aria-label="서비스 A 예약 관리"]'));
  await click(button('폴더 이름 변경'));await input(field('폴더 이름'),'정리 업무');await submit(document.querySelector('.workflow-folder-form')!);
  assert.match(document.querySelector('.workflow-folder-scope select')!.textContent!,/정리 업무/);
  await click(button('폴더 삭제'));await submit(document.querySelector('.workflow-folder-form')!);
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,2);
  assert.deepEqual(organization.assignments,{});
  await click(buttons().find(b=>b.getAttribute('aria-label')==='목록 새로고침')!);
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,2);
 }finally{await h.close();}
});

test('workflow folder save failure keeps dialog and selection for retry',async()=>{
 const h=await setup('?screen=workflows',{},path=>path.endsWith('/workflow_folders')?new Response(JSON.stringify({error:'다른 곳에서 폴더가 변경됐습니다.'}),{status:400}):undefined,createElement(EntryApp));
 try{
  await click(button('폴더 만들기'));await input(field('폴더 이름'),'실패 유지');await submit(document.querySelector('.workflow-folder-form')!);
  assert.equal(field('폴더 이름').value,'실패 유지');assert.match(document.querySelector('[role="alert"]')!.textContent!,/변경/);
  await click(button('취소'));assert.equal(document.querySelector('.workflow-folder-form'),null);
 }finally{await h.close();}
});

test('workflow folder pending save rejects duplicate submission and keeps the dialog open',async()=>{
 const pending=deferred();let saves=0;
 const h=await setup('?screen=workflows',{},path=>{
  if(path.endsWith('/workflow_folders')){saves++;return pending.promise;}
 },createElement(EntryApp));
 try{
  await click(button('폴더 만들기'));await input(field('폴더 이름'),'중복 방지');
  const form=document.querySelector<HTMLFormElement>('.workflow-folder-form')!;
  await submit(form);await submit(form);
  assert.equal(saves,1);assert.equal(button('취소').disabled,true);
  assert.equal(field('폴더 이름').disabled,true);
  await act(async()=>{pending.resolve({organization:{revision:1,folders:[{id:'pending-folder',name:'중복 방지'}],assignments:{}}});await tick();});
  assert.equal(document.querySelector('.workflow-folder-form'),null);assert.match(document.querySelector('.workflow-folder-scope select')!.textContent!,/중복 방지/);
 }finally{await h.close();}
});

test('workflow folder search and folder changes clear hidden selection',async()=>{
 const h=await setup('?screen=workflows',{},path=>path.endsWith('/agents')?{agents:[A,B],organization:{revision:1,folders:[{id:'f',name:'정리'}],assignments:{A:'f'}}}:undefined,createElement(EntryApp));
 try{
  await click(document.querySelector('input[aria-label="현재 목록 전체 선택"]')!);
  assert.equal(button('선택한 워크플로우 이동 (2)').disabled,false);
  await input(document.querySelector('input[aria-label="워크플로우 검색"]')!,'서비스 A');
  assert.equal(document.querySelector('.workflow-folder-move'),null);
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,1);
  await click(document.querySelector('input[aria-label="서비스 A 선택"]')!);
  await act(async()=>{const el=document.querySelector<HTMLSelectElement>('.workflow-folder-scope select')!;el.value='unfiled';el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  assert.equal(document.querySelector('.workflow-folder-move'),null);
  assert.equal(document.querySelectorAll('.entry-workflow-card').length,0);
 }finally{await h.close();}
});

test('workflow folder selection caps at 500 and releases a slot when unchecked',async()=>{
 const agents=Array.from({length:501},(_,i)=>({...A,id:'bulk-'+i,config:{...A.config,name:'대량 '+i}}));let moved:string[]=[];
 const h=await setup('?screen=workflows',{},(path,body)=>{
  if(path.endsWith('/agents'))return {agents};
  if(path.endsWith('/workflow_folders')){moved=body.agent_ids;return {organization:{revision:1,folders:[],assignments:{}}};}
 },createElement(EntryApp));
 try{
  await click(document.querySelector('input[aria-label="현재 목록에서 500개 선택"]')!);
  assert.equal(document.querySelectorAll('.workflow-card-select input:checked').length,500);
  const last=document.querySelector<HTMLInputElement>('input[aria-label="대량 500 선택"]')!;
  assert.equal(last.disabled,true);assert.ok(button('선택한 워크플로우 이동 (500)'));
  await click(document.querySelector('input[aria-label="대량 0 선택"]')!);
  assert.equal(last.disabled,false);await click(last);
  assert.equal(document.querySelectorAll('.workflow-card-select input:checked').length,500);
  await click(button('선택한 워크플로우 이동 (500)'));await submit(document.querySelector('.workflow-folder-form')!);
  assert.equal(moved.length,500);assert.ok(moved.includes('bulk-500'));assert.ok(!moved.includes('bulk-0'));
 }finally{await h.close();}
});
