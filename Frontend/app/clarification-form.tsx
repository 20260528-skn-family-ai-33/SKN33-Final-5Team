import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import type {ClarificationQuestionData} from './clarification-question';
import './clarification-question.css';
import './clarification-form.css';
import {normalizeTimeInput} from '../lib/time-input';

export type ClarificationAnswer={questionId?:string;key?:string;text:string;value:string|string[]|number|null;custom?:boolean;skip?:boolean};
type InputValue={text:string;selected:string[];custom:string;customSelected:boolean;type:string};
const emptyValue=(type:string):InputValue=>({text:'',selected:[],custom:'',customSelected:false,type});
function responseType(question:ClarificationQuestionData){return question.response?.type??(question.options?.length?'single_choice':'text');}
function rowKey(question:ClarificationQuestionData,index:number,questions:ClarificationQuestionData[]){
 if(question.key&&questions.filter(item=>item.key===question.key).length===1)return question.key;
 return question.id||question.text+'-'+index;
}

export function ClarificationForm({questions,disabled=false,onSubmit,onDirtyChange}:{
 questions:ClarificationQuestionData[];
 disabled?:boolean;
 onSubmit:(answers:ClarificationAnswer[])=>void|Promise<void>;
 onDirtyChange?:(dirty:boolean)=>void;
}) {
 const formId=useId(),inFlight=useRef(false),dirtyCallback=useRef(onDirtyChange);dirtyCallback.current=onDirtyChange;
 const [values,setValues]=useState<Record<string,InputValue>>({}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const signature=JSON.stringify(questions);
 const rows=questions.map((question,index)=>({question,index,key:rowKey(question,index,questions)}));
 useEffect(()=>{
  setValues(prior=>Object.fromEntries(questions.map((question,index)=>{
   const key=rowKey(question,index,questions),type=responseType(question),previous=prior[key];
   const value=previous?.type===type?{...previous,selected:previous.selected.filter(option=>question.options?.includes(option))}:emptyValue(type);
   return [key,value];
  })));
  setError('');
 },[signature]);
 function valueFor(key:string,question:ClarificationQuestionData){return values[key]||emptyValue(responseType(question));}
 function change(key:string,question:ClarificationQuestionData,patch:Partial<InputValue>){setValues(prior=>({...prior,[key]:{...(prior[key]||emptyValue(responseType(question))),...patch}}));setError('');}
 function answerFor(key:string,question:ClarificationQuestionData):ClarificationAnswer|undefined {
  const value=valueFor(key,question),type=responseType(question),base={questionId:question.id,key:question.key,text:question.text};
  if((type==='single_choice'||type==='multi_choice')&&question.options?.length){
   const custom=value.customSelected?value.custom.trim():'';
   const selected=value.customSelected&&type==='single_choice'?[]:value.selected;
   if(!selected.length&&!custom)return;
   return {...base,value:type==='multi_choice'?[...selected,...(custom?[custom]:[])]:custom||selected[0],...(custom?{custom:true}:{})};
  }
  const text=value.text.trim();if(!text)return;
  return {...base,value:type==='number'?Number(text):type==='time'?normalizeTimeInput(text)||text:text};
 }
 const enteredAnswers=rows.map(({key,question})=>answerFor(key,question)).filter((answer):answer is ClarificationAnswer=>!!answer);
 const answers=rows.map(({key,question})=>answerFor(key,question)??(question.required===false?{questionId:question.id,key:question.key,text:question.text,value:null,skip:true}:undefined)).filter((answer):answer is ClarificationAnswer=>!!answer);
 const dirty=enteredAnswers.length>0||rows.some(({key,question})=>{const value=valueFor(key,question);return value.customSelected||!!value.text;});
 useEffect(()=>{dirtyCallback.current?.(dirty);},[dirty]);
 useEffect(()=>()=>{dirtyCallback.current?.(false);},[]);
 const locked=disabled||busy;
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(inFlight.current||disabled||!answers.length)return;
  for(const {key,question} of rows){
   const input=valueFor(key,question),answer=answerFor(key,question),response=question.response;
   if(input.customSelected&&!input.custom.trim()){setError('직접 입력을 선택한 항목의 답변을 적어주세요.');return;}
   if(answer&&responseType(question)==='time'&&!normalizeTimeInput(String(answer.value))){setError(question.text+' — 시간은 09:00, 10:30처럼 시:분 형식으로 입력해주세요.');return;}
   if(!answer||responseType(question)!=='number')continue;
   const value=Number(answer.value),minimum=typeof response?.minimum==='number'?response.minimum:undefined,maximum=typeof response?.maximum==='number'?response.maximum:undefined,step=response?.step;
   if(!Number.isFinite(value)||minimum!==undefined&&value<minimum||maximum!==undefined&&value>maximum){setError(question.text+' — 안내된 범위 안의 숫자를 입력해주세요.');return;}
   if(typeof step==='number'&&step>0){const position=(value-(minimum??0))/step;if(Math.abs(position-Math.round(position))>1e-8){setError(question.text+' — '+step+' 단위로 입력해주세요.');return;}}
  }
  inFlight.current=true;setBusy(true);setError('');
  try{await onSubmit(answers);}
  catch(err){setError((err as Error).message);}
  finally{inFlight.current=false;setBusy(false);}
 }
 if(!questions.length)return null;
 return <form className="clarification-form" aria-label="요청사항 확인" aria-busy={busy} onSubmit={submit}>
  <div className="clarification-form-heading"><strong>{questions.some(question=>question.required!==false)?'워크플로우 생성에 필요한 '+questions.filter(question=>question.required!==false).length+'개 항목을 확인해주세요.':'원하는 세부 설정을 선택해주세요.'}</strong><p>입력한 답변만 먼저 적용할 수 있습니다. 비워 둔 필수 항목은 다음 대화에서 다시 묻습니다. 선택사항은 건너뛸 수 있습니다.</p></div>
  {rows.map(({key,index,question})=>{
   const type=responseType(question),value=valueFor(key,question),fieldId=formId+'-'+index;
   const options=Array.from(new Set(question.options??[])).filter(option=>option.trim()&&option.trim()!=='직접 입력');
   const choices=(type==='single_choice'||type==='multi_choice')&&!!question.options?.length;
   return <fieldset key={key} className="clarification-form-question" disabled={locked}>
    <legend>{question.group&&<span className="clarification-question-group">{question.group}</span>}<span>{question.text}</span></legend>
    {question.reason&&<p className="clarification-form-reason">{question.reason}</p>}
    {choices?<>
     <div className="clarification-choice-control"><div className="clarification-options">{options.map((option,optionIndex)=>{
      const checked=value.selected.includes(option)&&!(type==='single_choice'&&value.customSelected);
      return <label key={option} className={'clarification-option'+(checked?' selected':'')} htmlFor={fieldId+'-'+optionIndex}>
       <input id={fieldId+'-'+optionIndex} type={type==='multi_choice'?'checkbox':'radio'} name={fieldId} value={option} checked={checked} onChange={event=>change(key,question,{selected:type==='single_choice'?[option]:event.target.checked?[...value.selected,option]:value.selected.filter(item=>item!==option),...(type==='single_choice'?{customSelected:false}:{})})}/><span>{option}</span>
      </label>;
     })}<label className={'clarification-option'+(value.customSelected?' selected':'')} htmlFor={fieldId+'-custom-choice'}>
      <input id={fieldId+'-custom-choice'} type={type==='multi_choice'?'checkbox':'radio'} name={fieldId} checked={value.customSelected} onChange={event=>change(key,question,{customSelected:event.target.checked})}/><span>직접 입력</span>
     </label></div>{!value.customSelected&&<strong className={'clarification-input-marker '+(question.required===false?'optional':'required')}>{question.required===false?'선택':'필수'}</strong>}</div>
     {value.customSelected&&<div className="clarification-value clarification-custom-value"><label className="sr-only" htmlFor={fieldId+'-custom'}>{question.text} 직접 입력</label><textarea id={fieldId+'-custom'} rows={2} maxLength={2000} value={value.custom} onChange={event=>change(key,question,{custom:event.target.value})} placeholder="원하는 조건이나 형식을 적어주세요."/><strong className={'clarification-input-marker '+(question.required===false?'optional':'required')}>{question.required===false?'선택':'필수'}</strong></div>}
     <p className="clarification-form-hint">{type==='multi_choice'?'여러 항목을 선택할 수 있습니다.':'한 가지를 선택하세요.'}</p>
    </>:<div className="clarification-value"><label className="sr-only" htmlFor={fieldId}>{question.text}</label>{type==='time'?<input id={fieldId} className="clarification-time-input" type="text" inputMode="text" maxLength={5} placeholder="09:00" value={value.text} onChange={event=>change(key,question,{text:event.target.value})} aria-describedby={fieldId+'-time-hint'}/>:type==='number'?<input id={fieldId} className="clarification-number-input" type="number" value={value.text} min={question.response?.minimum} max={question.response?.maximum} step={question.response?.step??'any'} onChange={event=>change(key,question,{text:event.target.value})} aria-describedby={question.response?.unit?fieldId+'-unit':undefined}/>:<textarea id={fieldId} rows={2} maxLength={2000} value={value.text} onChange={event=>change(key,question,{text:event.target.value})}/>} {question.response?.unit&&type!=='time'&&<span id={fieldId+'-unit'}>{question.response.unit}</span>}<strong className={'clarification-input-marker '+(question.required===false?'optional':'required')}>{question.required===false?'선택':'필수'}</strong></div>}{type==='time'&&<p id={fieldId+'-time-hint'} className="clarification-form-hint">24시간 기준 시:분 입력 · 예: 09:00, 10:30 · 서울 표준시</p>}
   </fieldset>;
  })}
  {error&&<p className="clarification-error" role="alert">{error}</p>}
  <div className="clarification-form-submit"><small>{enteredAnswers.length}개 항목 입력됨</small><button type="submit" className="as-button as-primary" disabled={locked||!answers.length}>{busy?'답변 적용 중…':enteredAnswers.length?'답변 적용':'선택사항 건너뛰기'}</button></div>
 </form>;
}
