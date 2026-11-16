import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(frontendRoot,'app/clarification-form.tsx')],absWorkingDir:frontendRoot,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
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
const {ClarificationForm}=module.exports;
const tick=()=>new Promise(done=>setImmediate(done));
async function mount(props:any){
 dom.window.close();installDOM();const root=createRoot(document.getElementById('root'));
 await act(async()=>{root.render(createElement(ClarificationForm,props));await tick();});
 return {render:async(next:any)=>act(async()=>{root.render(createElement(ClarificationForm,next));await tick();}),close:async()=>act(async()=>{root.unmount();await tick();})};
}
async function click(element:Element){await act(async()=>{(element as HTMLElement).click();await tick();});}
async function input(element:HTMLInputElement|HTMLTextAreaElement,value:string){
 await act(async()=>{const prototype=element.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value')!.set!.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));element.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await tick();});
}
async function submit(){await act(async()=>{document.querySelector('form')!.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();});}
function question(text:string){const found=Array.from(document.querySelectorAll('fieldset')).find(field=>field.querySelector('legend')?.textContent?.includes(text));assert.ok(found,text);return found;}
function choice(field:Element,value:string){const found=Array.from(field.querySelectorAll<HTMLInputElement>('input')).find(input=>input.value===value);assert.ok(found,value);return found;}

test('independent request controls send all selected values in a single typed submission',async()=>{
 const submitted:any[]=[];
 const questions=[
  {id:'fields-id',key:'fields',group:'포함할 내용',text:'어떤 항목을 포함할까요?',options:['담당자','완료일','진행 상태'],response:{type:'multi_choice'}},
  {id:'format-id',key:'format',group:'결과 형식',text:'어떤 파일 형식으로 만들까요?',reason:'결과 파일을 만들 때 사용할 형식이 필요합니다.',options:['CSV','Excel'],response:{type:'single_choice'}},
  {id:'limit-id',key:'limit',group:'처리 범위',text:'최소 수량은 얼마인가요?',response:{type:'number',minimum:0,step:1,unit:'개'}},
 ];
 const h=await mount({questions,onSubmit:(answers:any)=>submitted.push(answers)});
 try{
  assert.match(document.body.textContent!,/워크플로우 생성에 필요한 3개 항목/);
  assert.match(question(questions[1].text).textContent!,/결과 파일을 만들 때 사용할 형식이 필요합니다/);
  await click(choice(question(questions[0].text),'담당자'));await click(choice(question(questions[0].text),'완료일'));
  await click(choice(question(questions[1].text),'Excel'));await input(question(questions[2].text).querySelector('input')!,'0');
  await click(document.querySelector('[aria-label="다음 질문"]')!);await click(document.querySelector('[aria-label="다음 질문"]')!);
  assert.equal(submitted.length,0);assert.equal(document.querySelectorAll('button[type="submit"]').length,1);
  await submit();assert.equal(submitted.length,1);
  assert.deepEqual(submitted[0].map((answer:any)=>[answer.questionId,answer.value]),[['fields-id',['담당자','완료일']],['format-id','Excel'],['limit-id',0]]);
 }finally{await h.close();}
});

test('a refreshed question id preserves values for the same request key and skips unanswered optional requests',async()=>{
 const submitted:any[]=[];
 const questions=[{id:'old-id',key:'format',text:'결과 형식은 무엇인가요?',options:['CSV','JSON'],response:{type:'single_choice'}},{id:'blank-id',key:'notes',text:'추가 조건이 있나요?',response:{type:'text'},required:false}];
 const onSubmit=(answers:any)=>submitted.push(answers),h=await mount({questions,onSubmit});
 try{
  await click(choice(question(questions[0].text),'JSON'));
  await h.render({questions:[{...questions[0],id:'new-id'},questions[1]],onSubmit});
  assert.equal(choice(question(questions[0].text),'JSON').checked,true);await submit();
  assert.deepEqual(submitted[0].map((answer:any)=>[answer.questionId,answer.value]),[['new-id','JSON'],['blank-id',null]]);assert.equal(submitted[0][1].skip,true);
 }finally{await h.close();}
});

test('optional questions can be skipped without creating dirty input or answering a required question',async()=>{
 const submitted:any[]=[],dirty:boolean[]=[];
 const questions=[{id:'required',key:'hour',text:'몇 시에 실행할까요?',response:{type:'number'}},{id:'optional',key:'style',text:'원하는 표현 방식이 있나요?',options:['간결하게','자세하게'],response:{type:'single_choice'},required:false}];
 const h=await mount({questions,onSubmit:(answers:any)=>submitted.push(answers),onDirtyChange:(value:boolean)=>dirty.push(value)});
 try{
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  assert.equal(document.querySelector('button[type="submit"]')!.textContent,'선택사항 건너뛰기');assert.equal((document.querySelector('button[type="submit"]') as HTMLButtonElement).disabled,false);assert.equal(dirty.at(-1),false);
  await submit();assert.deepEqual(submitted[0].map((answer:any)=>[answer.questionId,answer.value,answer.skip]),[['optional',null,true]]);assert.equal(dirty.at(-1),false);
 }finally{await h.close();}
});

test('custom formats and additional multiple-choice content are submitted with an explicit custom marker',async()=>{
 const submitted:any[]=[];
 const questions=[{id:'single',key:'format',text:'어떤 형식인가요?',options:['CSV','JSON'],response:{type:'single_choice'}},{id:'multi',key:'sections',text:'어떤 내용을 담을까요?',options:['요약','상세'],response:{type:'multi_choice'}}];
 const h=await mount({questions,onSubmit:(answers:any)=>submitted.push(answers)});
 try{
  const single=question(questions[0].text),multi=question(questions[1].text);
  await click(single.querySelector('input[id$="custom-choice"]')!);await input(single.querySelector('textarea')!,'표와 설명을 함께 작성');
  await click(choice(multi,'요약'));await click(multi.querySelector('input[id$="custom-choice"]')!);await input(multi.querySelector('textarea')!,'예외 항목');
  await submit();assert.deepEqual(submitted[0].map((answer:any)=>[answer.value,answer.custom]),[['표와 설명을 함께 작성',true],[['요약','예외 항목'],true]]);
 }finally{await h.close();}
});

test('invalid numeric steps are rejected and a failed request preserves inputs for retry',async()=>{
 const questions=[{id:'ratio',key:'ratio',text:'적용할 비율은 얼마인가요?',response:{type:'number',minimum:0,maximum:1,step:0.1}}];
 let requests=0;
 const h=await mount({questions,onSubmit:async()=>{requests++;throw Error('저장 연결을 다시 확인해주세요.');}});
 try{
  const numeric=document.querySelector<HTMLInputElement>('input[type="number"]')!;
  await input(numeric,'0.15');await submit();assert.equal(requests,0);assert.match(document.querySelector('[role="alert"]')!.textContent!,/0\.1 단위/);
  await input(numeric,'0.2');await submit();assert.equal(requests,1);assert.match(document.querySelector('[role="alert"]')!.textContent!,/저장 연결/);assert.equal(numeric.value,'0.2');
 }finally{await h.close();}
});

test('a pending submission locks every request and blocks duplicate submissions',async()=>{
 let resolve!:()=>void;const pending=new Promise<void>(done=>{resolve=done;});let requests=0;
 const questions=[{id:'format',key:'format',text:'저장 형식을 선택하세요.',options:['CSV','JSON'],response:{type:'single_choice'}}];
 const h=await mount({questions,onSubmit:async()=>{requests++;await pending;}});
 try{
  await click(choice(question(questions[0].text),'CSV'));await submit();await submit();
  assert.equal(requests,1);assert.equal(document.querySelector('fieldset')!.disabled,true);assert.equal(document.querySelector('button')!.disabled,true);
  await act(async()=>{resolve();await pending;await tick();});assert.equal(document.querySelector('fieldset')!.disabled,false);
 }finally{resolve();await h.close();}
});


test('execution times accept a colon and put a required marker next to the input',async()=>{
 const submitted:any[]=[];const text='실행 시각을 입력해주세요.';
 const h=await mount({questions:[{id:'time-id',key:'schedule.time',text,response:{type:'time'}}],onSubmit:(answers:any)=>submitted.push(answers)});
 try{
  const field=question(text).querySelector<HTMLInputElement>('input')!;assert.equal(field.type,'text');assert.equal(field.placeholder,'09:00');
  assert.equal(field.parentElement!.querySelector('.clarification-input-marker.required')?.textContent,'필수');
  assert.equal(question(text).querySelector('legend small'),null);
  await input(field,'9:30');await submit();assert.equal(submitted[0][0].value,'09:30');
  await input(field,'24:00');await submit();assert.equal(submitted.length,1);assert.match(document.querySelector('[role="alert"]')!.textContent!,/시:분/);
 }finally{await h.close();}
});

test('review settings combine selectable, compact numeric and time controls with full-width free text',async()=>{
 const submitted:any[]=[];
 const questions=[
  {id:'team',key:'review.team',text:'어느 팀에서 검토할까요?',options:['운영팀','품질팀'],response:{type:'single_choice'}},
  {id:'checks',key:'review.checks',text:'어떤 검사를 포함할까요?',options:['누락 검사','금액 검사','중복 검사'],response:{type:'multi_choice'}},
  {id:'threshold',key:'review.threshold',text:'검토할 최소 금액은 얼마인가요?',response:{type:'number',minimum:0,step:100,unit:'원'}},
  {id:'deadline',key:'review.deadline',text:'검토 마감 시각은 언제인가요?',response:{type:'time'}},
  {id:'notes',key:'review.notes',text:'검토자가 참고할 내용이 있나요?',response:{type:'text'},required:false},
 ];
 const h=await mount({questions,onSubmit:(answers:any)=>submitted.push(answers)});
 try{
  const numeric=question(questions[2].text).querySelector<HTMLInputElement>('input[type="number"]')!;
  const time=question(questions[3].text).querySelector<HTMLInputElement>('input')!;
  const notes=question(questions[4].text).querySelector<HTMLTextAreaElement>('textarea')!;
  assert.ok(numeric.classList.contains('clarification-number-input'));
  assert.ok(time.classList.contains('clarification-time-input'));
  assert.equal(question(questions[2].text).querySelector('textarea'),null);
  assert.equal(question(questions[3].text).querySelector('textarea'),null);
  assert.equal(notes.classList.contains('clarification-number-input'),false);
  for(const field of [numeric,time])assert.equal(field.parentElement!.querySelector('.clarification-input-marker.required')?.textContent,'필수');
  await click(choice(question(questions[0].text),'품질팀'));
  await click(choice(question(questions[1].text),'누락 검사'));await click(choice(question(questions[1].text),'금액 검사'));
  await input(numeric,'1200');await input(time,'17:30');await input(notes,'예외 거래는 별도로 표시');
  await submit();
  assert.deepEqual(submitted[0].map((answer:any)=>[answer.key,answer.value]),[
   ['review.team','품질팀'],['review.checks',['누락 검사','금액 검사']],['review.threshold',1200],['review.deadline','17:30'],['review.notes','예외 거래는 별도로 표시'],
  ]);
 }finally{await h.close();}
});

test('question cards preserve answers across next and previous and submit one complete payload',async()=>{
 const submitted:any[]=[];
 const questions=[{id:'one',key:'format',text:'저장 형식',options:['CSV','Excel'],response:{type:'single_choice'}},{id:'two',key:'sections',text:'포함 항목',options:['요약','상세'],response:{type:'multi_choice'}}];
 const h=await mount({questions,onSubmit:(answers:any)=>submitted.push(answers)});
 try{
  const fields=()=>Array.from(document.querySelectorAll('fieldset'));
  assert.deepEqual(fields().map(el=>el.hidden),[false,true]);
  assert.equal((document.querySelector('[aria-label="이전 질문"]') as HTMLButtonElement).disabled,true);
  await click(choice(question('저장 형식'),'CSV'));
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  assert.deepEqual(fields().map(el=>el.hidden),[true,false]);assert.equal(submitted.length,0);
  await click(choice(question('포함 항목'),'요약'));await click(choice(question('포함 항목'),'상세'));
  await click(document.querySelector('[aria-label="이전 질문"]')!);
  assert.equal(choice(question('저장 형식'),'CSV').checked,true);
  await click(choice(question('저장 형식'),'Excel'));
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  assert.equal(choice(question('포함 항목'),'상세').checked,true);
  await click(document.querySelector('button[type="submit"]')!);
  assert.equal(submitted.length,1);assert.deepEqual(submitted[0].map((answer:any)=>[answer.questionId,answer.value]),[['one','Excel'],['two',['요약','상세']]]);
 }finally{await h.close();}
});

test('validation returns to the invalid hidden question without losing other answers',async()=>{
 const questions=[{id:'time',text:'실행 시간',response:{type:'time'}},{id:'notes',text:'추가 설명',response:{type:'text'}}];
 let requests=0;const h=await mount({questions,onSubmit:()=>{requests++;}});
 try{
  await input(question('실행 시간').querySelector('input')!,'24:00');
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  await input(question('추가 설명').querySelector('textarea')!,'입력 보존');
  await click(document.querySelector('button[type="submit"]')!);
  assert.equal(requests,0);assert.equal(question('실행 시간').hidden,false);
  assert.match(document.querySelector('[role="alert"]')!.textContent!,/시:분/);
  await input(question('실행 시간').querySelector('input')!,'09:30');
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  assert.equal(question('추가 설명').querySelector('textarea')!.value,'입력 보존');
  await click(document.querySelector('button[type="submit"]')!);assert.equal(requests,1);
 }finally{await h.close();}
});

test('question refresh keeps the active request by key and falls back when removed',async()=>{
 const one={id:'a',key:'a',text:'첫 질문',response:{type:'text'}},two={id:'b',key:'b',text:'둘째 질문',response:{type:'text'}};
 const onSubmit=()=>{},h=await mount({questions:[one,two],onSubmit});
 try{
  await click(document.querySelector('[aria-label="다음 질문"]')!);
  await input(question('둘째 질문').querySelector('textarea')!,'보존');
  await h.render({questions:[{...two,id:'b-new'},one],onSubmit});
  assert.equal(question('둘째 질문').hidden,false);assert.equal(question('둘째 질문').querySelector('textarea')!.value,'보존');
  await h.render({questions:[one],onSubmit});assert.equal(question('첫 질문').hidden,false);
 }finally{await h.close();}
});


test('Enter advances numeric input without early submission and IME composition stays untouched',async()=>{
 let requests=0;const h=await mount({questions:[{id:'amount',text:'수량',response:{type:'number'}},{id:'extra',text:'메모',response:{type:'text'}}],onSubmit:()=>{requests++;}});
 try{
  const field=question('수량').querySelector('input')!;
  await input(field,'0');
  const composing=new dom.window.KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true});
  await act(async()=>{field.dispatchEvent(composing);await tick();});
  assert.equal(question('수량').hidden,false);assert.equal(composing.defaultPrevented,false);
  const enter=new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  await act(async()=>{field.dispatchEvent(enter);await tick();});
  assert.equal(enter.defaultPrevented,true);assert.equal(question('메모').hidden,false);assert.equal(requests,0);
 }finally{await h.close();}
});

test('disabled question cards cannot navigate or submit',async()=>{
 let requests=0;const h=await mount({questions:[{id:'one',text:'질문 하나'},{id:'two',text:'질문 둘'}],disabled:true,onSubmit:()=>{requests++;}});
 try{
  assert.ok(Array.from(document.querySelectorAll('button')).every(button=>button.disabled));
  await click(document.querySelector('[aria-label="다음 질문"]')!);await submit();
  assert.equal(question('질문 하나').hidden,false);assert.equal(requests,0);
 }finally{await h.close();}
});

test.after(()=>{dom.window.close();for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else Reflect.deleteProperty(globalThis,key);});
