import {AsyncLocalStorage} from 'node:async_hooks';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {withDataDirectory,baseDataDirectory,dataDirectory} from './paths';
import {withTeamLibraryAccess} from './team-library-access';
import {readWorkspaceFile} from './workspace-files';

export type ScheduleSourceSelection={
 mode:'fixed'|'latest'|'new'|'all';space:'personal'|'team';team_id:string|null;folder_id:string|null;
 asset_ids:string[];target_node_id:string;revision?:number;
};
export type SourceWorker=(payload:Record<string,unknown>)=>Promise<any>;
export type ScheduleSourceContext={directory:string;ownerId?:string;authorizeOwner?:()=>Promise<unknown>;authorizeTeam?:(teamId:string)=>Promise<unknown>};
const contexts=new AsyncLocalStorage<ScheduleSourceContext>();
export const withScheduleSourceContext=<T>(context:ScheduleSourceContext,action:()=>T):T=>contexts.run(context,action);
const uuid=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const selectionKeys=new Set(['mode','space','team_id','folder_id','asset_ids','target_node_id','revision']);
function identifier(value:unknown,label:string){if(typeof value!=='string'||!uuid.test(value))throw new Error(label+'를 확인하세요.');return value.toLowerCase();}
export function sourceRevision(value:unknown):number {
 if(!Number.isSafeInteger(value)||Number(value)<0)throw new Error('자료 연결 버전을 확인하세요.');return Number(value);
}
export function parseScheduleSourceSelection(value:unknown):ScheduleSourceSelection|null {
 if(value===null)return null;
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('자료 연결 설정을 확인하세요.');
 const data=value as Record<string,unknown>;
 if(Object.keys(data).some(key=>!selectionKeys.has(key)))throw new Error('자료 연결 설정에 지원하지 않는 항목이 있습니다.');
 if(typeof data.mode!=='string'||!['fixed','latest','new','all'].includes(data.mode)||typeof data.space!=='string'||!['personal','team'].includes(data.space))throw new Error('자료 선택 방식과 저장 공간을 확인하세요.');
 const space=data.space as ScheduleSourceSelection['space'];
 if(space==='personal'&&data.team_id!=null)throw new Error('개인 자료에는 팀 ID를 지정할 수 없습니다.');
 const team_id=space==='team'?identifier(data.team_id,'팀 ID'):null;
 const folder_id=data.folder_id==null?null:identifier(data.folder_id,'폴더 ID');
 if(!Array.isArray(data.asset_ids)||data.asset_ids.length>100)throw new Error('연결할 파일은 최대 100개 선택하세요.');
 const asset_ids=data.asset_ids.map(id=>identifier(id,'자료 ID'));
 if(new Set(asset_ids).size!==asset_ids.length)throw new Error('같은 자료를 중복 선택할 수 없습니다.');
 if(data.mode==='fixed'?!asset_ids.length:!!asset_ids.length)throw new Error('고정 파일은 파일을 선택하고, 최신·새 파일·전체 함께 읽기는 폴더를 선택하세요.');
 if(typeof data.target_node_id!=='string'||!data.target_node_id.trim()||data.target_node_id.length>120||/[\u0000-\u001f]/.test(data.target_node_id))throw new Error('자료를 읽을 작업을 선택하세요.');
 return {mode:data.mode as ScheduleSourceSelection['mode'],space,team_id,folder_id,asset_ids,target_node_id:data.target_node_id,
  ...(data.revision===undefined?{}:{revision:sourceRevision(data.revision)})};
}
export async function authorizeScheduleSource(selection:ScheduleSourceSelection) {
 const context=contexts.getStore();
 if(!context)throw new Error('자료 연결 실행 환경을 확인하지 못했습니다.');
 await context.authorizeOwner?.();
 if(selection.space==='team'){
  if(!context.ownerId||!context.authorizeTeam)throw new Error('팀 자료를 사용하려면 로그인하세요.');
  await context.authorizeTeam(selection.team_id!);
 }
}

async function inSourceSpace<T>(selection:ScheduleSourceSelection,action:()=>Promise<T>):Promise<T> {
 const context=contexts.getStore();if(!context)throw new Error('자료 연결 실행 환경을 확인하지 못했습니다.');
 await authorizeScheduleSource(selection);
 const directory=selection.space==='team'?baseDataDirectory('teams',selection.team_id!):context.directory;
 const run=()=>withDataDirectory(directory,action);
 return selection.space==='team'?withTeamLibraryAccess(()=>authorizeScheduleSource(selection),run):run();
}

/** Reads the existing original only to verify the exported snapshot; never registers a personal copy. */
async function verifyOriginals(selection:ScheduleSourceSelection,assets:any[]) {
 return inSourceSpace(selection,async()=>{
  let total=0;
  for(const item of assets){
   const asset=item?.asset,path=asset?.original_path;
   if(typeof path!=='string'||!/^builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/.test(path))throw new Error('연결한 자료의 원본 파일을 확인하지 못했습니다.');
   await authorizeScheduleSource(selection);
   const bytes=await readWorkspaceFile(path,()=>readFile(dataDirectory(path)));
   total+=bytes.length;
   if(!bytes.length||bytes.length>8*1024*1024||total>80*1024*1024)throw new Error('연결한 자료의 원본 크기를 확인하세요.');
   if(typeof asset.hash==='string'&&createHash('sha256').update(bytes).digest('hex')!==asset.hash)throw new Error('자료 원본이 변경되었습니다. 연결할 파일을 다시 확인하세요.');
  }
  await authorizeScheduleSource(selection);
 });
}

export function scheduleSourceContext(){return contexts.getStore();}

const sourceActions=new Set(['test','scheduled_run','retry_run','resume_run','schedule','schedule_test']);
/** The same owner-scoped resolver prepares manual, queued and resumed executions. */
export async function prepareScheduleSource(payload:Record<string,unknown>,worker:SourceWorker):Promise<{payload:Record<string,unknown>;result?:any;selection?:ScheduleSourceSelection}> {
 if(!contexts.getStore()||!sourceActions.has(String(payload.action))||payload.action==='schedule'&&payload.active!==true)return {payload};
 // An explicit manual upload belongs to this run; the saved reservation source remains unchanged.
 if(payload.action==='test'&&Array.isArray(payload.file_ids)&&payload.file_ids.length)return {payload};
 const plan=await worker({action:'library_source_plan',source_action:payload.action,id:payload.id,...(payload.active===undefined?{}:{active:payload.active})});
 if(plan.selection==null)return {payload};
 const selection=parseScheduleSourceSelection(plan.selection)!;
 if(!['table','document'].includes(plan.target_kind))throw new Error('자료를 읽을 작업 종류를 확인하세요.');
 await authorizeScheduleSource(selection);
 let refs=plan.refs,run_id=plan.run_id;
 if(plan.needs_resolve||!Array.isArray(refs)){
  const metadata=await inSourceSpace(selection,()=>worker({action:'library_source_export',selection,target_kind:plan.target_kind}));
  if(!Array.isArray(metadata.candidates))throw new Error('자료 목록을 확인하지 못했습니다.');
  await authorizeScheduleSource(selection);
  const resolved=await worker({action:'library_source_resolve',source_action:payload.action,id:payload.id,selection,
   ...(payload.lease_token?{lease_token:payload.lease_token}:{}),candidates:metadata.candidates,...(run_id?{run_id}:{})});
  if(resolved.status==='expanded'||resolved.status==='skipped'){await authorizeScheduleSource(selection);return {payload,result:resolved,selection};}
  if(!Array.isArray(resolved.refs))throw new Error('실행할 자료를 선택하지 못했습니다.');
  refs=resolved.refs;run_id=resolved.run_id||run_id;
 }
 if(!Array.isArray(refs))throw new Error('실행할 자료를 확인하지 못했습니다.');
 if(selection.mode==='all'&&(!refs.length||refs.length>100))throw new Error('폴더 전체 함께 읽기는 파일 1~100개를 사용합니다. 폴더의 파일 수를 확인하세요.');
 const exportRefs=(selected:any[])=>inSourceSpace(selection,()=>worker({action:'library_source_export',selection,target_kind:plan.target_kind,refs:selected,
  ...(plan.pinned_snapshot?{verify_only:true}:{})}));
 let exported:any;
 if(!plan.pinned_snapshot&&refs.length>1){
  const assets:any[]=[];let bytes=0;
  for(let offset=0;offset<refs.length;offset+=4){
   const batches=await Promise.all(refs.slice(offset,offset+4).map((ref:any)=>exportRefs([ref])));
   for(const batch of batches){
    if(!Array.isArray(batch.assets))throw new Error('자료 내용을 확인하지 못했습니다.');
    bytes+=Buffer.byteLength(JSON.stringify(batch.assets));
    if(bytes>80*1024*1024)throw new Error('자료 스냅샷이 너무 큽니다. 고정 파일 수를 줄이거나 새 파일 모드를 사용하세요.');
    assets.push(...batch.assets);
   }
  }
  exported={assets,refs};
 }else exported=await exportRefs(refs);
 if(plan.pinned_snapshot){
  if(!Array.isArray(exported.originals))throw new Error('고정된 자료 원본을 확인하지 못했습니다.');
  await verifyOriginals(selection,exported.originals);
 }else{
  if(!Array.isArray(exported.assets))throw new Error('자료 내용을 확인하지 못했습니다.');
  await verifyOriginals(selection,exported.assets);
 }
 await authorizeScheduleSource(selection);
 const snapshot={selection,refs,...(plan.pinned_snapshot?{verified_only:true}:{assets:exported.assets})};
 return {payload:{...payload,...(run_id&&payload.action==='test'?{run_id}:{}),library_source_snapshot:snapshot},selection};
}

/** Recheck membership after the execution worker has waited for a process slot. */
export async function assertScheduleSourcePayload(payload:Record<string,unknown>) {
 const snapshot=payload.library_source_snapshot as {selection?:unknown}|undefined;
 if(snapshot?.selection)await authorizeScheduleSource(parseScheduleSourceSelection(snapshot.selection)!);
}

export async function validateScheduleSourceSelection(selection:ScheduleSourceSelection,targetKind:'table'|'document',worker:SourceWorker) {
 const result=await inSourceSpace(selection,()=>worker({action:'library_source_export',selection,target_kind:targetKind}));
 if(!Array.isArray(result.candidates))throw new Error('연결할 자료 목록을 확인하지 못했습니다.');
 if(selection.mode==='fixed'&&selection.asset_ids.some(id=>!result.candidates.some((asset:any)=>asset.id===id)))throw new Error('선택한 저장 공간에서 자료를 찾을 수 없습니다.');
 await authorizeScheduleSource(selection);return result;
}

/** Resolve an internal run alias on the server, then read only its original authorized workspace. */
export async function readScheduleSourceOriginal(runId:string,assetId:string,worker:SourceWorker):Promise<{asset:Record<string,any>;bytes:Buffer}> {
 const plan=await worker({action:'library_source_original_plan',run_id:runId,asset_id:assetId});
 if(!plan?.selection||!plan.asset||!plan.ref)throw new Error('해당 실행에 사용한 원본 자료를 찾을 수 없습니다.');
 const selection=parseScheduleSourceSelection(plan.selection)!;
 if(plan.asset.id!==plan.ref.id)throw new Error('실행 원본의 자료 식별자가 일치하지 않습니다.');
 await inSourceSpace(selection,()=>worker({action:'library_source_export',selection,
  target_kind:plan.asset.kind==='table'?'table':'document',refs:[plan.ref],verify_only:true}));
 return inSourceSpace(selection,async()=>{
  const asset=plan.asset,path=asset.original_path;
  if(typeof path!=='string'||!/^builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/.test(path))throw new Error('실행 원본 파일의 저장 경로를 확인하지 못했습니다.');
  const bytes=await readWorkspaceFile(path,()=>readFile(dataDirectory(path)));
  if(!bytes.length||bytes.length>8*1024*1024)throw new Error('실행 원본 파일의 크기를 확인하세요.');
  if(typeof asset.hash!=='string'||createHash('sha256').update(bytes).digest('hex')!==asset.hash)throw new Error('실행 당시 원본 파일을 확인하지 못했습니다.');
  await authorizeScheduleSource(selection);return {asset,bytes};
 });
}
