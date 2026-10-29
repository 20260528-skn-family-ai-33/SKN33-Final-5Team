import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {FRONTEND_ROOT} from './paths';

const require=createRequire(new URL('../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/agent-studio.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const nodeCompiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/node-library.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',write:false,logLevel:'silent'}).outputFiles[0].text;
function load(code:string){const module={exports:{} as any};new Function('require','module','exports',code)(require,module,module.exports);return module.exports;}
let dom:JSDOM;
const globals=['window','document','navigator','location','localStorage','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','Node','NodeFilter','SVGElement','HTMLButtonElement','HTMLSelectElement','DocumentFragment','MutationObserver','Event','CustomEvent','File','FileReader','getComputedStyle','ResizeObserver','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];
const old=new Map(globals.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
// ReactDOM feature detection must see the DOM on first import.
function installDOM(search=''){
 dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost/'+search,pretendToBeVisual:true});
 for(const key of globals){if(key==='IS_REACT_ACT_ENVIRONMENT')Object.defineProperty(globalThis,key,{value:true,writable:true,configurable:true});else if(key in dom.window)Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],writable:true,configurable:true});}
 Object.defineProperty(globalThis,'ResizeObserver',{value:class{observe(){}disconnect(){}},configurable:true});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
}
installDOM();
const {createElement,act,StrictMode}=require('react');
const {createRoot}=require('react-dom/client');
const AgentStudio=load(compiled).default,NodeLibrary=load(nodeCompiled).NodeLibrary;
const originalFetch=globalThis.fetch;
const agent=(id:string,name=id,schedule:any=null)=>({id,version:1,config:{name,purpose:'동작 검증',instructions:'요청 처리',tool_ids:['read_table'],requirements:[],questions:[],unsupported:[],document_ids:[],workflow:null,schedule},model:'gpt-4.1-mini',credential_id:'cred'});
const A=agent('A','서비스 A'),B=agent('B','서비스 B');
function snapshot(a:any,tests:any[]=[]) {return {agent:a,resources:[],schedule:a.config.schedule?{active:false,spec:a.config.schedule,next_run_at:'2026-11-01T00:00:00Z'}:null,source:null,jobs:[],tests};}
const tick=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>{resolve=r;});return {promise,resolve};}
async function setup(search='',stored:Record<string,string>={},override?:(path:string,body:any)=>Promise<any>|any,component?:any){
 dom.window.close();installDOM(search);for(const [k,v] of Object.entries(stored))localStorage.setItem(k,v);
 const calls:{path:string;body:any}[]=[];
 globalThis.fetch=(async(input:any,init:any)=>{
  const path=String(input),body=init?.body?JSON.parse(init.body):undefined;calls.push({path,body});
  let value=override?await override(path,body):undefined;
  if(value===undefined){if(path==='/api/credentials')value={credentials:[{id:'cred',provider:'openai',name:'테스트 연결'}]};else if(path.endsWith('/agents'))value={agents:[A,B]};else if(path.endsWith('/nodes'))value={nodes:[],defaults:[]};else if(path.endsWith('/catalog'))value={tools:[]};else if(path.endsWith('/connection'))value={connection:null};else if(path.includes('/agent?'))value=snapshot(path.endsWith('B')?B:A);else throw Error('Unexpected API '+path);}
  return value instanceof Response?value:new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 }) as any;
 const root=createRoot(document.getElementById('root'));await act(async()=>{root.render(component||createElement(AgentStudio));await tick();});
 const flush=()=>act(async()=>{await tick();});
 return {root,calls,flush,close:async()=>{await act(async()=>{root.unmount();await new Promise(resolve=>setTimeout(resolve,0));});globalThis.fetch=originalFetch;}};
}
const buttons=()=>Array.from(document.querySelectorAll('button')) as HTMLButtonElement[];
function button(text:string){const b=buttons().find(b=>b.textContent?.trim()===text);assert.ok(b,'Missing button '+text);return b;}
async function click(b:Element){await act(async()=>{(b as HTMLElement).click();await tick();});}
function field(label:string){const l=Array.from(document.querySelectorAll('label')).find(l=>l.textContent?.startsWith(label));assert.ok(l,'Missing field '+label);const input=l.querySelector('input,textarea,select');assert.ok(input);return input as HTMLInputElement;}
async function input(el:HTMLInputElement|HTMLTextAreaElement,value:string){await act(async()=>{const proto=el.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});}
async function submit(form:HTMLFormElement){await act(async()=>{form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();});}

test('DOM: service link overrides old session and renders the requested execution screen',async()=>{
 const h=await setup('?service=A',{'a4b-builder-session':'old'});try{assert.equal(document.querySelector('h1')?.textContent,'서비스 A');assert.ok(document.querySelector('.screen-run'));assert.equal(localStorage.getItem('a4b-builder-session'),null);assert.equal(h.calls.some(c=>c.path.includes('/session?')),false);}finally{await h.close();}
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
 try{await click(button('설정 수정'));await input(field('노드 이름'),'수정한 이름');await submit(document.querySelector('form')!);const body=h.calls.find(c=>c.body)?.body;assert.equal(body.spec.name,'수정한 이름');assert.equal(body.spec.execution_plan.filters.length,2);assert.deepEqual(body.spec.execution_plan.sort,complex.spec.execution_plan.sort);assert.equal(body.spec.execution_plan.missing_policy,'keep');assert.equal(body.spec.settings.query,'preserve');}finally{await h.close();}
});
test('DOM: node save pending disables form input; duplicate submit makes one write',async()=>{
 const pending=deferred();const h=await setup('',{},(_p,body)=>body?pending.promise:undefined,createElement(NodeLibrary,libraryProps()));
 try{await click(button('설정 수정'));await submit(document.querySelector('form')!);assert.equal((document.querySelector('fieldset') as HTMLFieldSetElement).disabled,true);await submit(document.querySelector('form')!);assert.equal(h.calls.length,1);await act(async()=>{pending.resolve({node:complex});await tick();});assert.equal((document.querySelector('fieldset') as HTMLFieldSetElement).disabled,false);}finally{await h.close();}
});
test('DOM: save success with list failure offers a read-only retry instead of another mutation',async()=>{
 let reads=0;const h=await setup('',{},()=>({node:complex}),createElement(NodeLibrary,libraryProps(async()=>{if(reads++===0)throw Error('목록 오류');})));
 try{await click(button('설정 수정'));await submit(document.querySelector('form')!);assert.match(document.querySelector('[role=alert]')?.textContent||'',/저장은 완료/);await click(button('저장한 노드 목록 다시 불러오기'));assert.equal(h.calls.length,1);assert.equal(document.querySelector('[role=alert]'),null);}finally{await h.close();}
});
test.after(()=>{dom.window.close();globalThis.fetch=originalFetch;for(const [key,descriptor] of old){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete (globalThis as any)[key];}});

test('DOM: startup API failure is visible on the node-library screen too',async()=>{
 const h=await setup('?screen=nodes',{},path=>path.endsWith('/catalog')?new Response(JSON.stringify({error:'카탈로그 연결 오류'}),{status:503}):undefined);
 try{assert.match(document.querySelector('[role=alert]')?.textContent||'',/카탈로그 연결 오류/);}finally{await h.close();}
});
function readySession(){return {id:'ready',revision:1,status:'ready',base_version:0,messages:[{role:'user',content:'합계를 계산해줘'},{role:'assistant',content:'설정 확인'}],draft:{...A.config,explanation:'설정 확인'},resources:[],trace:[],issues:[]};}
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
 try{await click(button('설정 수정'));await input(field('노드 이름'),'보존할 이름');await submit(document.querySelector('form')!);assert.equal(field('노드 이름').value,'보존할 이름');assert.match(document.querySelector('[role=alert]')?.textContent||'',/기준 버전/);await submit(document.querySelector('form')!);assert.equal(writes,2);}finally{await h.close();}
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
  try{await until(()=>document.querySelector('h1')?.textContent==='브라우저 검증 - 합계');const priorTitle=document.querySelector('.as-result-title')?.textContent;await click(button('에이전트 실행'));await until(()=>!!document.querySelector('.as-result-title')&&!document.querySelector('.as-busy'));assert.notEqual(document.querySelector('.as-result-title')?.textContent,priorTitle);assert.match(document.querySelector('.as-result-title')?.textContent||'',/실행 완료/);const cells=Array.from(document.querySelectorAll('.as-test-result td')).map(c=>c.textContent);assert.ok(cells.includes('30'));assert.ok(cells.includes('40'));const reportLink=document.querySelector('a[href*="report?id="]') as HTMLAnchorElement|null;
   let payload:any[];
   if(reportLink){const response=await originalFetch(base+reportLink.getAttribute('href'));assert.equal(response.status,200);assert.match(response.headers.get('Content-Disposition')||'',/attachment/);payload=await response.json() as any[];}
   else{const downloadButton=buttons().find(b=>b.textContent?.includes('결과 다운로드'));assert.ok(downloadButton);const downloads:{url:string;name:string}[]=[];dom.window.HTMLAnchorElement.prototype.click=function(){downloads.push({url:this.href,name:this.download});};await click(downloadButton);assert.equal(downloads.length,1);assert.match(downloads[0].name,/json$/);payload=await (await originalFetch(downloads[0].url)).json() as any[];}
   assert.equal(payload.length,2);assert.ok(payload.some(row=>Object.values(row).includes(30)));assert.ok(payload.some(row=>Object.values(row).includes(40)));}finally{await h.close();}
 });
 test('DOM + real API: missing input shows preflight error without a false completion',async()=>{
  const h=await setup('?service='+ids['브라우저 검증 - 파일 필요'],{},live);
  try{await until(()=>document.querySelector('h1')?.textContent==='브라우저 검증 - 파일 필요');await click(button('에이전트 실행'));await until(()=>!!document.querySelector('[role=alert]'));assert.match(document.querySelector('[role=alert]')?.textContent||'',/자료|표|파일/);assert.equal(document.querySelector('.as-result-title'),null);}finally{await h.close();}
 });
 test('DOM + real API: paused schedule runs now using its managed source',async()=>{
  const h=await setup('?service='+ids['브라우저 검증 - 예약'],{},live);
  try{await until(()=>!!document.querySelector('.as-schedule-card'));assert.equal(document.querySelector('.as-run-files'),null);const priorTitle=document.querySelector('.as-result-title')?.textContent;await click(button('지금 실행'));await until(()=>!!document.querySelector('.as-result-title')&&!document.querySelector('.as-busy'));assert.notEqual(document.querySelector('.as-result-title')?.textContent,priorTitle);assert.match(document.querySelector('.as-result-title')?.textContent||'',/실행 완료/);const cells=Array.from(document.querySelectorAll('.as-test-result td')).map(c=>c.textContent);assert.ok(cells.includes('30'));assert.ok(cells.includes('40'));}finally{await h.close();}
 });
 test('DOM + real API: complex node creates a new version preserving both filters and sorting',async()=>{
  const h=await setup('?screen=nodes',{},live);
  try{await until(()=>!!Array.from(document.querySelectorAll('.nl-card')).find(c=>c.textContent?.includes('복합 필터 보존 검증')));const card=Array.from(document.querySelectorAll('.nl-card')).find(c=>c.textContent?.includes('복합 필터 보존 검증'))!;await click(card.querySelector('button')!);await input(field('노드 이름'),'복합 필터 보존 검증');await submit(document.querySelector('.nl-form')!);await until(()=>!!document.querySelector('.nl-notice')&&!(document.querySelector('fieldset') as HTMLFieldSetElement).disabled);const response=await originalFetch(base+'/api/agent-builder/nodes');const result=await response.json() as any;const saved=result.nodes.find((n:any)=>n.id===ids.complex_node);assert.equal(saved.spec.execution_plan.filters.length,2);assert.equal(saved.spec.execution_plan.sort[0].direction,'descending');assert.equal(saved.spec.execution_plan.missing_policy,'keep');assert.ok(saved.version>=2);}finally{await h.close();}
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
