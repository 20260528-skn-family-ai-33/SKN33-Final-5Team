"use client";
import {useRef,useState} from 'react';
import {Upload,Loader2,X,FileText} from 'lucide-react';
import {request} from './connection-panel';
import {toast} from 'sonner';
import type {Json} from '@/lib/workflow';

export function FileInput({onChange,onBusyChange}:{onChange:(input:Json[]|null)=>void;onBusyChange?:(busy:boolean)=>void}) {
 const input=useRef<HTMLInputElement>(null);
 const [name,setName]=useState('');const [busy,setBusy]=useState(false);
 async function choose(file:File){
  if(file.size>8*1024*1024){toast.error('8MB 이하의 파일을 선택해주세요.');return;}
  setBusy(true);onBusyChange?.(true);try{
   const encoded=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]||'');reader.onerror=()=>reject(new Error('파일을 읽지 못했어요.'));reader.readAsDataURL(file);});
   const result=await request<{input:Json[]}>('/api/files',{data:encoded,fileName:file.name,mimeType:file.type||'application/octet-stream',property:'data'});onChange(result.input);setName(file.name);toast.success('시작 파일을 준비했어요.');
  }catch(error){toast.error((error as Error).message);}finally{setBusy(false);onBusyChange?.(false);if(input.current)input.current.value='';}
 }
 return <div className="field"><label>파일로 시작하기 (선택)</label><input ref={input} type="file" className="hidden" aria-label="시작 파일 선택" onChange={event=>{const file=event.target.files?.[0];if(file)void choose(file);}}/><div className="flex items-center gap-2 flex-wrap"><button className="btn" type="button" disabled={busy} onClick={()=>input.current?.click()}>{busy?<Loader2 className="animate-spin"/>:<Upload/>}{busy?'파일 준비 중…':'파일 선택'}</button>{name&&<><span className="text-sm break-all flex items-center gap-2"><FileText size={15}/>{name}</span><button className="icon-btn" aria-label="시작 파일 제거" onClick={()=>{setName('');onChange(null);}}><X size={16}/></button></>}</div><small>8MB 이하의 파일을 올리면 ‘data’ 파일 항목으로 전달해요. CSV·Excel·PDF·JSON·텍스트는 ‘파일에서 데이터 읽기’, Office 등 여러 형식은 ‘여러 형식 문서 읽기’ 단계로 연결하세요. Docling은 별도 Python 환경이 필요해요.</small></div>;
}
