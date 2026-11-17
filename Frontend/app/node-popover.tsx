import {WorkspaceIconButton} from './workspace-icon-button';
import {useEffect,useRef,useState} from 'react';
import {XIcon} from 'lucide-react';
import type {Workflow} from './workflow-preview';
import type {ToolInfo} from './node-library';
import {NodeInspector} from './node-inspector';

type EditValue=string|number|null;
export type NodeEditRequest={request:string;nodeId:string;label:string;changes:{path:string;oldValue:EditValue;newValue:EditValue}[]};
type GuidedField={path:string;label:string;value:string;rawValue:string|number;kind:'number'|'text'};
type ComparisonMode='number'|'column';
type NumericComparison={index:number;field:string;value:number|string|null;compareField:string|null;mode:ComparisonMode};
const editableTextKeys=new Set(['query','channel_id','value_column','sum_field','field','compare_field','format','output_format','null_policy','model']);
const fieldLabels:Record<string,string>={max_results:'조회 한도',limit:'한도',count:'건수',threshold:'기준값',query:'검색 조건',channel_id:'채널 ID',value_column:'계산할 열',sum_field:'합계 열',field:'열 이름',compare_field:'비교할 열',format:'결과 형식',output_format:'결과 형식',null_policy:'빈 값 처리',model:'모델',temperature:'모델 온도'};
const safeKey=(key:string)=>/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)&&
 !/(?:secret|token|password|credential|api.?key|authorization)/i.test(key)&&
 !['__proto__','prototype','constructor'].includes(key);
function numericComparisons(settings:Record<string,unknown>):NumericComparison[]{
 const plan=settings.execution_plan;
 if(!plan||typeof plan!=='object'||Array.isArray(plan)||!Array.isArray((plan as {filters?:unknown}).filters))return [];
 return (plan as {filters:unknown[]}).filters.slice(0,5).flatMap((entry,index)=>{
  if(!entry||typeof entry!=='object'||Array.isArray(entry))return [];
  const filter=entry as Record<string,unknown>;
  if(filter.value_type!=='number'||['is_empty','not_empty'].includes(String(filter.operator)))return [];
  const value=filter.value,compareField=filter.compare_field;
  const numeric=typeof value==='number'&&Number.isFinite(value)||typeof value==='string'&&value.length<=32&&value.trim()!==''&&Number.isFinite(Number(value));
  const column=typeof compareField==='string'&&compareField.length<=200&&compareField.trim()!=='';
  if(numeric===column)return [];
  return [{index,field:typeof filter.field==='string'?filter.field:'',value:numeric?value as number|string:null,
   compareField:column?compareField as string:null,mode:column?'column' as const:'number' as const}];
 });
}
function guidedFields(settings:Record<string,unknown>,comparisons:NumericComparison[]):GuidedField[]{
 let args:unknown;
 try{args=typeof settings.arguments_json==='string'?JSON.parse(settings.arguments_json):null;}catch{args=null;}
 const fields:GuidedField[]=[];
 if(args&&typeof args==='object'&&!Array.isArray(args))for(const [key,value] of Object.entries(args)){
  if(!safeKey(key))continue;
  const kind=typeof value==='number'&&Number.isFinite(value)?'number':typeof value==='string'&&editableTextKeys.has(key)&&value.length<=200?'text':null;
  if(kind)fields.push({path:'arguments_json.'+key,label:fieldLabels[key]||key,value:String(value),rawValue:value as string|number,kind});
 }
 const plan=settings.execution_plan;
 if(plan&&typeof plan==='object'&&!Array.isArray(plan)&&Array.isArray((plan as {filters?:unknown}).filters)){
  const filters=(plan as {filters:unknown[]}).filters;
  for(const [index,entry] of filters.slice(0,5).entries()){
   if(!entry||typeof entry!=='object'||Array.isArray(entry))continue;
   const filter=entry as Record<string,unknown>,prefix=`조건 ${index+1} · `;
   for(const key of ['field','value','compare_field']){
    if(comparisons.some(comparison=>comparison.index===index)&&key!=='field')continue;
    const value=filter[key];
    if(!safeKey(key)||!['string','number'].includes(typeof value)||typeof value==='number'&&!Number.isFinite(value))continue;
    fields.push({path:`execution_plan.filters[${index}].${key}`,label:prefix+(key==='value'?'기준값':fieldLabels[key]||key),value:String(value),rawValue:value as string|number,kind:typeof value==='number'?'number':'text'});
   }
  }
 }
 return fields.slice(0,10);
}
function changeRequest(node:Workflow['nodes'][number],fields:GuidedField[],comparisons:NumericComparison[],
 values:Record<string,string>,modes:Record<number,ComparisonMode>){
 const editChanges:NodeEditRequest['changes']=[];
 const changes=fields.flatMap(field=>{
  const entered=values[field.path];
  if(entered===undefined||entered===field.value)return [];
  const value=field.kind==='number'?entered.trim():entered;
  if(value===field.value)return [];
  editChanges.push({path:field.path,oldValue:field.rawValue,newValue:field.kind==='number'?Number(value):value});
  return [`- ${field.label} (${field.path}): ${field.value} → ${value||'(비움)'}`];
 });
 for(const comparison of comparisons){
  const mode=modes[comparison.index]??comparison.mode;
  const prefix=`execution_plan.filters[${comparison.index}]`;
  const numericText=values[prefix+'.value']??String(comparison.value??'');
  const columnText=values[prefix+'.compare_field']??comparison.compareField??'';
  const nextValue=mode==='number'?Number(numericText):null;
  const nextColumn=mode==='column'?columnText:null;
  if(mode===comparison.mode&&
   (mode==='number'?Number(comparison.value)===nextValue:comparison.compareField===nextColumn))continue;
  const oldValue=comparison.value===null?'null':JSON.stringify(comparison.value);
  const oldColumn=comparison.compareField===null?'null':JSON.stringify(comparison.compareField);
  editChanges.push({path:prefix+'.value',oldValue:comparison.value,newValue:nextValue},
   {path:prefix+'.compare_field',oldValue:comparison.compareField,newValue:nextColumn});
  const label=`조건 ${comparison.index+1}${comparison.field?' ('+comparison.field+')':''}`;
  changes.push(`- ${label}: ${mode==='number'?'숫자 직접 입력':'비교 열'}로 설정. ${prefix}.value: ${oldValue} → ${nextValue===null?'null':nextValue}; `+
   `${prefix}.compare_field: ${oldColumn} → ${nextColumn===null?'null':JSON.stringify(nextColumn)}; value_type은 number로 유지.`);
 }
 const target='`'+node.label+'` 작업 (노드 ID: '+node.id+')';
 const request=changes.length?`${target}의 설정을 다음과 같이 바꿔줘.\n${changes.join('\n')}\n다른 작업의 설정은 유지해줘.`:
  `${target}의 설정을 다음과 같이 바꿔줘: `;
 return {request,nodeId:node.id,label:node.label,changes:editChanges} satisfies NodeEditRequest;
}

export function NodePopover({node,workflow,tool,placement,onClose,onModify,returnFocus}:{
 node:Workflow['nodes'][number];workflow:Workflow;tool?:ToolInfo;
 placement:'canvas'|'run';onClose:()=>void;onModify?:(edit:NodeEditRequest)=>void;returnFocus?:HTMLElement|null;
}){
 const root=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;
 const comparisons=numericComparisons(node.settings),fields=guidedFields(node.settings,comparisons);
 const [values,setValues]=useState<Record<string,string>>({}),[modes,setModes]=useState<Record<number,ComparisonMode>>({});
 const invalidNumber=fields.some(field=>field.kind==='number'&&values[field.path]!==undefined&&
  (values[field.path].trim()===''||!Number.isFinite(Number(values[field.path]))));
 const invalidComparison=comparisons.some(comparison=>{
  const prefix=`execution_plan.filters[${comparison.index}]`,mode=modes[comparison.index]??comparison.mode;
  const value=mode==='number'?values[prefix+'.value']??String(comparison.value??''):values[prefix+'.compare_field']??comparison.compareField??'';
  return mode==='number'?value.trim()===''||!Number.isFinite(Number(value)):value.trim()==='';
 });
 useEffect(()=>{
  let restoreFocus=true;
  root.current?.querySelector<HTMLButtonElement>('button')?.focus({preventScroll:true});
  const outside=(event:PointerEvent)=>{
   if(root.current&&!event.composedPath().includes(root.current)){restoreFocus=false;close.current();}
  };
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();close.current();}};
  window.addEventListener('keydown',escape);
  document.addEventListener('pointerdown',outside,true);
  return()=>{window.removeEventListener('keydown',escape);document.removeEventListener('pointerdown',outside,true);if(restoreFocus)returnFocus?.focus({preventScroll:true});};
 },[node.id,returnFocus]);
 return <div ref={root} className={'as-node-popover '+(placement==='run'?'is-run':'is-canvas')}
  role="dialog" aria-modal="false" aria-label={node.label+' 작업 설정'}>
  <WorkspaceIconButton type="button" data-slot="dialog-close" aria-label="닫기" onClick={onClose}
   className="as-node-popover-close"><XIcon/></WorkspaceIconButton>
  <div className="as-node-popover-heading"><strong>작업 설정</strong><span>{onModify?'변경은 채팅에서 적용':'읽기 전용'}</span></div>
  <div className="as-node-popover-content"><NodeInspector node={node} workflow={workflow} tool={tool}
   onClose={onClose} showBackButton={false} footerNote={onModify?'값을 입력해도 아직 저장되지 않습니다. 아래 버튼으로 설계 대화에 변경 요청을 준비하세요.':'이 실행 시점의 설정을 읽기 전용으로 보여줍니다.'}/>
   {onModify&&(fields.length>0||comparisons.length>0)&&<fieldset className="as-node-edit-fields"><legend>값 변경 요청</legend>
    {fields.map(field=><label key={field.path}>{field.label}<small>{field.path}</small>
     <input data-setting-path={field.path} type={field.kind==='number'?'number':'text'} step={field.kind==='number'?'any':undefined} maxLength={field.kind==='text'?200:32}
      value={values[field.path]??field.value} onChange={event=>setValues(current=>({...current,[field.path]:event.target.value}))}/></label>)}
    {comparisons.map(comparison=>{
     const prefix=`execution_plan.filters[${comparison.index}]`,mode=modes[comparison.index]??comparison.mode;
     const path=prefix+(mode==='number'?'.value':'.compare_field');
     return <div key={comparison.index} data-comparison-index={comparison.index}>
      <strong>조건 {comparison.index+1}{comparison.field?' · '+comparison.field:''}의 비교 기준</strong>
      <div role="group" aria-label={`조건 ${comparison.index+1} 비교 방식`} style={{display:'flex',gap:6,margin:'6px 0'}}>
       <button type="button" className="as-button" aria-pressed={mode==='number'}
        style={{flex:1,background:mode==='number'?'var(--as-soft)':undefined,borderColor:mode==='number'?'var(--as-accent)':undefined}}
        onClick={()=>setModes(current=>({...current,[comparison.index]:'number'}))}>숫자 직접 입력</button>
       <button type="button" className="as-button" aria-pressed={mode==='column'}
        style={{flex:1,background:mode==='column'?'var(--as-soft)':undefined,borderColor:mode==='column'?'var(--as-accent)':undefined}}
        onClick={()=>setModes(current=>({...current,[comparison.index]:'column'}))}>비교 열</button>
      </div>
      <label>{mode==='number'?'비교할 숫자':'비교할 열'}<small>{path}</small>
       <input data-setting-path={path} type={mode==='number'?'number':'text'} step={mode==='number'?'any':undefined}
        maxLength={mode==='number'?32:200} value={values[path]??(mode==='number'?String(comparison.value??''):comparison.compareField??'')}
        onChange={event=>setValues(current=>({...current,[path]:event.target.value}))}/></label>
     </div>;
    })}
    {(invalidNumber||invalidComparison)&&<p role="alert">숫자 또는 비교할 열을 올바르게 입력하세요.</p>}
   </fieldset>}</div>
  {onModify&&<div className="as-node-popover-actions"><button type="button" className="as-button as-primary" disabled={invalidNumber||invalidComparison}
   onClick={()=>onModify(changeRequest(node,fields,comparisons,values,modes))}>채팅에 변경 요청</button></div>}
 </div>;
}
