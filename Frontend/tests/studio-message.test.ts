import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(frontendRoot,'app/studio-message.tsx')],absWorkingDir:frontendRoot,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const globals=['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','Element','Node','Event','IS_REACT_ACT_ENVIRONMENT'];
const original=new Map(globals.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
let dom:JSDOM;
function installDOM(){
 dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost/',pretendToBeVisual:true});
 for(const key of globals)if(key==='IS_REACT_ACT_ENVIRONMENT')Object.defineProperty(globalThis,key,{value:true,writable:true,configurable:true});else if(key in dom.window)Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],writable:true,configurable:true});
}
installDOM();
const {createElement,act}=require('react'),{createRoot}=require('react-dom/client');
const module={exports:{} as any};new Function('require','module','exports',compiled)(require,module,module.exports);
const {StudioMessage}=module.exports;
let reduced=false;let listeners=new Set<()=>void>();
function media(){return {get matches(){return reduced;},addEventListener:(_name:string,listener:()=>void)=>listeners.add(listener),removeEventListener:(_name:string,listener:()=>void)=>listeners.delete(listener)};}

const tick=()=>new Promise(done=>setImmediate(done));
async function mount(props:any){
 dom.window.close();installDOM();reduced=false;listeners=new Set();Object.defineProperty(window,'matchMedia',{value:()=>media(),configurable:true});const root=createRoot(document.getElementById('root'));
 await act(async()=>{root.render(createElement(require('react').StrictMode,null,createElement(StudioMessage,props)));await tick();});
 return {render:async(next:any)=>act(async()=>{root.render(createElement(require('react').StrictMode,null,createElement(StudioMessage,next)));await tick();}),close:async()=>act(async()=>{root.unmount();await tick();})};
}
async function click(element:Element){await act(async()=>{(element as HTMLElement).click();await tick();});}
async function input(element:HTMLInputElement|HTMLTextAreaElement,value:string){
 await act(async()=>{const prototype=element.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value')!.set!.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));element.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});
}
async function submit(){await act(async()=>{document.querySelector('form')!.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();});}

const visible=()=>document.querySelector('p > span[aria-hidden="true"]')?.textContent??document.querySelector('p')?.textContent;
async function advance(t:any,frames:number){for(let i=0;i<frames;i++)await act(async()=>{t.mock.timers.tick(24);await tick();});}

test('new assistant reply reveals progressively, keeps an accessible full answer and completes without changing content',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const text='안녕하세요 👋 한글 답변과 이모지가 함께 표시됩니다. '.repeat(10),h=await mount({role:'assistant',content:text,animate:true});
 try{
  assert.equal(visible(),'');assert.equal(document.querySelector('.sr-only')?.textContent,text);
  await advance(t,1);const partial=visible()!;assert.ok(partial.length>0&&partial.length<text.length);assert.ok(text.startsWith(partial));
  assert.equal(/[^\uD800-\uDBFF][\uDC00-\uDFFF]|[\uD800-\uDBFF]$/.test(partial),false);
  await advance(t,60);assert.equal(visible(),text);assert.equal(document.querySelector('.studio-reveal-message'),null);
 }finally{await h.close();t.mock.timers.reset();}
});

test('history and user messages are immediate and do not create animation timers',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const h=await mount({role:'assistant',content:'저장된 대화'});
 try{
  assert.equal(visible(),'저장된 대화');assert.equal(document.querySelector('.studio-reveal-message'),null);
  await h.render({role:'user',content:'사용자 요청',animate:true});assert.equal(visible(),'사용자 요청');
 }finally{await h.close();t.mock.timers.reset();}
});

test('show all cancels the pending reveal and a later reply can animate independently',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const text='처리 조건을 확인했습니다. '.repeat(20),h=await mount({role:'assistant',content:text,animate:true});
 try{
  await advance(t,1);await click(document.querySelector('.studio-reveal-message')!);assert.equal(visible(),text);
  await advance(t,3);assert.equal(visible(),text);
  await h.render({role:'assistant',content:'새 답변입니다.',animate:true});assert.equal(visible(),'');
  await advance(t,60);assert.equal(visible(),'새 답변입니다.');
 }finally{await h.close();t.mock.timers.reset();}
});

test('turning on reduced motion reveals the full reply and removes pending listeners on unmount',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const text='접근성 설정을 존중하는 답변입니다.',h=await mount({role:'assistant',content:text,animate:true});
 try{
  await advance(t,1);assert.ok(visible()!.length<text.length);
  await act(async()=>{reduced=true;for(const listener of listeners)listener();await tick();});assert.equal(visible(),text);
 }finally{await h.close();assert.equal(listeners.size,0);t.mock.timers.reset();}
});

test.after(()=>{dom.window.close();for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else Reflect.deleteProperty(globalThis,key);});
