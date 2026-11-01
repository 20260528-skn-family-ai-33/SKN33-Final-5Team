"use client";
import {Plus,Trash2} from 'lucide-react';
import {Switch} from '@/components/ui/switch';
import {Select,SelectContent,SelectItem,SelectTrigger,SelectValue} from '@/components/ui/select';
import {clone,type Json} from '@/lib/workflow';

export function MergeMatchFields({parameters:p,input=[],inputPortCounts,onChange}:{parameters:Json;input?:Json[];inputPortCounts?:number[];onChange:(parameters:Json)=>void}) {
 const different=p.advanced===true||p.advanced==='true';
 const pairs=Array.isArray(p.mergeByFields?.values)?p.mergeByFields.values:[];
 const safeKeys=(items:Json[])=>[...new Set(items.flatMap(item=>Object.keys(item)).filter(key=>!['__easyflowBinary','__proto__','prototype','constructor'].includes(key)))].slice(0,100);
 const counts=Array.isArray(inputPortCounts)&&inputPortCounts.length<=10&&inputPortCounts.every(count=>Number.isSafeInteger(count)&&count>=0)&&inputPortCounts.reduce((total,count)=>total+count,0)===input.length?inputPortCounts:undefined;
 let offset=0;const ports=counts?.map(count=>{const items=input.slice(offset,offset+count);offset+=count;return items;});
 const keys=ports?safeKeys(ports[0]??[]).filter(key=>safeKeys(ports[1]??[]).includes(key)):safeKeys(input);
 const portKeys=(index:number)=>ports?safeKeys(ports[index]??[]):safeKeys(input);
 const edit=(index:number,key:string,value:string)=>{const next=clone(p);next.mergeByFields.values[index][key]=value;onChange(next);};
 const field=(index:number,key:'field1'|'field2')=><div className="field"><label htmlFor={`merge-${key}-${index}`}>입력 {key==='field1'?1:2}의 기준 항목</label><input id={`merge-${key}-${index}`} value={String(pairs[index]?.[key]??'')} onChange={event=>edit(index,key,event.target.value)} placeholder={key==='field1'?'customerId':'buyerId'}/>{portKeys(key==='field1'?0:1).length>0&&<Select onValueChange={value=>edit(index,key,value)}><SelectTrigger><SelectValue placeholder="받은 항목 이름에서 선택"/></SelectTrigger><SelectContent>{portKeys(key==='field1'?0:1).map(value=><SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>}</div>;
 return <div className="field"><label className="inline-switch"><Switch checked={different} onCheckedChange={value=>{const next=clone(p);next.advanced=value;if(value&&!Array.isArray(next.mergeByFields?.values)){const fields=typeof p.fieldsToMatchString==='string'?p.fieldsToMatchString.split(',').map((field:string)=>field.trim()).filter(Boolean):[];next.mergeByFields={values:fields.length?fields.map((field:string)=>({field1:field,field2:field})):[{field1:'',field2:''}]};}if(!value&&pairs.length)next.fieldsToMatchString=pairs.map((pair:any)=>pair.field1).join(',');onChange(next);}}/>두 입력의 항목 이름이 달라요</label>
  {different?<>{pairs.map((pair:any,index:number)=>pair&&typeof pair==='object'&&!Array.isArray(pair)?<div className="assignment" key={index}><div className="assignment-head"><b>비교 기준 {index+1}</b><button className="icon-btn ml-auto" aria-label={`비교 기준 ${index+1} 삭제`} onClick={()=>{const next=clone(p);next.mergeByFields.values.splice(index,1);onChange(next);}}><Trash2/></button></div>{field(index,'field1')}{field(index,'field2')}</div>:<p className="error-text" key={index}>이 비교 기준의 형식은 전체 매개변수에서 확인해주세요.</p>)}<button className="btn ghost full" disabled={pairs.length>=50} onClick={()=>{const next=clone(p);next.mergeByFields??={};next.mergeByFields.values??=[];next.mergeByFields.values.push({field1:'',field2:''});onChange(next);}}><Plus/>비교 기준 추가</button></>:<><label htmlFor="merge-same-fields">같은 값을 찾을 항목 이름</label><input id="merge-same-fields" value={String(p.fieldsToMatchString??'')} onChange={event=>onChange({...p,fieldsToMatchString:event.target.value})} placeholder="customerId"/>{keys.length>0&&<Select onValueChange={value=>onChange({...p,fieldsToMatchString:value})}><SelectTrigger><SelectValue placeholder="받은 항목 이름에서 선택"/></SelectTrigger><SelectContent>{keys.map(value=><SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>}</>}
  <small>여러 기준은 모두 같아야 일치해요. 같은 이름 여러 개는 쉼표로 구분하고, 중첩 항목은 customer.id로 적어요. 위 입력의 번호는 캔버스 연결점과 같아요.</small>
 </div>;
}
