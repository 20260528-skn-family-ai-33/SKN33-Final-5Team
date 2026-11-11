import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import './clarification-question.css';
import {normalizeTimeInput} from '../lib/time-input';

export type QuestionResponse={type:'single_choice'|'multi_choice'|'number'|'text'|'time';minimum?:number;maximum?:number;step?:number;unit?:string};
export type ClarificationQuestionData={id?:string;key?:string;text:string;options?:string[];response?:QuestionResponse;group?:string|null;reason?:string|null;required?:boolean};

export function ClarificationQuestion({question,disabled=false,onAnswer}:{
 question:ClarificationQuestionData;
 disabled?:boolean;
 onAnswer:(answer:string)=>void|Promise<void>;
}) {
 const fieldId=useId(),inFlight=useRef(false);
 const [value,setValue]=useState(''),[selected,setSelected]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const questionKey=JSON.stringify([question.id,question.text,question.options,question.response]);
 useEffect(()=>{setValue('');setSelected([]);setError('');},[questionKey]);
 const response=question.response,type=response?.type??(question.options?.length?'single_choice':'text');
 const options=Array.from(new Set(question.options??[])).filter(option=>option.trim());
 const choices=(type==='single_choice'||type==='multi_choice')&&options.length>0;
 const locked=disabled||busy;
 const answer=choices?selected.join(', '):value.trim();
 async function submit(event:FormEvent<HTMLFormElement>) {
  event.preventDefault();
  if(inFlight.current||disabled||!answer)return;
  if(type==='time'&&!normalizeTimeInput(answer)){setError('시간은 09:00, 10:30처럼 시:분 형식으로 입력해주세요.');return;}
  if(type==='number') {
   const numeric=Number(value);
   if(!Number.isFinite(numeric)||response?.minimum!==undefined&&numeric<response.minimum||response?.maximum!==undefined&&numeric>response.maximum) {
    setError('안내된 범위 안의 숫자를 입력해주세요.');return;
   }
  }
  inFlight.current=true;setBusy(true);setError('');
  try {await onAnswer(type==='time'?normalizeTimeInput(answer)!:answer);}
  catch(err){setError((err as Error).message);}
  finally{inFlight.current=false;setBusy(false);}
 }
 function choose(option:string,checked:boolean) {
  setSelected(prior=>type==='single_choice'?[option]:checked?[...prior,option]:prior.filter(item=>item!==option));setError('');
 }
 return <form className="clarification-question" onSubmit={submit} aria-busy={busy}>
  <fieldset disabled={locked}><legend>{question.text}</legend>
   {choices?<div className="clarification-options">{options.map((option,index)=><label key={option} className={'clarification-option'+(selected.includes(option)?' selected':'')} htmlFor={fieldId+'-'+index}>
    <input id={fieldId+'-'+index} type={type==='multi_choice'?'checkbox':'radio'} name={fieldId} value={option} checked={selected.includes(option)} onChange={event=>choose(option,event.target.checked)}/><span>{option}</span>
   </label>)}</div>:<div className="clarification-value"><label className="sr-only" htmlFor={fieldId}>{question.text}</label>{type==='time'?<input id={fieldId} className="clarification-time-input" type="text" inputMode="text" required maxLength={5} placeholder="09:00" value={value} onChange={event=>{setValue(event.target.value);setError('');}}/>:type==='number'?<input id={fieldId} className="clarification-number-input" type="number" required value={value} min={response?.minimum} max={response?.maximum} step={response?.step??'any'} onChange={event=>{setValue(event.target.value);setError('');}} aria-describedby={response?.unit?fieldId+'-unit':undefined}/>:<textarea id={fieldId} value={value} required rows={2} maxLength={4000} onChange={event=>{setValue(event.target.value);setError('');}}/>}{response?.unit&&<span id={fieldId+'-unit'}>{response.unit}</span>}</div>}
  </fieldset>
  {error&&<p className="clarification-error" role="alert">{error}</p>}
  <div className="clarification-submit"><small>{type==='multi_choice'?'해당하는 항목을 모두 선택하세요.':choices?'항목을 선택한 뒤 확인해주세요.':type==='number'?'숫자를 입력한 뒤 확인해주세요.':'답변을 입력한 뒤 확인해주세요.'}</small><button type="submit" className="as-button as-primary" disabled={locked||!answer}>{busy?'답변 보내는 중…':'답변 확인'}</button></div>
 </form>;
}
