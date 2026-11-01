"use client";
import {StickyNote,Trash2} from 'lucide-react';
import {toast} from 'sonner';
import {Sheet,SheetContent,SheetHeader,SheetTitle,SheetDescription} from '@/components/ui/sheet';
import {Select,SelectTrigger,SelectValue,SelectContent,SelectItem} from '@/components/ui/select';
import type {FlowNode,Json} from '@/lib/workflow';

export function NoteEditor({draft,open,onClose,onChange,onApply,onDelete}:{draft:FlowNode;open:boolean;onClose:()=>void;onChange:(node:FlowNode)=>void;onApply:(node:FlowNode)=>boolean;onDelete:()=>void}) {
 const parameters=draft.parameters;
 const update=(values:Json)=>onChange({...draft,parameters:{...parameters,...values}});
 function apply() {
  if(typeof parameters.content!=='string'||parameters.content.length>20000){toast.error('메모 내용은 20,000자까지 입력할 수 있어요.');return;}
  const color=Number(parameters.color??1),width=Number(parameters.width??240),height=Number(parameters.height??160);
  if(!Number.isInteger(color)||color<1||color>7||!Number.isFinite(width)||width<240||width>3000||!Number.isFinite(height)||height<160||height>3000){toast.error('메모 크기는 가로 240~3,000, 세로 160~3,000 안에서 입력해주세요.');return;}
  if(onApply({...draft,parameters:{...parameters,color,width,height}}))toast.success('메모를 적용했어요.');
 }
 return <Sheet open={open} onOpenChange={value=>{if(!value)onClose();}}><SheetContent className="note-editor-sheet"><SheetHeader><SheetTitle><StickyNote size={21}/>{draft.name}</SheetTitle><SheetDescription>작업 설명을 캔버스에 남겨요. 메모는 실행되지 않아요.</SheetDescription></SheetHeader><div className="node-params">
  <div className="field"><label htmlFor="note-name">메모 이름</label><input id="note-name" value={draft.name} onChange={event=>onChange({...draft,name:event.target.value})}/></div>
  <div className="field"><label htmlFor="note-content">메모 내용</label><textarea id="note-content" rows={10} maxLength={20000} value={String(parameters.content??'')} onChange={event=>update({content:event.target.value})}/><small># 제목, **강조**, 목록을 사용할 수 있어요. 캔버스에서도 직접 편집해요.</small></div>
  <div className="field"><label htmlFor="note-color">메모 색상</label><Select value={String(parameters.color??1)} onValueChange={color=>update({color:Number(color)})}><SelectTrigger id="note-color"><SelectValue/></SelectTrigger><SelectContent>{['노랑','갈색','빨강','초록','파랑','보라','회색'].map((color,index)=><SelectItem key={index} value={String(index+1)}>{color}</SelectItem>)}</SelectContent></Select></div>
  <div className="field"><label htmlFor="note-width">가로 크기</label><input id="note-width" type="number" min="240" max="3000" value={parameters.width??240} onChange={event=>update({width:event.target.value===''?'':Number(event.target.value)})}/></div>
  <div className="field"><label htmlFor="note-height">세로 크기</label><input id="note-height" type="number" min="160" max="3000" value={parameters.height??160} onChange={event=>update({height:event.target.value===''?'':Number(event.target.value)})}/><small>오른쪽 아래 모서리를 끌어 크기를 바꿀 수도 있어요.</small></div>
  <div className="node-editor-actions"><button className="btn primary" onClick={apply}>메모 적용</button><button className="icon-btn" onClick={onDelete} aria-label="메모 삭제"><Trash2/></button></div>
 </div></SheetContent></Sheet>;
}
