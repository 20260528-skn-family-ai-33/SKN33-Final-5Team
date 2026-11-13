import {useRef,useState} from 'react';
import {Folder,FolderPlus,Pencil,Trash2} from 'lucide-react';
import {Dialog,DialogContent,DialogHeader,DialogTitle} from '@/components/ui/dialog';
import {request} from '../components/flow/connection-panel';
import './workflow-folders.css';
export type WorkflowOrganization={revision:number;folders:{id:string;name:string}[];assignments:Record<string,string>};
export const emptyOrganization:WorkflowOrganization={revision:0,folders:[],assignments:{}};
export function WorkflowFolders({organization,onChange,active,onActive,selected,onMoved,disabled}:{organization:WorkflowOrganization;onChange:(next:WorkflowOrganization)=>void;active:string;onActive:(id:string)=>void;selected:string[];onMoved:()=>void;disabled:boolean}){
 const [dialog,setDialog]=useState<'create'|'rename'|'delete'|'move'|null>(null),[value,setValue]=useState(''),[target,setTarget]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const inFlight=useRef(false),folder=organization.folders.find(f=>f.id===active);
 function open(kind:typeof dialog){setError('');setNotice('');setValue(kind==='rename'?folder?.name||'':'');setTarget('');setDialog(kind);}
 async function save(){
  if(inFlight.current)return;inFlight.current=true;setBusy(true);setError('');
  try{
   const result=await request<{organization:WorkflowOrganization}>('/api/agent-builder/workflow_folders',{operation:dialog,revision:organization.revision,folder_id:dialog==='move'?target||null:folder?.id||null,name:value,agent_ids:selected});
   onChange(result.organization);if(dialog==='delete')onActive('unfiled');if(dialog==='move')onMoved();
   setNotice(dialog==='delete'?'폴더를 삭제하고 워크플로우를 미분류로 옮겼습니다.':dialog==='move'?'워크플로우를 이동했습니다.':'폴더를 저장했습니다.');setDialog(null);
  }catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy(false);}
 }
 return <section className="workflow-folders" aria-label="워크플로우 폴더">
  <div className="workflow-folder-list" role="group" aria-label="폴더 선택">
   {[{id:'all',name:'전체'},{id:'unfiled',name:'미분류'},...organization.folders].map(f=><button key={f.id} type="button" className={'as-button'+(active===f.id?' as-secondary':'')} aria-pressed={active===f.id} disabled={busy||disabled} onClick={()=>onActive(f.id)}><Folder size={16}/>{f.name}</button>)}
  </div>
  <div className="workflow-folder-actions">
   <button className="as-button" disabled={busy||disabled} onClick={()=>open('create')}><FolderPlus size={16}/>폴더 만들기</button>
   {folder&&<><button className="as-button" disabled={busy||disabled} onClick={()=>open('rename')}><Pencil size={16}/>폴더 이름 변경</button><button className="as-button" disabled={busy||disabled} onClick={()=>open('delete')}><Trash2 size={16}/>폴더 삭제</button></>}
   <button className="as-button" disabled={busy||disabled||!selected.length} onClick={()=>open('move')}>선택 이동{selected.length?` (${selected.length})`:''}</button>
  </div>
  {notice&&<p role="status">{notice}</p>}
  {dialog&&<Dialog open onOpenChange={v=>{if(!v&&!inFlight.current)setDialog(null);}}><DialogContent className="as-dialog" showCloseButton={!busy} aria-describedby={undefined}><DialogHeader><DialogTitle>{({create:'폴더 만들기',rename:'폴더 이름 변경',delete:'폴더 삭제',move:'워크플로우 이동'})[dialog]}</DialogTitle></DialogHeader>
   <form className="workflow-folder-form" aria-busy={busy} onSubmit={e=>{e.preventDefault();void save();}}>
    {dialog==='delete'?<p>‘{folder?.name}’ 폴더를 삭제할까요? 안의 워크플로우는 미분류로 이동하며 실행 설정과 예약은 유지됩니다.</p>:dialog==='move'?<label>대상 폴더<select value={target} disabled={busy} onChange={e=>setTarget(e.target.value)}><option value="">미분류</option>{organization.folders.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>:<label>폴더 이름<input autoFocus maxLength={60} value={value} disabled={busy} onChange={e=>setValue(e.target.value)}/></label>}
    {error&&<p role="alert" className="as-error">{error}</p>}
    <div><button type="button" className="as-button" disabled={busy} onClick={()=>setDialog(null)}>취소</button><button className="as-button as-primary" type="submit" disabled={busy||((dialog==='create'||dialog==='rename')&&!value.trim())}>{busy?'저장 중…':dialog==='delete'?'폴더 삭제':dialog==='move'?'이동':'저장'}</button></div>
   </form>
  </DialogContent></Dialog>}
 </section>;
}
