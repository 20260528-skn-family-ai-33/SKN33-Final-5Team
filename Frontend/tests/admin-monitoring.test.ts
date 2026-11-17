import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSync} from 'esbuild';
import {createRequire} from 'node:module';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const code=buildSync({stdin:{contents:"export {AuthProvider} from './app/auth'; export {AdminMonitoring} from './app/admin-monitoring'; export {WorkspaceNav} from './app/workspace-nav';",resolveDir:resolve('Frontend'),sourcefile:'monitor-test.tsx'},absWorkingDir:resolve('Frontend'),bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
test('admin UI gates data, opens details, uses PATCH and handles denied/revoked access',async()=>{
 const names=['window','document','navigator','location','localStorage','HTMLElement','HTMLInputElement','HTMLButtonElement','Element','Node','NodeFilter','SVGElement','DocumentFragment','MutationObserver','Event','CustomEvent','MouseEvent','KeyboardEvent','getComputedStyle','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];const originals=new Map(names.map(n=>[n,Object.getOwnPropertyDescriptor(globalThis,n)])),fetchBefore=globalThis.fetch;
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/?screen=monitoring'});
 for(const n of names)Object.defineProperty(globalThis,n,{value:n==='IS_REACT_ACT_ENVIRONMENT'?true:(dom.window as any)[n],configurable:true,writable:true});
 const mod={exports:{} as any};new Function('require','module','exports',code)(require,mod,mod.exports);
 const {createElement,act}=require('react'),{createRoot}=require('react-dom/client'),{AuthProvider,AdminMonitoring,WorkspaceNav}=mod.exports;
 const ui=createRoot(document.getElementById('root'));const calls:{url:string;init?:RequestInit}[]=[];
 let role='member',delayDetail=false,delaySave=false;let detailResolve:((value:Response)=>void)|undefined,saveResolve:((value:Response)=>void)|undefined;const item={id:'request-1',member_id:'member-1',member_email:'member@example.test',context:{prompt:'지난주 문의를 요약해줘',prompt_truncated:false,prompt_storage_limit:4000,prompt_storage_truncated:null as boolean|null,conversation:[{role:'user',content:'<img src=x onerror=alert(1)> 문의 요약',truncated:false},{role:'assistant',content:'어떤 기간인가요?',truncated:false}],conversation_source:'session_latest',conversation_truncated:false},kind:'generation',status:'failed',stage:'request_analysis',node:'requirements',reason_code:'invalid_model_response',reason:'필수 필드 누락',started_at:new Date().toISOString(),triage:{status:'unreviewed',note:'',revision:0},events:[{node:'requirements',status:'failed',at:new Date().toISOString()}],diagnostics:{validation:[{field:'draft.name',type:'missing'}]}};
 globalThis.fetch=async(path:any,init?:RequestInit)=>{const url=String(path);calls.push({url,init});if(url==='/api/auth/me')return Response.json({enabled:true,authenticated:true,user:{id:'admin-1',name:'Admin',email:'admin@example.test',role}});
 if(url.includes('/generation/')){if(init?.method==='PATCH'&&delaySave)return new Promise<Response>(resolve=>{saveResolve=resolve;});if(init?.method!=='PATCH'&&delayDetail)return new Promise<Response>(resolve=>{detailResolve=resolve;});if(init?.method==='PATCH')return Response.json({triage:{status:'investigating',revision:1}});return Response.json({item,history:[]});}
 return Response.json({items:[item],total:1,page:1,page_size:25,checked_at:new Date().toISOString(),summary:{requests:1,failed:1,blocked:0,affected_users:1},groups:[]});};
 const tick=()=>new Promise(r=>setImmediate(r));
 try{
 await act(async()=>{ui.render(createElement(AuthProvider,null,createElement(WorkspaceNav,{active:'monitoring',onNavigate:()=>{}}),createElement(AdminMonitoring)));await tick();});
 assert.ok(document.body.textContent?.includes('관리자 전용'));assert.equal(calls.filter(x=>x.url.startsWith('/api/admin')).length,0);assert.equal(document.querySelector('button[aria-label="모니터링"]'),null);
 role='admin';await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null);
 for(const name of ['요청 목록 스크롤','실패 집계 스크롤'])assert.ok(document.querySelector('[role=region][aria-label="'+name+'"][tabindex="0"]'));
 assert.equal(document.querySelector('.am-subtitle'),null);
 assert.ok(document.querySelector('button[aria-label="모니터링"]'));assert.ok(document.body.textContent?.includes('요구사항 정리'));assert.ok(document.body.textContent?.includes('member@example.test'));
 for(const label of ['검색','이전 페이지','다음 페이지']){
  const button=document.querySelector('button[aria-label="'+label+'"]') as HTMLButtonElement;
  assert.ok(button.classList.contains('workspace-icon-button'));
  assert.ok(button.classList.contains('is-standard'));assert.ok(button.classList.contains('is-outline'));
  if(label!=='검색')assert.equal(button.disabled,true,'single-page navigation stays disabled');
 }
 await act(async()=>{(document.querySelector('button[aria-label="request-1 상세 보기"]') as HTMLButtonElement).click();await tick();});
 assert.ok(document.querySelector('[role=dialog]'));assert.ok(document.querySelector('[aria-label="요청 상세 스크롤"]'));
 assert.ok(document.body.textContent?.includes('필수 필드 누락'));assert.ok(document.body.textContent?.includes('draft.name: missing'));
 assert.ok(document.body.textContent?.includes('지난주 문의를 요약해줘'));
 assert.match(document.querySelector('.am-storage-limit')!.textContent!,/4,000.*생략 여부 미기록/);
 for(const truncated of [true,false]){
  item.context.prompt_storage_truncated=truncated;item.context.prompt_truncated=truncated;
  await act(async()=>{(document.querySelector('button[aria-label="요청 상세 닫기"]') as HTMLButtonElement).click();await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null);
 await act(async()=>{(document.querySelector('button[aria-label="request-1 상세 보기"]') as HTMLButtonElement).click();await tick();await tick();});
  assert.match(document.querySelector('.am-storage-limit')!.textContent!,truncated?/나머지 생략/:/전체/);
 }
 const conversation=document.querySelector('.am-conversation') as HTMLDetailsElement;assert.equal(conversation.open,false);
 conversation.open=true;assert.match(conversation.textContent!,/현재 저장된 대화/);assert.match(conversation.textContent!,/어떤 기간인가요/);assert.equal(conversation.querySelector('img'),null,'user content is escaped text');
 item.member_email='';item.context.prompt='';item.context.conversation=[];
 await act(async()=>{(document.querySelector('button[aria-label="요청 상세 닫기"]') as HTMLButtonElement).click();await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null);
 await act(async()=>{(document.querySelector('button[aria-label="request-1 상세 보기"]') as HTMLButtonElement).click();await tick();await tick();});
 assert.match(document.body.textContent!,/회원 IDmember-1/);assert.match(document.querySelector('.am-prompt')!.textContent!,/미기록/);assert.match(document.querySelector('.am-conversation')!.textContent!,/연결된 사용자·AI 대화가 없습니다/);
 await act(async()=>{([...document.querySelectorAll('button')].find(b=>b.textContent==='처리 내용 저장')!).click();await tick();await tick();});
 const saved=calls.find(x=>x.init?.method==='PATCH');assert.ok(saved);assert.deepEqual(JSON.parse(saved.init!.body as string),{status:'unreviewed',note:'',revision:0});
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="요청 상세 닫기"]')!.click();await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null);
 delayDetail=true;
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="request-1 상세 보기"]')!.click();await tick();});
 assert.ok(document.querySelector('[role=dialog]'));
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="요청 상세 닫기"]')!.click();await tick();});
 assert.ok(calls.filter(c=>c.url.includes('/generation/')).at(-1)!.init!.signal!.aborted);
 await act(async()=>{detailResolve!(Response.json({item,history:[]}));await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null,'late details never reopen a closed dialog');delayDetail=false;
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="request-1 상세 보기"]')!.click();await tick();});
 delaySave=true;
 await act(async()=>{([...document.querySelectorAll('button')].find(b=>b.textContent==='처리 내용 저장')!).click();await tick();});
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="요청 상세 닫기"]')!.click();await tick();});
 await act(async()=>{saveResolve!(Response.json({triage:{status:'unreviewed',revision:2}}));await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null,'late save never reopens a closed dialog');delaySave=false;
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="request-1 상세 보기"]')!.click();await tick();});
 await act(async()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));await tick();});
 assert.equal(document.querySelector('[role=dialog]'),null);
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
 assert.equal(document.activeElement?.getAttribute('aria-label'),'request-1 상세 보기');
 await act(async()=>{document.querySelector<HTMLButtonElement>('[aria-label="request-1 상세 보기"]')!.click();await tick();});
 role='member';await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});assert.ok(!document.body.textContent?.includes('필수 필드 누락'));assert.equal(document.querySelector('button[aria-label="모니터링"]'),null);
 }finally{await act(async()=>ui.unmount());dom.window.close();globalThis.fetch=fetchBefore;for(const [n,d] of originals)if(d)Object.defineProperty(globalThis,n,d);else delete (globalThis as any)[n];}
});
