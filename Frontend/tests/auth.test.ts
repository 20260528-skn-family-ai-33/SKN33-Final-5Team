import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSync} from 'esbuild';
import {createRequire} from 'node:module';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(new URL('../../package.json',import.meta.url));
const code=buildSync({stdin:{contents:"export * from './app/auth'; import {AuthProvider} from './app/auth'; import EntryApp from './app/entry-app'; import {createElement} from 'react'; export function AppUnderTest(){return createElement(AuthProvider,null,createElement(EntryApp));}",resolveDir:root,sourcefile:'auth-test-entry.tsx'},absWorkingDir:root,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const module={exports:{} as any};new Function('require','module','exports',code)(require,module,module.exports);
const {AuthProvider,AccountMenu,SignIn,useAuth,loginUrl,AppUnderTest}=module.exports;
const names=['window','document','navigator','location','localStorage','HTMLElement','Event','CustomEvent','IS_REACT_ACT_ENVIRONMENT'];
const original=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)])),originalFetch=globalThis.fetch;
let dom:JSDOM;
function install(){dom=new JSDOM('<div id="root"></div>',{url:'http://localhost:3030/?screen=workflow',pretendToBeVisual:true});for(const name of names)Object.defineProperty(globalThis,name,{value:name==='IS_REACT_ACT_ENVIRONMENT'?true:(dom.window as any)[name],configurable:true,writable:true});}
install();const {createElement,act,useState,useEffect}=require('react'),{createRoot}=require('react-dom/client');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
let starts=0,stops=0;
function PrivateArea(){useEffect(()=>{starts++;return()=>{stops++;};},[]);const [draft]=useState('private draft');return createElement('p',null,draft);}
function App(){const auth=useAuth();return createElement('div',null,createElement(AccountMenu),auth.loading||auth.enabled&&!auth.user?createElement(SignIn,{onHome:()=>{}}):createElement(PrivateArea));}
const alice={id:'alice-id',name:'Alice',email:'alice@example.com',role:'member'},bob={id:'bob-id',name:'Bob',email:'bob@example.com',role:'member'};
test('frontend authentication gates private screens and clears state when identities change',async t=>{
 const responses:any[]=[],logoutCalls:RequestInit[]=[];let current:any={enabled:true,authenticated:false,user:null};
 globalThis.fetch=(async(path:any,init?:RequestInit)=>{if(String(path)==='/api/auth/logout'){logoutCalls.push(init!);return Response.json({error:'일시적인 로그아웃 실패'},{status:503});}responses.push(current);return current instanceof Error?Promise.reject(current):Response.json(current);}) as typeof fetch;
 const ui=createRoot(document.getElementById('root'));starts=0;stops=0;
 const render=async()=>act(async()=>{ui.render(createElement(AuthProvider,null,createElement(App)));await tick();});
 const focus=async()=>act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});
 try{
  await render();
  await t.test('anonymous users see sign-in before any private component mounts',()=>{
   assert.match(document.body.textContent||'',/내 작업 공간에 로그인/);assert.equal(starts,0);assert.ok(document.querySelector('a[href^="/auth/login?"]'));
  });
  await t.test('sign-in preserves a private route and sends intro visitors to the workspace',()=>{
   assert.match(decodeURIComponent(loginUrl()),/returnTo=\/\?screen=workflow/);
   window.history.replaceState(null,'','/?screen=intro');assert.match(decodeURIComponent(loginUrl()),/screen=home/);window.history.replaceState(null,'','/?screen=workflow');
  });
  current={enabled:true,authenticated:true,user:alice};await focus();
  await t.test('verified member sees their name and the private workspace',()=>{
   assert.ok(document.body.textContent?.includes('Alice'));assert.equal(starts,1);assert.equal(document.querySelector('form'),null);assert.ok([...document.querySelectorAll('button')].some(button=>button.textContent==='로그아웃'));
  });
  await t.test('logout uses a same-origin JSON POST and preserves the screen on a failed request',async()=>{
   const button=[...document.querySelectorAll('button')].find(button=>button.textContent==='로그아웃')!;
   await act(async()=>{button.click();await tick();});
   assert.equal(logoutCalls.length,1);assert.equal(logoutCalls[0].method,'POST');assert.equal(logoutCalls[0].credentials,'same-origin');assert.equal(new Headers(logoutCalls[0].headers).get('Content-Type'),'application/json');
   assert.match(document.body.textContent||'',/일시적인 로그아웃 실패/);assert.ok(document.body.textContent?.includes('Alice'));assert.equal(stops,0);
  });
  localStorage.setItem('a4b-builder-session','alice-private-session');localStorage.setItem('a4b-selected-agent','alice-private-agent');localStorage.setItem('a4b-chat-session-example','alice-chat');
  // Another tab may write Bob's ID before this tab receives its storage event.
  localStorage.setItem('a4b-auth-member',bob.id);
  current={enabled:true,authenticated:true,user:bob};await focus();
  await t.test('switching accounts clears old navigation even when another tab already stored the new member ID',()=>{
   assert.ok(document.body.textContent?.includes('Bob'));assert.equal(starts,2);assert.equal(stops,1);assert.equal(localStorage.getItem('a4b-selected-agent'),null);assert.equal(localStorage.getItem('a4b-builder-session'),null);assert.equal(localStorage.getItem('a4b-chat-session-example'),null);
  });
  await act(async()=>{window.dispatchEvent(new Event('a4b:unauthenticated'));await tick();});
  await t.test('an API 401 unmounts the private UI and offers sign-in again',()=>{
   assert.equal(stops,2);assert.match(document.body.textContent||'',/다시 로그인/);assert.ok(!document.body.textContent?.includes('private draft'));
  });
  current=new Error('offline');await focus();
  await t.test('network errors cannot silently disable authentication',()=>{assert.match(document.body.textContent||'',/offline/);assert.equal(starts,2);});
  current={enabled:false,authenticated:false,user:null};await focus();
  await t.test('explicit local mode keeps the original workspace available',()=>{assert.equal(starts,3);assert.ok(!document.querySelector('form'));});
 }finally{
  await act(async()=>ui.unmount());dom.window.close();globalThis.fetch=originalFetch;
  for(const [name,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete (globalThis as any)[name];
 }
});
test('actual product entry does not fetch private data before authentication finishes',async()=>{
 install();const calls:string[]=[];let complete!:(value:Response)=>void;
 globalThis.fetch=(async(path:any)=>{calls.push(String(path));return new Promise<Response>(resolve=>{complete=resolve;});}) as typeof fetch;
 const ui=createRoot(document.getElementById('root'));
 try{
  await act(async()=>{ui.render(createElement(AppUnderTest));await tick();});
  assert.deepEqual(calls,['/api/auth/me']);assert.ok(document.querySelector('.page-transition'));assert.equal(document.querySelector('.a4b-auth-primary'),null);assert.equal(document.querySelector('.workspace-overview'),null);
  await act(async()=>{complete(Response.json({enabled:true,authenticated:false,user:null}));await tick();});
  assert.deepEqual(calls,['/api/auth/me']);assert.match(document.body.textContent||'',/내 작업 공간에 로그인/);
 }finally{
  await act(async()=>ui.unmount());dom.window.close();globalThis.fetch=originalFetch;
  for(const [name,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete (globalThis as any)[name];
 }
});

test('initial authentication preserves saved design navigation for the same member only',async()=>{
 for(const storedMember of [alice.id,bob.id]){
  install();localStorage.setItem('a4b-auth-member',storedMember);
  const saved={'a4b-builder-session':'saved-design','a4b-selected-agent':'saved-agent','a4b-chat-session-example':'saved-chat'};
  for(const [key,value] of Object.entries(saved))localStorage.setItem(key,value);
  globalThis.fetch=(async()=>Response.json({enabled:true,authenticated:true,user:alice})) as typeof fetch;
  const ui=createRoot(document.getElementById('root'));
  try{
   await act(async()=>{ui.render(createElement(AuthProvider,null,createElement(App)));await tick();});
   assert.ok(document.body.textContent?.includes('Alice'));
   for(const [key,value] of Object.entries(saved))assert.equal(localStorage.getItem(key),storedMember===alice.id?value:null,key);
   assert.equal(localStorage.getItem('a4b-auth-member'),alice.id);
   await act(async()=>{window.dispatchEvent(new Event('focus'));await tick();});
   for(const [key,value] of Object.entries(saved))assert.equal(localStorage.getItem(key),storedMember===alice.id?value:null,key);
  }finally{
   await act(async()=>ui.unmount());dom.window.close();globalThis.fetch=originalFetch;
   for(const [name,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete (globalThis as any)[name];
  }
 }
});
