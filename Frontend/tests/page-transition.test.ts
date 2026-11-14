import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(frontendRoot,'app/page-transition.tsx')],absWorkingDir:frontendRoot,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
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
const {PageTransition}=module.exports;
const tick=()=>new Promise(done=>setImmediate(done));
async function mount(props:any){
 dom.window.close();installDOM();const root=createRoot(document.getElementById('root'));
 await act(async()=>{root.render(createElement(require('react').StrictMode,null,createElement(PageTransition,props)));await tick();});
 return {render:async(next:any)=>act(async()=>{root.render(createElement(require('react').StrictMode,null,createElement(PageTransition,next)));await tick();}),close:async()=>act(async()=>{root.unmount();await tick();})};
}
test('initial transition shows only the symbol and restores page interaction when ready',async()=>{
 const h=await mount({active:true});
 try{
  assert.ok(document.querySelector('.page-transition'));assert.equal(document.querySelector('.page-transition img')!.getAttribute('src'),'/brand/a4b-flow-mark.svg');
  assert.equal(document.querySelector('.page-transition img')!.getAttribute('alt'),'');
  assert.equal(document.getElementById('root')!.hasAttribute('inert'),true);
  await h.render({active:false});assert.equal(document.querySelector('.page-transition'),null);assert.equal(document.getElementById('root')!.hasAttribute('inert'),false);
  await h.render({active:true});assert.equal(document.querySelector('.page-transition'),null);
 }finally{await h.close();}
});
test('leaving a pending screen removes its overlay and interaction lock',async()=>{
 const h=await mount({active:true});await h.close();assert.equal(document.querySelector('.page-transition'),null);assert.equal(document.getElementById('root')!.hasAttribute('inert'),false);
});
test('overlapping loaders release interaction only after the last loader exits',async()=>{
 dom.window.close();installDOM();const root=createRoot(document.getElementById('root'));
 const render=async(first:boolean,second:boolean)=>act(async()=>{root.render(createElement(require('react').StrictMode,null,first&&createElement(PageTransition,{key:'first',active:true}),second&&createElement(PageTransition,{key:'second',active:true})));await tick();});
 try{
  await render(true,true);assert.equal(document.querySelectorAll('.page-transition').length,2);
  await render(false,true);assert.equal(document.querySelectorAll('.page-transition').length,1);assert.equal(document.getElementById('root')!.hasAttribute('inert'),true);
  await render(false,false);assert.equal(document.querySelectorAll('.page-transition').length,0);assert.equal(document.getElementById('root')!.hasAttribute('inert'),false);
 }finally{await act(async()=>{root.unmount();await tick();});}
});
test('an existing interaction lock is preserved when a loader exits',async()=>{
 dom.window.close();installDOM();const element=document.getElementById('root')!;element.setAttribute('inert','');const root=createRoot(element);
 try{
  await act(async()=>{root.render(createElement(PageTransition,{active:true}));await tick();});
  await act(async()=>{root.unmount();await tick();});assert.equal(element.hasAttribute('inert'),true);
 }finally{element.removeAttribute('inert');}
});
test.after(()=>{dom.window.close();for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else Reflect.deleteProperty(globalThis,key);});
