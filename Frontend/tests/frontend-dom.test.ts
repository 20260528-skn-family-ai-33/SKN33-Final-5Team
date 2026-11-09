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
function load(code:string){const module={exports:{} as any};new Function('require','module','exports',code)(require,module,module.exports);return module.exports;}
let dom:JSDOM;
const globals=['window','document','navigator','location','localStorage','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','Node','NodeFilter','SVGElement','HTMLButtonElement','HTMLSelectElement','DocumentFragment','MutationObserver','Event','CustomEvent','File','FileReader','getComputedStyle','ResizeObserver','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];
const old=new Map(globals.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
// ReactDOM feature detection must see the DOM on first import.
function installDOM(search=''){
 dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost/'+search,pretendToBeVisual:true});
 for(const key of globals){if(key==='IS_REACT_ACT_ENVIRONMENT')Object.defineProperty(globalThis,key,{value:true,writable:true,configurable:true});else if(key in dom.window)Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],writable:true,configurable:true});}
 dom.window.localStorage.setItem('a4b-product-tour-v1','done');
 Object.defineProperty(globalThis,'ResizeObserver',{value:class{observe(){}disconnect(){}},configurable:true});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};dom.window.scrollTo=()=>{};
}
installDOM();
const {createElement,act,StrictMode}=require('react');
const {createRoot}=require('react-dom/client');
const EntryApp=load(entryCompiled).default;
const AgentStudio=load(compiled).default,NodeLibrary=load(nodeCompiled).NodeLibrary;
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
  if(value===undefined){if(path==='/api/credentials')value={credentials:[{id:'cred',provider:'openai',name:'테스트 연결'}]};else if(path.endsWith('/agents'))value={agents:[A,B]};else if(path.endsWith('/nodes'))value={nodes:[],defaults:[]};else if(path.endsWith('/catalog'))value={tools:[]};else if(/\/library(?:\?|$)/.test(path))value={space:{id:'personal',kind:'personal',name:'내 자료'},folder_id:null,folders:[],assets:[],next:null};else if(path.includes('/connection'))value={connection:{model:'qwen3:8b-q4_K_M',credential:{id:'local-model',provider:'openai',name:'Ollama · Qwen'}},health:{status:'ready',message:'Qwen 서버 응답 확인 · 모델 준비됨'}};else if(path.includes('/agent?'))value=snapshot(path.endsWith('B')?B:A);else throw Error('Unexpected API '+path);}
  return value instanceof Response?value:new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 }) as any;
 const root=createRoot(document.getElementById('root'));await act(async()=>{root.render(component||createElement(AgentStudio));await tick();});
 const flush=()=>act(async()=>{await tick();});
 return {root,calls,flush,close:async()=>{await act(async()=>{root.unmount();await new Promise(resolve=>setTimeout(resolve,0));});globalThis.fetch=originalFetch;}};
}
const buttons=()=>Array.from(document.querySelectorAll('button')) as HTMLButtonElement[];
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
  for(const target of [button('워크플로우'),document.querySelector('[aria-label="서비스 소개로 이동"]')!,button('새 워크플로우')]){
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
  assert.equal(location.search,'?screen=workflows');
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
  assert.match(cards[1].textContent||'',/읽어 낸 글자가 없어요/);
  assert.equal(cards[1].querySelector('input[type=checkbox]'),null);
  await click(button('읽은 내용 전체 보기'));
  assert.equal(document.querySelector('.as-resource-dialog .as-preserve')?.textContent,text);
 }finally{await h.close();}
});
test('DOM: a first upload offers observed file values before the first chat message',async()=>{
 const file={id:'uploaded',name:'접수.txt',kind:'document',has_readable_text:true,columns:[],fields:[{label:'접수번호',value:'A-120'}],snippets:['접수번호: A-120'],preview:[]};
 const h=await setup('',{},path=>{
  if(path.endsWith('/sessions'))return {id:'new',revision:0};
  if(path.endsWith('/upload'))return {...readySession(),id:'new',revision:1,messages:[],draft:null,status:'empty',file_inspection:[file]};
 });
 try{
  await click(button('자료 연결'));await click(button('새 파일 올리기'));
  const picker=document.querySelector('input[type=file]') as HTMLInputElement;
  Object.defineProperty(picker,'files',{value:[new dom.window.File(['접수번호: A-120'],'접수.txt')]});
  await act(async()=>{picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  const deadline=Date.now()+3000;
  while(!document.querySelector('.as-inspected-source')){assert.ok(Date.now()<deadline);await h.flush();}
  const card=document.querySelector('.as-inspected-source') as HTMLDetailsElement;assert.ok(card.open);
  assert.match(card.textContent||'',/접수번호: A-120/);await click(field('접수번호'));await click(button('요청에 추가'));
  assert.equal((document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement).value,'접수.txt에서 접수번호 항목을 추출해 주세요.');
  assert.equal(h.calls.some(c=>c.path.endsWith('/message')),false);
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
  assert.ok(button('실행 완료 후 삭제').disabled && button('예약 소스 교체 후 삭제').disabled);
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
test('DOM: receipt clarification uploads a file and submits only chosen fields to its pending question',async()=>{
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
  assert.deepEqual(answers,[{id:'ready',revision:2,content:'영수증에서 추출할 항목: 날짜, 결제 금액, 승인번호',questionId:fields.id}]);
 }finally{await h.close();}
});
for(const [name,withSummary,itemColumns] of [
 ['items only',false,['상품코드','금액']],['summary only',true,[]],['summary and items',true,['상품코드','상품명','단가','수량','금액']],
] as const) test('DOM: grounded Excel receipt submits '+name+' to the current question',async()=>{
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
   [{id:'ready',revision:1,questionId:question.id,content:lines.join('\n')}]);
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
 try{await click(button('초안 확인 · 저장'));assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.ok(document.querySelector('.screen-run'));assert.match(document.querySelector('[role=alert]')?.textContent||'',/저장은 완료/);}finally{await h.close();}
});
test('DOM: service-detail failure after saving offers reload without another confirm',async()=>{
 let details=0;
 const h=await setup('',{'a4b-builder-session':'ready'},path=>{
  if(path.includes('/session?'))return readySession();
  if(path.endsWith('/confirm'))return {...readySession(),status:'saved',saved_agent_id:'A'};
  if(path.includes('/agent?')&&details++===0)return new Response(JSON.stringify({error:'서비스 조회 오류'}),{status:503});
 });
 try{await click(button('초안 확인 · 저장'));await click(button('저장한 서비스 다시 불러오기'));assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,1);}finally{await h.close();}
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


test('entry: service introduction is the default, then opens the actual workflow list',async()=>{
 const t=await setup('',{},undefined,createElement(EntryApp));
 try{
  assert.match(document.body.textContent||'',/업무를 말하세요/);
  assert.equal(t.calls.length,0,'landing must not require a backend or model');
  await click(button('A4B 시작하기'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  assert.ok(document.body.textContent?.includes('서비스 A'));
  assert.ok(document.body.textContent?.includes('서비스 B'));
  await input(document.querySelector('input[aria-label="워크플로우 검색"]')!,'없는 작업');
  assert.ok(document.body.textContent?.includes('검색 결과가 없습니다.'));
 }finally{await t.close();}
});
test('entry: scrolling intro keeps every link on the existing routes without API calls',async()=>{
 const cases:[string,string,string|null][]=[['워크플로우 열기','workflows',null],['제품 살펴보기','preview',null],['작업 라이브러리','nodes',null],['워크플로우 목록','workflows',null],['실행 기록','history',null],['워크플로우 시작하기','workflows',null]];
 for(const [label,screen,node] of cases){
  const t=await setup('?screen=intro',{'a4b-builder-session':'kept'},undefined,createElement(EntryApp));
  try{
   assert.ok(document.querySelector('#a4b-how')&&document.querySelector('#a4b-usecases')&&document.querySelector('#a4b-product-preview'),'scroll sections');
   assert.equal(t.calls.length,0,'intro must not call the backend');
   await click(button(label));await t.flush();
   const params=new URL(location.href).searchParams;
   assert.equal(params.get('screen'),screen,label);assert.equal(params.get('previewNode'),node,label);
   assert.equal(localStorage.getItem('a4b-builder-session'),'kept','intro links never reset a draft');
  }finally{await t.close();}
 }
 const n=await setup('?screen=intro',{},undefined,createElement(EntryApp));
 try{await click(document.querySelector('.entry-preview-node.ai') as HTMLElement);await n.flush();const params=new URL(location.href).searchParams;assert.equal(params.get('screen'),'preview');assert.equal(params.get('previewNode'),'ai');assert.ok(document.body.textContent?.includes('AI 작업 설정'));}finally{await n.close();}
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
  await click(button('새 워크플로우'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.ok(document.querySelector('.as-composer'));
  await click(button('목록으로'));await t.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  await act(async()=>{window.history.replaceState(null,'','?screen=intro');window.dispatchEvent(new dom.window.PopStateEvent('popstate'));await tick();});
  assert.ok(document.body.textContent?.includes('업무를 말하세요.'));
 }finally{await t.close();}
});
test('entry: old model URLs redirect to workflows, and deep links keep their exact run',async()=>{
 const t=await setup('?screen=models',{},undefined,createElement(EntryApp));
 try{
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
  assert.ok(!document.body.textContent?.includes('Qwen'));
  assert.equal(document.querySelectorAll('input[type=password]').length,0);
  assert.ok(document.querySelector('h1')?.textContent==='워크플로우');
  assert.ok(!t.calls.some(call=>call.path==='/api/credentials'));
 }finally{await t.close();}
 const older={id:'old-run',status:'succeeded',answer:'이전 실행 결과',error:null,steps:[],rows:[],artifact:null,agent_version:1};
 const latest={...older,id:'latest-run',answer:'최신 실행 결과'};
 const r=await setup('?screen=run&service=A&run=old-run',{},async path=>path.includes('/agent?')?snapshot(A,[latest,older]):undefined,createElement(EntryApp));
 try{assert.ok(document.body.textContent?.includes('이전 실행 결과'));assert.ok(!document.body.textContent?.includes('최신 실행 결과'));}finally{await r.close();}
});

test('entry: ZIP preview is separate from real workflows, preserves storage, and has no API side effects',async()=>{
 const t=await setup('',{'a4b-selected-agent':'A','a4b-builder-session':'remembered'},undefined,createElement(EntryApp));
 try{
  assert.equal(document.querySelectorAll('.entry-preview-steps').length,0);
  assert.equal(document.querySelectorAll('.entry-preview-node:is(button)').length,1);
  await click(button('편집기 미리보기'));await t.flush();
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
 const n=await setup('',{},undefined,createElement(EntryApp));
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
  await click(button('목록으로'));assert.equal(prompts,1);assert.ok(document.querySelector('.as-composer'));
  window.confirm=()=>true;await click(button('목록으로'));await n.flush();
  assert.equal(new URL(location.href).searchParams.get('screen'),'workflows');
 }finally{await n.close();}
});

test('navigation: internal back/forward preserves unsent workflow text and avoids duplicate startup loads',async()=>{
 const t=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 const travel=async(direction:'back'|'forward')=>act(async()=>{window.history[direction]();await new Promise(r=>setTimeout(r,35));});
 try{
  await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'탭 이동 후 유지할 업무');
  const loads=t.calls.filter(c=>c.path.endsWith('/catalog')).length;
  await click(button('작업 라이브러리'));assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  await click(button('설계로 돌아가기'));await click(button('실행'));assert.equal(new URL(location.href).searchParams.get('screen'),'run');
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
 const t=await setup('?screen=nodes',{},path=>path.endsWith('/nodes')?{nodes:[complex],defaults:[]}:undefined,createElement(EntryApp));
 try{
  await click(button('설정 수정'));await input(field('작업 이름'),'미저장 작업');
  await click(button('설계로 돌아가기'));await click(button('작업 라이브러리'));
  assert.equal(field('작업 이름').value,'미저장 작업');
  window.confirm=()=>false;await click(button('워크플로우'));
  assert.equal(new URL(location.href).searchParams.get('screen'),'nodes');
  assert.equal(field('작업 이름').value,'미저장 작업');
 }finally{await t.close();}
});

test('navigation: cancelling browser back from a dirty editor restores the URL and input',async()=>{
 const t=await setup('?screen=workflows',{},undefined,createElement(EntryApp));
 try{
  await click(button('새 워크플로우'));await t.flush();
  await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'뒤로 가기로 잃으면 안 되는 입력');
  let prompts=0;window.confirm=()=>{prompts++;return false;};
  await act(async()=>{window.history.back();await new Promise(r=>setTimeout(r,100));});
  assert.equal(prompts,1);assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');
  assert.equal((document.querySelector('textarea[aria-label="업무 설명"]') as HTMLTextAreaElement).value,'뒤로 가기로 잃으면 안 되는 입력');
  const event=new dom.window.Event('beforeunload',{cancelable:true});window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 }finally{await t.close();}
});

test('redesign: infrastructure controls and model fields are absent from all product menus',async()=>{
 for(const screen of ['workflows','workflow','nodes','history']){
  const h=await setup('?screen='+screen,{},undefined,createElement(EntryApp));
  try{assert.ok(!document.body.textContent?.includes('Qwen'));assert.equal(document.querySelectorAll('input[type=password]').length,0);assert.deepEqual(Array.from(document.querySelectorAll('.workspace-nav button')).map(b=>b.textContent),['워크플로우','실행 기록','작업 라이브러리','자료함','서비스 연결']);assert.ok(!Array.from(document.querySelectorAll('label')).some(l=>l.textContent?.startsWith('모델')));}finally{await h.close();}
 }
});
test('redesign: changing a saved workflow can be cancelled without losing an unsent request',async()=>{
 const h=await setup('?screen=workflow');
 try{await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'보존할 입력');window.confirm=()=>false;await click(Array.from(document.querySelectorAll('.as-agent-link')).find(b=>b.querySelector('strong')?.textContent==='서비스 B')!);assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'보존할 입력');assert.ok(!h.calls.some(c=>c.path.includes('/agent?id=B')));}finally{await h.close();}
});
test('redesign: mobile panel choices keep the composer value and expose exactly the selected area',async()=>{
 const h=await setup('?screen=workflow');
 try{await input(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!,'편집 영역을 바꿔도 보존');const nav=document.querySelector('.as-panel-switcher')!;await click(Array.from(nav.querySelectorAll('button')).find(b=>b.textContent==='흐름 확인')!);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(Array.from(nav.querySelectorAll('button')).find(b=>b.textContent==='설정 · 저장')!);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'편집 영역을 바꿔도 보존');}finally{await h.close();}
});
test('redesign: library hides manual creation and preserves saved-node edits',async()=>{
 const h=await setup('?screen=nodes',{},undefined,createElement(NodeLibrary,libraryProps()));
 try{assert.equal(document.querySelector('form'),null);assert.ok(!document.body.textContent?.includes('지원 기능'));await click(button('설정 수정'));await input(field('작업 이름'),'보존할 이름');window.confirm=()=>false;await click(button('설정 수정'));assert.equal(field('작업 이름').value,'보존할 이름');assert.ok(!Array.from(document.querySelectorAll('label')).some(l=>l.textContent?.startsWith('역할과 목적')));assert.equal(document.querySelector('[aria-label="작업 종류"]'),null);}finally{await h.close();}
});

test('workspace shell: brand, help and create action keep identical markup across all product screens',async()=>{
 let header='';
 for(const screen of ['workflows','history','nodes','workflow','run','assets','connections']){
  const h=await setup('?screen='+screen,{},undefined,createElement(EntryApp));
  try{const current=document.querySelector('.workspace-header')!.innerHTML.replace(/radix-[^" ]+/g,'radix-id');if(header)assert.equal(current,header);else header=current;assert.equal(document.querySelectorAll('.workspace-brand').length,1);assert.equal(buttons().filter(b=>b.textContent?.trim()==='새 워크플로우').length,1);assert.equal(document.querySelectorAll('.workspace-nav [aria-current=page]').length,1);await click(button('사용 안내'));assert.ok(document.querySelector('[role=dialog]')?.textContent?.includes('화면 둘러보기'));}finally{await h.close();}
 }
});
test('workspace library: pending startup does not show zero counts or an editable empty form',async()=>{
 const waiting=deferred();const h=await setup('?screen=nodes',{},path=>path.endsWith('/catalog')?waiting.promise:undefined,createElement(EntryApp));
 try{assert.match(document.querySelector('.workspace-loading')!.textContent!,/불러오고/);assert.ok(!document.querySelector('form[aria-label="작업 설정"]'));assert.ok(!document.body.textContent?.includes('지원 기능 0종'));assert.equal(button('대화로 만들기').disabled,true);await act(async()=>{waiting.resolve({tools:[]});await tick();});assert.equal(document.querySelector('form[aria-label="작업 설정"]'),null);assert.equal(button('대화로 만들기').disabled,false);}finally{await h.close();}
});
test('workspace library: failed initial fetch is distinct from an empty library',async()=>{
 const h=await setup('?screen=nodes',{},path=>{if(path.endsWith('/catalog'))throw Error('카탈로그 불러오기 실패');},createElement(EntryApp));
 try{assert.match(document.querySelector('[role=alert]')!.textContent!,/불러오기 실패/);assert.match(document.querySelector('.workspace-loading')!.textContent!,/불러오지 못했습니다/);assert.ok(!document.querySelector('form[aria-label="작업 설정"]'));assert.ok(!document.body.textContent?.includes('대화로 워크플로우를 만들거나'));}finally{await h.close();}
});

test('product tour: next and previous follow real controls without changing an unsent request or executing work',async()=>{
 const h=await setup('?screen=workflow',{},undefined,createElement(EntryApp));
 try{await input(document.querySelector('textarea[aria-label="업무 설명"]')!,'안내 후에도 남아야 하는 요청');await click(button('사용 안내'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/1 \/ 5/);await click(button('다음'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/필요한 파일/);await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(button('이전'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'conversation');await click(button('다음'));await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');await click(button('다음'));await click(button('안내 마치기'));assert.equal(document.querySelector('[role=dialog]'),null);assert.equal(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="업무 설명"]')!.value,'안내 후에도 남아야 하는 요청');assert.ok(!h.calls.some(c=>c.body));assert.equal(localStorage.getItem('a4b-product-tour-v1'),'done');}finally{await h.close();}
});
test('product tour: first use opens once, skip is remembered, and help can restart it',async()=>{
 const h=await setup('?screen=workflows',{'a4b-product-tour-v1':''},undefined,createElement(EntryApp));
 try{assert.ok(document.querySelector('[role=dialog]'));await click(button('나중에 보기'));assert.equal(document.querySelector('[role=dialog]'),null);await click(button('작업 라이브러리'));await h.flush();assert.equal(document.querySelector('[role=dialog]'),null);await click(button('사용 안내'));assert.ok(document.querySelector('[role=dialog]')?.textContent?.includes('대화로'));await click(document.querySelector('[aria-label="안내 닫기"]')!);assert.equal(document.querySelector('[role=dialog]'),null);}finally{await h.close();}
});
test('product tour: finishing the list tour starts an empty design and continues its controls, without a model request',async()=>{
 const h=await setup('?screen=workflows',{'a4b-product-tour-v1':''},undefined,createElement(EntryApp));
 try{for(let i=0;i<3;i++)await click(button('다음'));await click(button('설계 시작'));await h.flush();assert.equal(new URL(location.href).searchParams.get('screen'),'workflow');assert.match(document.querySelector('[role=dialog]')!.textContent!,/할 일과 원하는 결과/);assert.ok(!h.calls.some(c=>c.body));}finally{await h.close();}
});

test('product tour: a saved workflow explains review and explicit editing, without pretending its summary is editable',async()=>{
 const h=await setup('?screen=workflow&service=A',{},undefined,createElement(EntryApp));
 try{await click(button('사용 안내'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/저장한 업무 내용을 확인/);assert.equal(document.querySelector('.as-composer'),null);await click(button('다음'));await click(button('다음'));assert.match(document.querySelector('[role=dialog]')!.textContent!,/이 흐름이 사용하는 설정/);await click(button('다음'));assert.equal(document.querySelector('.product-tour-card')?.getAttribute('data-tour-target'),'edit-workflow');assert.ok(!h.calls.some(c=>c.body));}finally{await h.close();}
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
  await click(button('설정하기'));await input(field('작업 이름'),'보존할 작업');await click(button('사용 안내'));
  const targets=['preset-configure','preset-use','create-task','saved-tasks'];
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
 try{await click(button('흐름 확인'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');await click(button('사용 안내'));await click(button('다음'));await click(button('다음'));await click(button('다음'));assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'settings');await act(async()=>{document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();});assert.equal(document.querySelector('[role=dialog]'),null);assert.equal(document.querySelector('.as-editor-grid')?.getAttribute('data-panel'),'canvas');}finally{await h.close();}
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
