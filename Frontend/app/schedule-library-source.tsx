import {useEffect,useRef,useState} from 'react';
import {FolderOpen,RefreshCw,Save} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {useAuth} from './auth';
import {teamLibraryBase,useTeamSpaces} from './team-library';
import './schedule-library-source.css';

export type ScheduleLibrarySource={
 mode:'fixed'|'latest'|'new';space:'personal'|'team';team_id:string|null;folder_id:string|null;
 asset_ids:string[];target_node_id:string;revision:number;
};
export type SourceReader={id:string;label:string;kind:string};
type Folder={id:string;name:string;parent_id:string|null};
type Asset={id:string;name:string;format?:string;kind?:string;capabilities?:string[];created_at?:string;pending_delete?:boolean;processing_status?:string};
type LibraryPage={folders:Folder[];assets:Asset[];next:string|null};
const modeLabels={fixed:'고정 파일',latest:'최신 파일',new:'새 파일만 처리'};
const modeDescriptions={
 fixed:'선택한 파일을 매번 읽습니다. 같은 이름으로 새 파일을 올려도 대상은 바뀌지 않습니다.',
 latest:'예약이 실행될 때 선택한 폴더에서 가장 최근에 등록된 파일 하나를 읽습니다.',
 new:'선택한 폴더에서 아직 성공적으로 처리하지 않은 파일을 찾아 파일별로 실행합니다. 실패한 파일은 완료 처리하지 않습니다.',
};

function folderLabel(folder:Folder,folders:Folder[]) {
 const names=[folder.name],seen=new Set([folder.id]);let parent=folder.parent_id;
 while(parent&&!seen.has(parent)){
  seen.add(parent);const item=folders.find(item=>item.id===parent);if(!item)break;names.unshift(item.name);parent=item.parent_id;
 }
 return names.join(' / ');
}

export function ScheduleLibrarySourceSettings({workflowId,readers,disabled,onSaved,onDirtyChange,onBusyChange}:{
 workflowId:string;readers:SourceReader[];disabled:boolean;onSaved:(changed:boolean)=>Promise<void>;
 onDirtyChange?:(dirty:boolean)=>void;onBusyChange?:(busy:boolean)=>void;
}) {
 const auth=useAuth(),spaces=useTeamSpaces(auth.enabled&&!!auth.user&&!auth.loading);
 const [selection,setSelection]=useState<ScheduleLibrarySource|null>(null),[loading,setLoading]=useState(true),[loadFailed,setLoadFailed]=useState(false);
 const [mode,setMode]=useState<ScheduleLibrarySource['mode']>('fixed'),[teamId,setTeamId]=useState(''),[folderId,setFolderId]=useState<string|null>(null);
 const [target,setTarget]=useState(readers[0]?.id||''),[assetIds,setAssetIds]=useState<string[]>([]);
 const [folders,setFolders]=useState<Folder[]>([]),[assets,setAssets]=useState<Asset[]>([]),[next,setNext]=useState<string|null>(null);
 const [listLoading,setListLoading]=useState(false),[listError,setListError]=useState(''),[saving,setSaving]=useState(false),[dirty,setDirty]=useState(false);
 const [error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0),listGeneration=useRef(0),writing=useRef(false),mounted=useRef(true);
 const endpoint=teamLibraryBase(teamId);
 const reader=readers.find(item=>item.id===target);
 function changed(){setDirty(true);onDirtyChange?.(true);setNotice('');setError('');}
 function apply(value:ScheduleLibrarySource|null){
  setSelection(value);setMode(value?.mode||'fixed');setTeamId(value?.team_id||'');setFolderId(value?.folder_id||null);
  setAssetIds(value?.asset_ids||[]);setTarget(value?.target_node_id||readers[0]?.id||'');setDirty(false);onDirtyChange?.(false);
 }
 async function loadSelection(){
  const token=++generation.current;setLoading(true);setError('');setLoadFailed(false);
  try{const result=await request<{selection:ScheduleLibrarySource|null}>('/api/agent-builder/schedule_source?id='+encodeURIComponent(workflowId));if(token===generation.current)apply(result.selection);}
  catch(error){if(token===generation.current){setError((error as Error).message);setLoadFailed(true);}}
  finally{if(token===generation.current)setLoading(false);}
 }
 useEffect(()=>{mounted.current=true;void loadSelection();return()=>{mounted.current=false;generation.current++;listGeneration.current++;};},[workflowId]);
 async function loadFiles(before?:string|null){
  const token=++listGeneration.current;setListLoading(true);setListError('');
  const query=new URLSearchParams();if(folderId)query.set('folder_id',folderId);if(before)query.set('before',before);
  try{
   const result=await request<LibraryPage>(endpoint+'library'+(query.size?'?'+query.toString():''));
   if(token!==listGeneration.current)return;
   setFolders(result.folders||[]);setAssets(current=>before?[...current,...result.assets]:result.assets);setNext(result.next);
  }catch(error){if(token===listGeneration.current)setListError((error as Error).message);}
  finally{if(token===listGeneration.current)setListLoading(false);}
 }
 useEffect(()=>{
  if(loading||loadFailed)return;
  setAssets([]);setNext(null);void loadFiles();return()=>{listGeneration.current++;};
 },[endpoint,folderId,loading,loadFailed]);
 function chooseFile(id:string){
  changed();setAssetIds(current=>current.includes(id)?current.filter(value=>value!==id):reader?.kind==='table'?[id]:[...current,id].slice(0,100));
 }
 async function save(){
  if(writing.current||loading||loadFailed||listLoading||listError||!target||mode==='fixed'&&!assetIds.length)return;
  writing.current=true;setSaving(true);onBusyChange?.(true);setError('');setNotice('');
  try{
   const result=await request<{selection:ScheduleLibrarySource}>('/api/agent-builder/schedule_source',{
    id:workflowId,base_revision:selection?.revision||0,
    selection:{mode,space:teamId?'team':'personal',team_id:teamId||null,folder_id:folderId,asset_ids:mode==='fixed'?assetIds:[],target_node_id:target},
   });
   if(!mounted.current)return;
   const changedRevision=result.selection.revision!==(selection?.revision||0);
   setSelection(result.selection);setDirty(false);onDirtyChange?.(false);
   setNotice('자료함 연결을 저장했습니다. 설정이 바뀌면 예약이 중지되므로 확인한 뒤 다시 켜세요.');
   try{await onSaved(changedRevision);}catch(error){if(mounted.current)setError('설정은 저장했지만 화면을 갱신하지 못했습니다. '+(error as Error).message);}
  }catch(error){if(mounted.current)setError((error as Error).message);}
  finally{writing.current=false;if(mounted.current){setSaving(false);onBusyChange?.(false);}}
 }
 const choices=assets.filter(asset=>!asset.pending_delete&&(!asset.kind||asset.kind===reader?.kind||asset.capabilities?.includes(reader?.kind||'')));
 const rootName=teamId?(spaces.teams.find(team=>team.id===teamId)?.name||'팀 자료함'):'내 자료함';
 const folder=folders.find(item=>item.id===folderId);
 return <section className="schedule-library-source" aria-labelledby="schedule-library-heading">
  <div className="schedule-source-heading"><h3 id="schedule-library-heading"><FolderOpen size={16} aria-hidden="true"/>자료함에서 읽기</h3><button type="button" className="as-text-button" disabled={disabled||saving||loading||dirty} onClick={()=>void loadSelection()} aria-label="예약 자료 설정 새로고침"><RefreshCw size={14}/></button></div>
  {loading?<p role="status">예약 자료 설정을 불러오고 있습니다.</p>:loadFailed?<div role="alert"><p>{error}</p><button type="button" className="as-button" onClick={()=>void loadSelection()}>자료 설정 다시 불러오기</button></div>:<>
   {selection&&<p className="schedule-source-saved">저장된 읽기 방식: <strong>{modeLabels[selection.mode]}</strong></p>}
   <fieldset disabled={disabled||saving} className="schedule-source-fields">
    <div className="schedule-source-options"><label>읽기 방식<select aria-label="예약 자료 읽기 방식" value={mode} onChange={event=>{changed();setMode(event.target.value as ScheduleLibrarySource['mode']);}}><option value="fixed">고정 파일</option><option value="latest">최신 파일</option><option value="new">새 파일만 처리</option></select></label>
    <label>자료를 읽을 작업<select aria-label="자료를 읽을 작업" value={target} onChange={event=>{changed();setTarget(event.target.value);setAssetIds([]);}}>{readers.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    <label>자료 공간<select aria-label="예약 자료 공간" value={teamId} onChange={event=>{changed();setTeamId(event.target.value);setFolderId(null);setAssetIds([]);}}><option value="">내 자료함</option>{spaces.teams.map(team=><option key={team.id} value={team.id}>{team.name}</option>)}{teamId&&!spaces.teams.some(team=>team.id===teamId)&&<option value={teamId}>저장된 팀 · 권한 확인 필요</option>}</select></label>
    <label>대상 폴더<select aria-label="예약 자료 폴더" value={folderId||''} disabled={listLoading||!!listError} onChange={event=>{changed();setFolderId(event.target.value||null);setAssetIds([]);}}><option value="">{rootName} · 최상위</option>{folders.map(item=><option key={item.id} value={item.id}>{folderLabel(item,folders)}</option>)}</select></label></div>
    <p className="schedule-source-description">{modeDescriptions[mode]}</p>
    {mode==='new'&&<p className="schedule-source-description">연결할 공간·폴더·작업을 바꾸어 저장하면 처리 기준이 새로 시작되어, 이전에 처리한 파일도 다시 대상이 될 수 있습니다.</p>}
    {mode!=='fixed'&&<p className="schedule-source-description">대상: {rootName}{folder?' / '+folderLabel(folder,folders):' / 최상위'}의 파일 · 하위 폴더 제외</p>}
    {teamId&&<p className="schedule-source-description">팀 원본을 실행할 때 읽고 접근 권한을 다시 확인합니다. 실행에 사용한 파일과 버전은 기록으로 보관합니다.</p>}
    {spaces.error&&<p role="alert">{spaces.error}</p>}
    {listError&&<p role="alert">{listError} <button type="button" className="as-text-button" onClick={()=>void loadFiles()}>자료 목록 다시 불러오기</button></p>}
    {mode==='fixed'&&<div className="schedule-source-files" aria-label="고정 파일 선택">{choices.map(asset=><label key={asset.id}><input type="checkbox" checked={assetIds.includes(asset.id)} disabled={!assetIds.includes(asset.id)&&reader?.kind!=='table'&&assetIds.length>=100} onChange={()=>chooseFile(asset.id)}/><span>{asset.name}<small>{asset.format?.toUpperCase()}{asset.processing_status==='partial'?' · 일부 분석됨':''}</small></span></label>)}
     {!listLoading&&!listError&&!choices.length&&<p>이 폴더에 선택할 수 있는 파일이 없습니다.</p>}
     {assetIds.length>0&&<p className="schedule-source-description">선택한 파일 {assetIds.length}개{reader?.kind==='table'?' · 표는 한 파일씩 처리합니다.':''}</p>}
    </div>}
    {listLoading&&<p role="status">자료 목록을 불러오고 있습니다.</p>}
    {mode==='fixed'&&next&&!listError&&<button type="button" className="as-text-button" disabled={listLoading} onClick={()=>void loadFiles(next)}>자료 더 보기</button>}
    <button type="button" className="as-button" disabled={!dirty||listLoading||!!listError||!target||mode==='fixed'&&!assetIds.length} onClick={()=>void save()}><Save size={14}/>{saving?'저장 중…':'자료함 연결 저장'}</button>
   </fieldset>
   {error&&<p role="alert">{error}</p>}{notice&&<p role="status" className="schedule-source-notice">{notice}</p>}
  </>}
 </section>;
}
