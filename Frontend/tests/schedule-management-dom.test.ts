import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({stdin:{contents:"export {ScheduleSettings} from './app/schedule-settings';export {ScheduledWorkflows} from './app/scheduled-workflows';export {ClarificationQuestion} from './app/clarification-question';",resolveDir:frontendRoot,sourcefile:'schedule-controls-test.tsx'},absWorkingDir:frontendRoot,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const globals=['window','document','navigator','location','HTMLElement','HTMLInputElement','HTMLSelectElement','HTMLTextAreaElement','Element','Node','Event','IS_REACT_ACT_ENVIRONMENT'];
const previous=new Map(globals.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
let dom:JSDOM;
function installDOM(){
 dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost/',pretendToBeVisual:true});
 for(const key of globals)if(key==='IS_REACT_ACT_ENVIRONMENT')Object.defineProperty(globalThis,key,{value:true,writable:true,configurable:true});else if(key in dom.window)Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],writable:true,configurable:true});
}
installDOM();
const {createElement,act,useState}=require('react');
const {createRoot}=require('react-dom/client');
const module={exports:{} as any};new Function('require','module','exports',compiled)(require,module,module.exports);
const {ScheduleSettings,ScheduledWorkflows,ClarificationQuestion}=module.exports;
const originalFetch=globalThis.fetch,tick=()=>new Promise(done=>setImmediate(done));
const daily={frequency:'daily',timezone:'Asia/Seoul',hour:9,minute:0,weekday:null,monthday:null,month_end:false};
const saved={active:false,spec:daily,next_run_at:'2026-11-12T00:00:00Z',agent_version:1};
async function mount(component:any,handler?:(path:string,body:any)=>unknown){
 dom.window.close();installDOM();const calls:{path:string;body:any}[]=[];
 globalThis.fetch=(async(path:any,init?:RequestInit)=>{const body=init?.body?JSON.parse(String(init.body)):undefined;calls.push({path:String(path),body});const value=String(path)==='/api/workflows'?{workflows:[]}:await handler?.(String(path),body);return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});}) as any;
 const root=createRoot(document.getElementById('root'));await act(async()=>{root.render(component);await tick();});
 return {calls,render:async(component:any)=>act(async()=>{root.render(component);await tick();}),close:async()=>{await act(async()=>{root.unmount();await tick();});globalThis.fetch=originalFetch;}};
}
function button(text:string){const found=Array.from(document.querySelectorAll('button')).find(item=>item.textContent?.trim()===text);assert.ok(found,'Missing button '+text);return found;}
async function click(element:HTMLElement){await act(async()=>{element.click();await tick();});}
async function change(element:HTMLInputElement|HTMLSelectElement,value:string){await act(async()=>{const prototype=element.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value')!.set!.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));element.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});}
function labelled(label:string){const found=Array.from(document.querySelectorAll('label')).find(element=>element.textContent?.startsWith(label));assert.ok(found,'Missing label '+label);return document.getElementById(found.htmlFor)! as HTMLInputElement|HTMLSelectElement;}

test('schedule can be added to a manual workflow, paused, activated and removed independently',async()=>{
 let schedule:any=null;
 function Harness(){const [current,setCurrent]=useState(null);return createElement(ScheduleSettings,{workflowId:'manual',schedule:current,onChange:setCurrent});}
 const h=await mount(createElement(Harness),(path,body)=>{
  if(path.endsWith('/schedule_configure')){schedule={...saved,spec:body.spec};return {schedule};}
  if(path.endsWith('/schedule')){schedule={...schedule,active:body.active};return {schedule};}
  if(path.endsWith('/schedule_remove'))return {schedule:null};
  throw Error('Unexpected '+path);
 });
 try{
  await change(labelled('반복 주기'),'weekly');await click(button('예약 추가'));
  assert.equal(h.calls[0].body.spec.weekday,0,'visible Monday default must be saved');
  assert.equal(h.calls[0].body.id,'manual');assert.match(document.body.textContent!,/중지/);
  await click(button('예약 켜기'));assert.deepEqual(h.calls[1],{path:'/api/agent-builder/schedule',body:{id:'manual',active:true}});
  await click(button('예약 중지'));assert.equal(h.calls[2].body.active,false);
  await click(button('예약 해제'));assert.deepEqual(h.calls[3],{path:'/api/agent-builder/schedule_remove',body:{id:'manual'}});
  assert.match(document.body.textContent!,/워크플로우는 계속 직접 실행/);assert.ok(button('예약 추가'));
 }finally{await h.close();}
});

test('incomplete automatic inputs block activation while an active reservation remains stoppable',async()=>{
 const props={workflowId:'A',schedule:saved,activationDisabled:true,onChange:()=>{}};
 const h=await mount(createElement(ScheduleSettings,props));
 try{
  assert.equal(button('예약 켜기').disabled,true);assert.match(document.body.textContent!,/자료·요청 설정/);
  await h.render(createElement(ScheduleSettings,{...props,schedule:{...saved,active:true}}));
  assert.equal(button('예약 중지').disabled,false);assert.equal(button('예약 해제').disabled,false);
 }finally{await h.close();}
});

test('an edited workflow needs explicit version application before its reservation can resume',async()=>{
 const h=await mount(createElement(ScheduleSettings,{workflowId:'A',workflowVersion:2,schedule:saved,onChange:()=>{}}));
 try{
  assert.equal(button('예약 켜기').disabled,true);assert.match(document.body.textContent!,/설정 확인 필요/);
  await click(button('일정 수정'));assert.ok(button('일정 저장 · 현재 버전 적용'));
 }finally{await h.close();}
});

test('unsaved schedule edits survive a cancelled close and a status toggle, and clear on cancel or save',async()=>{
 const observed:boolean[]=[];
 function Harness({observer}:{observer:(dirty:boolean)=>void}){const [current,setCurrent]=useState(saved);return createElement(ScheduleSettings,{workflowId:'A',schedule:current,onChange:setCurrent,onDirtyChange:observer});}
 let serverSchedule=saved;
 const h=await mount(createElement(Harness,{observer:(dirty:boolean)=>observed.push(dirty)}),(path,body)=>{
  serverSchedule=path.endsWith('/schedule_configure')?{...saved,spec:body.spec}:{...serverSchedule,active:body.active};return {schedule:serverSchedule};
 });
 let confirmations=0,allowDiscard=false;dom.window.confirm=()=>{confirmations++;return allowDiscard;};
 try{
  assert.equal(observed.at(-1),false);await click(button('일정 수정'));await change(labelled('실행 시간'),'10:30');assert.equal(observed.at(-1),true);
  const count=observed.length;await h.render(createElement(Harness,{observer:(dirty:boolean)=>observed.push(dirty)}));assert.equal(observed.length,count,'changing the callback must not report a spurious clean state');
  await click(button('일정 수정 닫기'));assert.equal(confirmations,1);assert.equal(labelled('실행 시간').value,'10:30');assert.equal(observed.at(-1),true);
  await click(button('예약 켜기'));assert.equal(confirmations,1);assert.equal(labelled('실행 시간').value,'10:30');assert.equal(observed.at(-1),true);
  allowDiscard=true;await click(button('취소'));assert.equal(confirmations,2);assert.equal(observed.at(-1),false);assert.equal(document.querySelector('.schedule-settings-form'),null);
  await click(button('일정 수정'));assert.equal(labelled('실행 시간').value,'09:00');await change(labelled('실행 시간'),'12:15');assert.equal(observed.at(-1),true);
  await click(button('일정 저장'));assert.equal(observed.at(-1),false);assert.equal(confirmations,2,'saving does not ask to discard');
  await click(button('일정 수정'));assert.equal(labelled('실행 시간').value,'12:15');
 }finally{await h.close();assert.equal(observed.at(-1),false);}
});

test('new reservation defaults and reverting meaningful edits leave no unsaved changes',async()=>{
 const observed:boolean[]=[];
 const h=await mount(createElement(ScheduleSettings,{workflowId:'new',schedule:null,onChange:()=>{},onDirtyChange:(dirty:boolean)=>observed.push(dirty)}));
 try{
  assert.equal(observed.at(-1),false);await change(labelled('반복 주기'),'weekly');assert.equal(observed.at(-1),true);
  await change(labelled('실행 요일'),'3');await change(labelled('반복 주기'),'daily');assert.equal(observed.at(-1),false,'hidden weekly settings do not change a daily reservation');
 }finally{await h.close();assert.equal(observed.at(-1),false);}
});

test('reservation management protects dirty forms when closing, refreshing, switching rows or selecting another workflow',async()=>{
 const agent=(id:string)=>({id,version:1,config:{name:id,purpose:'자료 요약',schedule:null}}),agents=['A','B','C','D'].map(agent);
 let guard:()=>boolean=()=>true;
 const h=await mount(createElement(ScheduledWorkflows,{onOpen:()=>{},onCreate:()=>{},onCanLeaveChange:(value:()=>boolean)=>{guard=value;}}),path=>{
  if(path.endsWith('/schedules'))return {workflows:agents.slice(0,2).map(value=>({agent:value,schedule:saved}))};
  if(path.endsWith('/agents'))return {agents};throw Error('Unexpected '+path);
 });
 let confirmations=0,allowDiscard=false;dom.window.confirm=()=>{confirmations++;return allowDiscard;};
 try{
  await click(document.querySelector<HTMLElement>('[aria-label="A 일정 관리"]')!);await click(button('일정 수정'));await change(labelled('실행 시간'),'10:00');
  const unload=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  assert.equal(guard(),false);assert.equal(confirmations,1);
  await click(document.querySelector<HTMLElement>('[aria-label="예약 설정 닫기"]')!);assert.equal(confirmations,2);assert.equal(labelled('실행 시간').value,'10:00');
  await click(document.querySelector<HTMLElement>('[aria-label="B 일정 관리"]')!);assert.equal(confirmations,3);assert.match(document.querySelector('.schedule-manager-heading')!.textContent!,/A/);
  const reads=h.calls.length;await click(document.querySelector<HTMLElement>('[aria-label="예약 목록 새로고침"]')!);assert.equal(confirmations,4);assert.equal(h.calls.length,reads);
  allowDiscard=true;await click(button('기존 워크플로우에 예약 추가'));assert.equal(confirmations,5);
  const picker=document.querySelector<HTMLSelectElement>('[aria-label="예약할 워크플로우"]')!;await change(picker,'C');assert.equal(guard(),true);assert.equal(confirmations,5);
  await change(labelled('반복 주기'),'hourly');allowDiscard=false;await change(picker,'D');assert.equal(confirmations,6);assert.equal(picker.value,'C');assert.equal(labelled('반복 주기').value,'hourly');
  allowDiscard=true;await change(picker,'D');assert.equal(confirmations,7);assert.equal(picker.value,'D');assert.equal(labelled('반복 주기').value,'daily');assert.equal(guard(),true);assert.equal(confirmations,7);
  const cleanUnload=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(cleanUnload);assert.equal(cleanUnload.defaultPrevented,false);
 }finally{await h.close();}
});

test('reservation management uses saved schedule and keeps an unscheduled workflow available after removal',async()=>{
 const agent={id:'A',version:1,config:{name:'보고서',purpose:'자료 요약',schedule:null}},manual={id:'B',version:1,config:{name:'직접 작업',purpose:'직접 실행',schedule:null}};
 const opened:string[]=[];
 const h=await mount(createElement(ScheduledWorkflows,{onOpen:(id:string)=>opened.push(id),onCreate:()=>{}}),(path,body)=>{
  if(path.endsWith('/schedules'))return {workflows:[{agent,schedule:saved}]};
  if(path.endsWith('/agents'))return {agents:[agent,manual]};
  if(path.endsWith('/schedule_remove'))return {schedule:null};
  if(path.endsWith('/schedule_configure'))return {schedule:{...saved,spec:body.spec}};
  throw Error('Unexpected '+path);
 });
 try{
  assert.equal(document.querySelector('h1')?.textContent,'예약 관리');assert.match(document.querySelector('table')!.textContent!,/매일 09:00/);
  await click(button('상세 확인'));assert.deepEqual(opened,['A']);
  await click(button('일정 관리'));await click(button('예약 해제'));assert.equal(document.querySelector('[data-schedule-id="A"]'),null);
  await click(button('기존 워크플로우에 예약 추가'));
  const picker=document.querySelector<HTMLSelectElement>('[aria-label="예약할 워크플로우"]')!;assert.deepEqual(Array.from(picker.options).map(option=>option.value),['','A','B']);
  await change(picker,'B');await click(button('예약 추가'));assert.ok(document.querySelector('[data-schedule-id="B"]'));
  assert.equal(h.calls.at(-1)?.body.id,'B');
 }finally{await h.close();}
});

test('choice questions use explicit confirmation and multiple selections remain selected',async()=>{
 const answers:string[]=[];
 const h=await mount(createElement(ClarificationQuestion,{question:{id:'one',text:'실행 방식',options:['직접','예약'],response:{type:'single_choice'}},onAnswer:(answer:string)=>answers.push(answer)}));
 try{
  assert.equal(button('답변 확인').disabled,true);await click(document.querySelector<HTMLInputElement>('input[value="예약"]')!);assert.equal(answers.length,0);
  await click(button('답변 확인'));assert.deepEqual([...answers],['예약']);
  await h.render(createElement(ClarificationQuestion,{question:{id:'two',text:'대상 선택',options:['영업','지원'],response:{type:'multi_choice'}},onAnswer:(answer:string)=>answers.push(answer)}));
  assert.equal(button('답변 확인').disabled,true);
  for(const input of document.querySelectorAll<HTMLInputElement>('input[type=checkbox]'))await click(input);
  await click(button('답변 확인'));assert.deepEqual(answers,['예약','영업, 지원']);
 }finally{await h.close();}
});

test('numeric questions accept zero and expose bounds and unit as an input',async()=>{
 const answers:string[]=[];
 const h=await mount(createElement(ClarificationQuestion,{question:{id:'limit',text:'조회 건수',response:{type:'number',minimum:0,maximum:100,step:1,unit:'건'}},onAnswer:(answer:string)=>answers.push(answer)}));
 try{
  const input=document.querySelector<HTMLInputElement>('input[type=number]')!;assert.ok(input);assert.equal(input.min,'0');assert.equal(input.max,'100');assert.equal(input.step,'1');
  await change(input,'0');assert.equal(button('답변 확인').disabled,false);await click(button('답변 확인'));assert.deepEqual(answers,['0']);
 }finally{await h.close();}
});


test('reservation timezone is fixed to Seoul and cannot be edited',async()=>{
 const h=await mount(createElement(ScheduleSettings,{workflowId:'seoul',schedule:{...saved,spec:{...daily,timezone:'America/New_York'}},onChange:()=>{}}),(_path,body)=>({schedule:{...saved,spec:body.spec}}));
 try{
  await click(button('일정 수정'));assert.match(document.body.textContent!,/서울 표준시 \(고정\)/);
  assert.equal(document.querySelector('input[placeholder="Asia/Seoul"]'),null);
  await click(button('일정 저장'));assert.equal(h.calls[0].body.spec.timezone,'Asia/Seoul');
 }finally{await h.close();}
});
test.after(()=>{dom.window.close();for(const key of globals){const descriptor=previous.get(key);if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete (globalThis as any)[key];}globalThis.fetch=originalFetch;});
