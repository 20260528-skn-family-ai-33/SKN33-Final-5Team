import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSync} from 'esbuild';
import {createRequire} from 'node:module';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
const require=createRequire(import.meta.url);
const code=buildSync({stdin:{contents:"export {AuthProvider} from './app/auth'; export {AdminMonitoring} from './app/admin-monitoring'; export {WorkspaceNav} from './app/workspace-nav';",resolveDir:resolve('Frontend'),sourcefile:'monitor-test.tsx'},absWorkingDir:resolve('Frontend'),bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const mod={exports:{} as any};new Function('require','module','exports',code)(require,mod,mod.exports);
test('admin UI gates data, opens details, uses PATCH and handles denied/revoked access',async()=>{
 const names=['window','document','navigator','location','localStorage','HTMLElement','Event','IS_REACT_ACT_ENVIRONMENT'];const originals=new Map(names.map(n=>[n,Object.getOwnPropertyDescriptor(globalThis,n)])),fetchBefore=globalThis.fetch;
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/?screen=monitoring'});
 for(const n of names)Object.defineProperty(globalThis,n,{value:n==='IS_REACT_ACT_ENVIRONMENT'?true:(dom.window as any)[n],configurable:true,writable:true});
 const {createElement,act}=require('react'),{createRoot}=require('react-dom/client'),{AuthProvider,AdminMonitoring,WorkspaceNav}=mod.exports;
 const ui=createRoot(document.getElementById('root'));const calls:{url:string;init?:RequestInit}[]=[];
 let role='member';const item={id:'request-1',member_id:'member-1',member_email:'member@example.test',context:{prompt:'지난주 문의를 요약해줘',prompt_truncated:false,conversation:[{role:'user',content:'<img src=x onerror=alert(1)> 문의 요약',truncated:false},{role:'assistant',content:'어떤 기간인가요?',truncated:false}],conversation_source:'session_latest',conversation_truncated:false},kind:'generation',status:'failed',stage:'request_analysis',node:'requirements',reason_code:'invalid_model_response',reason:'필수 필드 누락',started_at:new Date().toISOString(),triage:{status:'unreviewed',note:'',revision:0},events:[{node:'requirements',status:'failed',at:new Date().toISOString()}],diagnostics:{validation:[{field:'draft.name',type:'missing'}]}};
 globalThis.fetch=async(path:any,init?:RequestInit)=>{const url=String(path);calls.push({url,init});if(url==='/api/auth/me')return Response.json({enabled:true,authenticated:true,user:{id:'admin-1',name:'Admin',email:'admin@example.test',role}});
 if(url.includes('/generation/')){if(init?.method==='PATCH')return Response.json({triage:{status:'investigating',revision:1}});return Response.json({item,history:[]});}
 return Response.json({items:[item],total:1,page:1,page_size:25,checked_at:new Date().toISOString(),summary:{requests:1,failed:1,blocked:0,affected_users:1},groups:[]});};
 const tick=()=>new Promise(r=>setImmediate(r));
 try{
 await act(async()=>{ui.render(createElement(AuthProvider,null,createElement(WorkspaceNav,{active:'monitoring',onNavigate:()=>{}}),createElement(AdminMonitoring)));await tick();});
 assert.ok(document.body.textContent?.includes('관리자 전용'));assert.equal(calls.filter(x=>x.url.startsWith('/api/admin')).length,0);assert.equal(document.querySelector('button[aria-label="모니터링"]'),null);
 role='admin';await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();await tick();});
 assert.ok(document.querySelector('button[aria-label="모니터링"]'));assert.ok(document.body.textContent?.includes('requirements'));assert.ok(document.body.textContent?.includes('member@example.test'));
 await act(async()=>{(document.querySelector('button[aria-label="request-1 상세 보기"]') as HTMLButtonElement).click();await tick();});
 assert.ok(document.body.textContent?.includes('필수 필드 누락'));assert.ok(document.body.textContent?.includes('draft.name: missing'));
 assert.ok(document.body.textContent?.includes('지난주 문의를 요약해줘'));
 const conversation=document.querySelector('.am-conversation') as HTMLDetailsElement;assert.equal(conversation.open,false);
 conversation.open=true;assert.match(conversation.textContent!,/실패 이후/);assert.match(conversation.textContent!,/어떤 기간인가요/);assert.equal(conversation.querySelector('img'),null,'user content is escaped text');
 item.member_email='';item.context.prompt='';item.context.conversation=[];
 await act(async()=>{([...document.querySelectorAll('button')].find(b=>b.textContent==='새로고침')!).click();await tick();await tick();});
 assert.match(document.body.textContent!,/회원 member-1/);assert.match(document.querySelector('.am-prompt')!.textContent!,/미기록/);assert.match(document.querySelector('.am-conversation')!.textContent!,/연결된 사용자·AI 대화가 없습니다/);
 await act(async()=>{([...document.querySelectorAll('button')].find(b=>b.textContent==='처리 내용 저장')!).click();await tick();await tick();});
 const saved=calls.find(x=>x.init?.method==='PATCH');assert.ok(saved);assert.deepEqual(JSON.parse(saved.init!.body as string),{status:'unreviewed',note:'',revision:0});
 role='member';await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});assert.ok(!document.body.textContent?.includes('필수 필드 누락'));assert.equal(document.querySelector('button[aria-label="모니터링"]'),null);
 }finally{await act(async()=>ui.unmount());dom.window.close();globalThis.fetch=fetchBefore;for(const [n,d] of originals)if(d)Object.defineProperty(globalThis,n,d);else delete (globalThis as any)[n];}
});
