import assert from 'node:assert/strict';
import test from 'node:test';
import childProcess from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {mkdtemp,mkdir,writeFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,sep} from 'node:path';
import {PassThrough,Writable} from 'node:stream';
import {syncBuiltinESMExports} from 'node:module';
import type {IncomingMessage,ServerResponse} from 'node:http';
import type {Store} from '../src/store';
import {withDataDirectory,dataDirectory} from '../src/paths';
import {assertTeamLibraryAccess} from '../src/team-library-access';

const previous={...process.env};
Object.assign(process.env,{A4B_STORAGE:'sqlite',EASYFLOW_PUBLIC_URL:'http://localhost',A4B_AGENT_PYTHON:process.execPath});
delete process.env.A4B_BUSINESS_RECORDS;delete process.env.A4B_S3_BUCKET;
const {parseScheduleSourceSelection,sourceRevision,prepareScheduleSource,assertScheduleSourcePayload,withScheduleSourceContext,validateScheduleSourceSelection,readScheduleSourceOriginal}=await import('../src/schedule-source');
const {handleAgentBuilder,builderWorker,resetWorkerSlots}=await import('../src/agent-builder');
const ownerId=randomUUID(),teamId=randomUUID(),assetId=randomUUID(),agentId=randomUUID();
const selection=(space:'personal'|'team'='personal',mode:'fixed'|'latest'|'new'|'all'='fixed')=>({mode,space,team_id:space==='team'?teamId:null,folder_id:null,asset_ids:mode==='fixed'?[assetId]:[],target_node_id:'reader',revision:1});
const ref={id:assetId,name:'같은 이름.csv',kind:'table',version:'effective-parsed-version-1',created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z'};
const original='builder-originals/'+ 'a'.repeat(64)+'.csv';
async function fixture(){
 const root=await mkdtemp(join(tmpdir(),'a4b-source-'));process.env.EASYFLOW_DATA_DIR=root;
 const owner=join(root,'users',ownerId),team=join(root,'teams',teamId);
 for(const [directory,text] of [[owner,'개인 원본'],[team,'팀 원본']]){await mkdir(join(directory,'builder-originals'),{recursive:true});await writeFile(join(directory,original),text);}
 return {root,owner,team,async close(){assert.ok(resolve(root).startsWith(resolve(tmpdir())+sep));await rm(root,{recursive:true,force:true});}};
}
function exported(directory:string,space:'personal'|'team'){
 const text=space==='team'?'팀 원본':'개인 원본';
 return {asset:{id:assetId,name:ref.name,kind:'table',original_path:original,hash:createHash('sha256').update(text).digest('hex')},rows:[{value:text}],identity:{asset_id:assetId,version:ref.version,space,team_id:space==='team'?teamId:null,folder_id:null}};
}

test('source selections reject ownership/path injection and inconsistent modes',()=>{
 assert.deepEqual(parseScheduleSourceSelection(selection()),selection());assert.equal(parseScheduleSourceSelection(null),null);
 assert.equal(sourceRevision(0),0);assert.throws(()=>sourceRevision('0'));assert.throws(()=>sourceRevision(-1));
 for(const invalid of [
  {...selection(),directory:'../someone'}, {...selection(),owner_id:randomUUID()}, {...selection(),team_id:teamId},
  {...selection('team'),team_id:'../outside'}, {...selection(),folder_id:'../folder'}, {...selection(),asset_ids:[]},
  {...selection(),asset_ids:[assetId,assetId]}, {...selection('personal','latest'),asset_ids:[assetId]},
  {...selection(),target_node_id:''}, {...selection(),revision:-1}, {...selection('personal','all'),asset_ids:[assetId]},
 ])assert.throws(()=>parseScheduleSourceSelection(invalid));
 assert.equal(parseScheduleSourceSelection(selection('team','new'))?.space,'team');
 assert.deepEqual(parseScheduleSourceSelection(selection('team','all')),selection('team','all'));
});

test('manual uploads override the saved reservation source for only this execution',async()=>{
 const payload={action:'test',id:agentId,file_ids:[randomUUID()]};
 const result=await withScheduleSourceContext({directory:tmpdir()},()=>prepareScheduleSource(payload,async()=>{
  throw new Error('An explicit manual upload must not resolve the reservation source');
 }));
 assert.deepEqual(result,{payload});
});

for(const space of ['personal','team'] as const)test(`source resolution stays in the ${space} namespace and never creates a personal library copy`,async()=>{
 const files=await fixture(),calls:{action:string;directory:string}[]=[];let checks=0;
 const chosen=selection(space),context={directory:files.owner,ownerId,authorizeOwner:async()=>{},authorizeTeam:async(id:string)=>{assert.equal(id,teamId);checks++;}};
 const worker=async(payload:Record<string,any>)=>{
  calls.push({action:payload.action,directory:dataDirectory()});await assertTeamLibraryAccess();
  if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'table',pinned_snapshot:false,needs_resolve:true};
  if(payload.action==='library_source_resolve'){assert.equal(dataDirectory(),files.owner);assert.deepEqual(payload.selection,chosen);return {status:'resolved',refs:[ref],run_id:'resolved-run'};}
  assert.equal(payload.action,'library_source_export');assert.equal(payload.target_kind,'table');
  assert.equal(dataDirectory(),space==='team'?files.team:files.owner);
  return payload.refs?{assets:[exported(dataDirectory(),space)],refs:payload.refs}:{candidates:[ref]};
 };
 try{
  const result=await withScheduleSourceContext(context,()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action:'test',id:agentId},worker)));
  const snapshot=result.payload.library_source_snapshot as any;
  assert.equal(result.payload.run_id,'resolved-run');assert.equal(snapshot.assets[0].rows[0].value,space==='team'?'팀 원본':'개인 원본');
  assert.equal(snapshot.assets[0].identity.space,space);assert.equal(snapshot.assets[0].identity.version,ref.version);
  assert.deepEqual(await readdir(join(files.owner,'builder-originals')),['a'.repeat(64)+'.csv']);
  assert.deepEqual(calls.map(call=>call.action),['library_source_plan','library_source_export','library_source_resolve','library_source_export']);
  if(space==='team')assert.ok(checks>=4);
 }finally{await files.close();}
});

test('revoked team membership stops resolution and is rechecked again before a prepared worker starts',async()=>{
 const files=await fixture(),chosen=selection('team');let allowed=true,resolved=false;
 const context={directory:files.owner,ownerId,authorizeTeam:async()=>{if(!allowed)throw new Error('팀에서 제외되었습니다.');}};
 const worker=async(payload:Record<string,any>)=>{
  if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'table',needs_resolve:true,pinned_snapshot:false};
  if(payload.action==='library_source_export'){allowed=false;return {candidates:[ref]};}
  resolved=true;throw new Error('resolution should not happen');
 };
 try{
  await assert.rejects(withScheduleSourceContext(context,()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action:'test',id:agentId},worker))),/제외/);
  assert.equal(resolved,false);
  await assert.rejects(withScheduleSourceContext(context,()=>assertScheduleSourcePayload({library_source_snapshot:{selection:chosen}})),/제외/);
  const stopped=await withScheduleSourceContext(context,()=>prepareScheduleSource({action:'schedule',id:agentId,active:false},async()=>{throw new Error('stop must not read a source');}));
  assert.equal(stopped.payload.active,false);
 }finally{await files.close();}
});

for(const mode of ['fixed','all'] as const)test(`${mode} document batches share one run and preserve all parsed snapshots in selected order`,async()=>{
 const files=await fixture(),second={...ref,id:randomUUID(),name:'두 번째 문서'},chosen={...selection('personal',mode),asset_ids:mode==='fixed'?[ref.id,second.id]:[]},batches:any[][]=[];
 const worker=async(payload:Record<string,any>)=>{
  if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'document',needs_resolve:true,pinned_snapshot:false};
  if(payload.action==='library_source_resolve')return {status:'resolved',refs:[ref,second],run_id:randomUUID()};
  if(!payload.refs)return {candidates:[ref,second]};
  batches.push(payload.refs);assert.equal(payload.refs.length,1);
  return {assets:payload.refs.map((item:any)=>({...exported(files.owner,'personal'),asset:{...exported(files.owner,'personal').asset,id:item.id,kind:'document',name:item.name},identity:{...exported(files.owner,'personal').identity,asset_id:item.id}}))};
 };
 try{
  const prepared=await withScheduleSourceContext({directory:files.owner},()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action:'test',id:agentId},worker)));
  assert.equal(batches.length,2);assert.deepEqual((prepared.payload.library_source_snapshot as any).assets.map((item:any)=>item.asset.id),[ref.id,second.id]);
 }finally{await files.close();}
});

for(const count of [0,101])test(`all-file sources reject ${count} files before reading original content`,async()=>{
 const chosen=selection('personal','all');let exports=0;
 await assert.rejects(withScheduleSourceContext({directory:tmpdir()},()=>prepareScheduleSource({action:'test',id:agentId},async payload=>{
  if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'document',refs:Array.from({length:count},()=>ref),needs_resolve:false,pinned_snapshot:false};
  exports++;throw Error('Out-of-range batches must not read originals');
 })),/1~100/);
 assert.equal(exports,0);
});

for(const action of ['retry_run','resume_run'])test(`${action} verifies original access without replacing the run's pinned content`,async()=>{
 const files=await fixture(),chosen=selection('team'),calls:string[]=[];
 try{
  const result=await withScheduleSourceContext({directory:files.owner,ownerId,authorizeTeam:async()=>{}},()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action,id:'saved-run'},async payload=>{
   calls.push(String(payload.action));
   if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'table',refs:[ref],pinned_snapshot:true,needs_resolve:false};
   assert.equal(payload.action,'library_source_export');assert.equal(payload.verify_only,true);assert.deepEqual(payload.refs,[ref]);return {refs:payload.refs,originals:[{asset:exported(files.team,'team').asset}]};
  })));
  assert.deepEqual(calls,['library_source_plan','library_source_export']);
  assert.deepEqual(result.payload.library_source_snapshot,{selection:chosen,refs:[ref],verified_only:true});
 }finally{await files.close();}
});

for(const status of ['expanded','skipped'])test(`new-file ${status} responses return before starting a parent execution`,async()=>{
 const files=await fixture(),chosen=selection('personal','new'),calls:string[]=[];
 const response=status==='expanded'?{status,enqueued_count:2,job_ids:['one','two']}:{status,message:'새 파일이 없습니다.'};
 try{
  const result=await withScheduleSourceContext({directory:files.owner},()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action:'scheduled_run',id:'parent-job',lease_token:'lease'},async payload=>{
   calls.push(String(payload.action));
   if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'table',needs_resolve:true,pinned_snapshot:false};
   if(payload.action==='library_source_export')return {candidates:[ref]};
   assert.equal(payload.action,'library_source_resolve');assert.equal(payload.lease_token,'lease');return response;
  })));
  assert.deepEqual(result.result,response);assert.equal(result.payload.library_source_snapshot,undefined);
  assert.deepEqual(calls,['library_source_plan','library_source_export','library_source_resolve']);
 }finally{await files.close();}
});

test('fixed sources fail when an ID belongs to another space, or the original was removed',async()=>{
 const files=await fixture(),chosen=selection('personal');
 try{
  await assert.rejects(withScheduleSourceContext({directory:files.owner},()=>validateScheduleSourceSelection(chosen,'table',async()=>({candidates:[{...ref,id:randomUUID()}]}))),/저장 공간/);
  await rm(join(files.owner,original));
  await assert.rejects(withScheduleSourceContext({directory:files.owner},()=>withDataDirectory(files.owner,()=>prepareScheduleSource({action:'test',id:agentId},async payload=>{
   if(payload.action==='library_source_plan')return {selection:chosen,target_kind:'table',refs:[ref],needs_resolve:false,pinned_snapshot:false};
   return {assets:[exported(files.owner,'personal')]};
  }))),/ENOENT/);
 }finally{await files.close();}
});

test('the execution bridge preserves the resolved run ID and sends only the authorized snapshot to the worker',async t=>{
 const files=await fixture(),chosen=selection('team'),runId=randomUUID(),payloads:Record<string,any>[]=[];resetWorkerSlots();
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);let result:any;
   if(payload.action==='library_source_plan')result={selection:chosen,target_kind:'table',needs_resolve:true,pinned_snapshot:false};
   else if(payload.action==='library_source_resolve')result={status:'resolved',selection:chosen,refs:[ref],run_id:runId};
   else if(payload.action==='library_source_export')result=payload.refs?{assets:[exported(files.team,'team')],refs:payload.refs}:{candidates:[ref]};
   else{assert.equal(payload.action,'test');result={id:payload.run_id,status:'succeeded'};}
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 try{
  const result=await withScheduleSourceContext({directory:files.owner,ownerId,authorizeTeam:async()=>{}},()=>withDataDirectory(files.owner,()=>builderWorker({action:'test',id:agentId,content:''})));
  assert.equal(result.id,runId);
  const execution=payloads.at(-1)!;assert.equal(execution.run_id,runId);assert.equal(execution.directory,files.owner);assert.match(execution.execution_token,/^[0-9a-f-]{36}$/);
  assert.equal(execution.library_source_snapshot.assets[0].rows[0].value,'팀 원본');
  assert.ok(payloads.filter(payload=>payload.action==='library_source_export').every(payload=>payload.directory===files.team));
  assert.ok(payloads.filter(payload=>payload.action!=='library_source_export').every(payload=>payload.directory===files.owner));
  assert.equal(payloads.some(payload=>payload.action==='library_upload'),false);
 }finally{t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();await files.close();}
});

test('source original aliases preserve a pinned latest-folder selection and read the team snapshot instead of a personal namesake',async()=>{
 const files=await fixture(),runId=randomUUID(),alias=randomUUID(),chosen={...selection('team','latest'),folder_id:randomUUID()},calls:{action:string;directory:string}[]=[];
 const worker=async(payload:Record<string,any>)=>{
  calls.push({action:payload.action,directory:dataDirectory()});
  if(payload.action==='library_source_original_plan'){
   assert.equal(payload.run_id,runId);assert.equal(payload.asset_id,alias);
   return {selection:chosen,ref,asset:{...exported(files.team,'team').asset,format:'csv'}};
  }
  assert.equal(payload.action,'library_source_export');assert.equal(payload.verify_only,true);assert.deepEqual(payload.refs,[ref]);return {originals:[]};
 };
 try{
  const result=await withScheduleSourceContext({directory:files.owner,ownerId,authorizeTeam:async()=>{}},()=>withDataDirectory(files.owner,()=>readScheduleSourceOriginal(runId,alias,worker)));
  assert.equal(result.bytes.toString(),'팀 원본');assert.equal(result.asset.id,assetId);
  assert.deepEqual(calls.map(call=>call.directory),[files.owner,files.team]);
 }finally{await files.close();}
});

test('source original downloads reject another run, revoked membership, unsafe stored paths and missing original bytes',async()=>{
 const files=await fixture(),runId=randomUUID(),alias=randomUUID(),chosen=selection('team');let allowed=true,path=original,exports=0;
 const context={directory:files.owner,ownerId,authorizeTeam:async()=>{if(!allowed)throw new Error('팀 권한이 없습니다.');}};
 const worker=async(payload:Record<string,any>)=>{
  if(payload.action==='library_source_original_plan'){
   if(payload.run_id!==runId||payload.asset_id!==alias)throw new Error('해당 실행의 원본이 없습니다.');
   return {selection:chosen,ref,asset:{...exported(files.team,'team').asset,original_path:path}};
  }
  exports++;return {originals:[]};
 };
 const read=(id=runId)=>withScheduleSourceContext(context,()=>withDataDirectory(files.owner,()=>readScheduleSourceOriginal(id,alias,worker)));
 try{
  await assert.rejects(read(randomUUID()),/해당 실행/);assert.equal(exports,0);
  allowed=false;await assert.rejects(read(),/팀 권한/);assert.equal(exports,0);
  allowed=true;path='../outside.csv';await assert.rejects(read(),/저장 경로/);
  path=original;await rm(join(files.team,original));await assert.rejects(read(),/ENOENT/);
 }finally{await files.close();}
});

test('source API validates selections, preserves revision checks and never accepts workspace overrides',async t=>{
 const files=await fixture(),payloads:Record<string,any>[]=[];let nativeOperation=false;resetWorkerSlots();
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);
   const result=payload.action==='agent'?{agent:{config:{workflow:{nodes:[{id:'reader',tool:'read_table',settings:nativeOperation?{operation:'load_csv'}:{}}]}}}}:
    payload.action==='library_source_original_plan'?{selection:selection('team'),ref,asset:{...exported(files.team,'team').asset,format:'csv'}}:
    payload.action==='library_source_export'?{candidates:[ref]}:{selection:payload.selection?{...payload.selection,revision:2}:null};
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 async function invoke(method:string,body:unknown,query='',name='schedule_source'){
  let status=0,value:any;const headers:Record<string,unknown>={};
  await withScheduleSourceContext({directory:files.owner,ownerId,authorizeTeam:async()=>{}},()=>withDataDirectory(files.owner,()=>handleAgentBuilder(
   {method} as IncomingMessage,{setHeader:(name:string,value:unknown)=>{headers[name]=value;},writeHead:(code:number,values:Record<string,unknown>)=>{status=code;Object.assign(headers,values);},end:(bytes:Buffer)=>{value=bytes;}} as unknown as ServerResponse,
   new URL('http://localhost/api/agent-builder/'+name+query),{} as Store,async()=>body,(_res,code,result)=>{status=code;value=result;})));
  return {status,value,headers};
 }
 try{
  assert.equal((await invoke('GET',undefined,'?id='+agentId)).headers['Cache-Control'],'no-store');
  const saved=await invoke('POST',{id:agentId,base_revision:1,selection:selection()});assert.equal(saved.status,200);assert.equal(saved.value.selection.revision,2);
  const last=payloads.at(-1)!;assert.equal(last.base_revision,1);assert.equal(last.directory,files.owner);assert.equal(last.selection.revision,undefined);
  await invoke('POST',{id:agentId,base_revision:2,selection:null});assert.equal(payloads.at(-1)!.selection,null);
  for(const body of [{id:agentId,base_revision:0,selection:selection(),directory:files.team},{id:agentId,base_revision:-1,selection:selection()},{id:agentId,base_revision:0,selection:{...selection(),asset_ids:[assetId,randomUUID()]}}])await assert.rejects(invoke('POST',body));
  await assert.rejects(invoke('GET',undefined,'?id='+agentId+'&owner_id='+ownerId));
  nativeOperation=true;await assert.rejects(invoke('POST',{id:agentId,base_revision:0,selection:selection()}),/읽을/);
  assert.equal((await invoke('DELETE',undefined)).status,405);
  const query='?run_id='+randomUUID()+'&asset_id='+randomUUID();
  await assert.rejects(invoke('GET',undefined,query+'&space=team','source_original'));
  const original=await invoke('GET',undefined,query,'source_original');assert.equal(original.status,200);assert.equal(original.value.toString(),'팀 원본');
  assert.equal(original.headers['Content-Security-Policy'],'sandbox');assert.equal(original.headers['X-Content-Type-Options'],'nosniff');assert.match(String(original.headers['Content-Disposition']),/^inline/);
  assert.ok(payloads.every(payload=>!('owner_id' in payload)&&!('workspace_id' in payload)));
 }finally{t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();await files.close();}
});

test.after(()=>{for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);resetWorkerSlots();});
