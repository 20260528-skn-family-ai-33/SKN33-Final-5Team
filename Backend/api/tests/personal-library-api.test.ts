import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdir,mkdtemp,readdir,rm,writeFile} from 'node:fs/promises';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {syncBuiltinESMExports} from 'node:module';
import {join,resolve,sep} from 'node:path';
import {PassThrough,Writable} from 'node:stream';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';
import {DeleteObjectCommand,GetObjectCommand,PutObjectCommand,S3Client} from '@aws-sdk/client-s3';
import {agentPython,PROJECT_ROOT,withDataDirectory} from '../src/paths';
import type {Store} from '../src/store';
import {withTeamLibraryAccess} from '../src/team-library-access';

const previous={...process.env};
Object.assign(process.env,{A4B_STORAGE:'sqlite',A4B_DOCUMENT_ROUTING:'0',A4B_S3_BUCKET:'test-personal-library',AWS_REGION:'ap-northeast-2',EASYFLOW_PUBLIC_URL:'http://localhost'});
delete process.env.A4B_BUSINESS_RECORDS;
const {handleAgentBuilder}=await import('../src/agent-builder');

async function sandbox(){
 const runtime=join(PROJECT_ROOT,'runtime');await mkdir(runtime,{recursive:true});
 return mkdtemp(join(runtime,'personal-library-api-'));
}
async function cleanup(directory:string){
 assert.ok(resolve(directory).startsWith(resolve(PROJECT_ROOT,'runtime')+sep));
 await rm(directory,{recursive:true,force:true});
}
async function invoke(directory:string,method:string,path:string,body?:unknown){
 const headers:Record<string,unknown>={};let status=0,result:any,bytes:Buffer|undefined;
 const response={setHeader(name:string,value:unknown){headers[name]=value;},writeHead(code:number,values:Record<string,unknown>){status=code;Object.assign(headers,values);},end(value?:Buffer|string){bytes=value===undefined?undefined:Buffer.from(value);}} as unknown as ServerResponse;
 const handled=await withDataDirectory(directory,()=>handleAgentBuilder(
  {method} as IncomingMessage,response,new URL('http://localhost/api/agent-builder/'+path),
  {getCredential(){throw new Error('Standalone library must not request model credentials');}} as unknown as Store,
  async(_request,max)=>{assert.ok(max);assert.ok(Buffer.byteLength(JSON.stringify(body??{}))<=max!);return body;},
  (_response,code,value)=>{status=code;result=value;})).catch(error=>{throw new Error(method+' '+path+': '+(error as Error).message,{cause:error});});
 assert.equal(handled,true);return {status,result,headers,bytes};
}
function mockObjects(t:any){
 const objects=new Map<string,Buffer>();
 t.mock.method(S3Client.prototype,'send',async(command:PutObjectCommand|GetObjectCommand|DeleteObjectCommand)=>{
  const key=command.input.Key!;
  assert.match(key,/^app\/v1\/users\/[0-9a-f-]{36}\/builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/);
  if(command instanceof PutObjectCommand)objects.set(key,Buffer.from(command.input.Body as Buffer));
  if(command instanceof GetObjectCommand)return {Body:{transformToByteArray:()=>objects.get(key)}};
  if(command instanceof DeleteObjectCommand)objects.delete(key);
  return {};
 });return objects;
}

test('personal library API uploads CSV and text without a design session, manages folders and isolates members',
 {skip:!existsSync(agentPython()),timeout:150000},async t=>{
 const root=await sandbox(),alice=join(root,'users',randomUUID()),bob=join(root,'users',randomUUID());
 process.env.EASYFLOW_DATA_DIR=root;
 const objects=mockObjects(t);
 try{
  const created=await invoke(alice,'POST','folders',{name:'업무 자료',parent_id:null});
  assert.equal(created.status,200);const folder=created.result.folder;
  const nested=(await invoke(alice,'POST','folders',{name:'월간',parent_id:folder.id})).result.folder;
  const csv=Buffer.from('name,amount\nAlice,1200\n'),text=Buffer.from('확인한 고객 안내문입니다.');
  const table=(await invoke(alice,'POST','library_upload',{fileName:'목록.csv',data:csv.toString('base64'),folder_id:folder.id})).result.asset;
  const document=(await invoke(alice,'POST','library_upload',{fileName:'안내.txt',data:text.toString('base64'),folder_id:null})).result.asset;
  assert.equal(table.folder_id,folder.id);assert.equal(document.folder_id,null);assert.equal(objects.size,2);
  assert.deepEqual((await invoke(alice,'GET','library?folder_id='+folder.id)).result.assets.map((a:any)=>a.id),[table.id]);
  assert.deepEqual((await invoke(alice,'GET','library')).result.assets.map((a:any)=>a.id),[document.id]);
  const all=(await invoke(alice,'GET','assets')).result.assets;
  assert.equal(all.length,2,'legacy chooser still includes files from every folder');
  assert.ok(all.every((a:any)=>a.usages.length===0),'standalone uploads create no hidden design sessions');
  assert.deepEqual((await invoke(alice,'GET','asset_preview?id='+table.id)).result.rows,[{name:'Alice',amount:'1200'}]);
  assert.equal((await invoke(alice,'GET','asset_preview?id='+document.id)).result.text,text.toString());
  assert.deepEqual((await invoke(alice,'GET','original?id='+table.id)).bytes,csv);
  assert.deepEqual((await invoke(alice,'GET','original?id='+document.id)).bytes,text);
  assert.equal((await invoke(bob,'GET','library')).result.assets.length,0);
  assert.equal((await invoke(bob,'GET','library')).result.folders.length,0);
  await assert.rejects(invoke(bob,'GET','library?folder_id='+folder.id),/폴더/);
  await assert.rejects(invoke(bob,'POST','asset_move',{id:table.id,folder_id:null}),/자료/);
  await assert.rejects(invoke(bob,'GET','original?id='+table.id),/자료/);
  await assert.rejects(invoke(alice,'DELETE','folders?id='+folder.id),/폴더|자료|비어/);
  const renamed=(await invoke(alice,'PATCH','folders',{id:folder.id,name:'공통 업무'})).result.folder;
  assert.equal(renamed.name,'공통 업무');
  const moved=(await invoke(alice,'POST','asset_move',{id:table.id,folder_id:nested.id})).result.asset;
  assert.equal(moved.id,table.id);assert.equal(moved.original_path,table.original_path);
  assert.equal(objects.size,2,'moving a folder reference never copies S3 bytes');
  assert.deepEqual((await invoke(alice,'GET','original?id='+table.id)).bytes,csv);
  assert.equal((await invoke(alice,'PATCH','folders',{id:nested.id,parent_id:null})).status,200);
  assert.deepEqual((await invoke(alice,'DELETE','folders?id='+folder.id)).result,{deleted:true});
  assert.deepEqual((await invoke(alice,'GET','library?folder_id='+nested.id+'&q='+encodeURIComponent('목록'))).result.assets.map((a:any)=>a.id),[table.id]);
  const before=objects.size;
  await assert.rejects(invoke(alice,'POST','library_upload',{fileName:'미지원.bad',data:'invalid',folder_id:randomUUID()}),/폴더/);
  assert.equal(objects.size,before,'missing destination is rejected before parsing/storing bytes');
  const db=new DatabaseSync(join(alice,'agent-builder.sqlite'),{readOnly:true});
  try{assert.equal(db.prepare('SELECT count(*) AS count FROM builder_sessions').get()!.count,0);}finally{db.close();}
  await invoke(alice,'DELETE','assets?id='+table.id);await invoke(alice,'DELETE','assets?id='+document.id);
  assert.equal(objects.size,0);
  assert.equal((await invoke(alice,'GET','assets')).result.assets.length,0);
 }finally{await cleanup(root);}
});

test('personal library routes reject unsupported methods and client ownership before starting workers',async()=>{
 const root=await sandbox();
 try{
  for(const [method,path,allow] of [['POST','library','GET'],['GET','library_upload','POST'],['GET','folders','POST, PATCH, DELETE'],['PATCH','asset_move','POST']]){
   const response=await invoke(root,method,path);
   assert.equal(response.status,405);assert.equal(response.headers.Allow,allow);
  }
  for(const [method,path,body] of [
   ['GET','library?workspace_id='+randomUUID(),undefined],['GET','library?before=0',undefined],
   ['GET','library?before=9007199254740992',undefined],['GET','library?folder_id=bad',undefined],
   ['GET','library?folder_id='+randomUUID()+'&folder_id='+randomUUID(),undefined],
   ['GET','library?q='+encodeURIComponent('가'.repeat(201)),undefined],
   ['GET','library?q='+encodeURIComponent('첫째\u2028둘째'),undefined],
   ['POST','library_upload',{fileName:'x.txt',data:'eA==',owner_id:randomUUID()}],
   ['POST','folders',{name:'x',team_id:randomUUID()}],['POST','folders',{name:'a/b'}],
   ['POST','folders',{name:'첫째\u2028둘째'}],['POST','folders',{name:'숨김\u200b'}],
   ['PATCH','folders',{id:randomUUID(),name:'x',parent_id:null}],
   ['POST','asset_move',{id:randomUUID()}],['DELETE','folders?id=bad',undefined]
  ] as const)await assert.rejects(invoke(root,method,path,body));
  assert.deepEqual(await readdir(root),[],'validation never invokes storage or a model');
 }finally{await cleanup(root);}
});

test('an upload whose destination disappears cleans unregistered raw and S3 files but preserves committed originals',async t=>{
 const root=await sandbox(),directory=join(root,'users',randomUUID()),objects=mockObjects(t);
 process.env.EASYFLOW_DATA_DIR=root;
 const python=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;
 let committed=false,original='';
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  child.kill=()=>true;let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);let response:any;
   if(payload.action==='validate_destination')response={ok:true,result:{folder_id:payload.folder_id}};
   else if(payload.action==='library_upload'){
    original=payload.asset.original_path;assert.ok(existsSync(join(directory,original)));assert.equal(objects.size,1);
    response={ok:false,error:committed?'응답이 유실됐어요.':'폴더가 삭제됐어요.'};
   }else{assert.equal(payload.action,'original_registered');response={ok:true,result:{registered:committed}};}
   queueMicrotask(()=>{child.stdout.end(JSON.stringify(response));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 try{
  const upload=()=>invoke(directory,'POST','library_upload',{fileName:'목록.csv',data:Buffer.from('name\nAlice\n').toString('base64'),folder_id:randomUUID()});
  await assert.rejects(upload(),/폴더가 삭제/);assert.equal(objects.size,0);assert.equal(existsSync(join(directory,original)),false);
  committed=true;
  await assert.rejects(upload(),/응답이 유실/);assert.equal(objects.size,1);assert.equal(existsSync(join(directory,original)),true);
 }finally{
  if(python===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=python;
  t.mock.restoreAll();syncBuiltinESMExports();await cleanup(root);
 }
});

test('standalone upload removes analysis artifacts after parser and table validation failures',async t=>{
 const root=await sandbox(),directory=join(root,'users',randomUUID()),objects=mockObjects(t);
 process.env.EASYFLOW_DATA_DIR=root;
 const previousPython=process.env.A4B_AGENT_PYTHON,previousRouting=process.env.A4B_DOCUMENT_ROUTING;
 process.env.A4B_AGENT_PYTHON=process.execPath;process.env.A4B_DOCUMENT_ROUTING='1';
 const source='constructor\nvalue\n',workerActions:string[]=[];let failParser=true,analysisDirectory='';
 t.mock.method(childProcess,'spawn',(_python:string,args:string[])=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  child.kill=()=>true;
  if(args[0].endsWith('analyze_document.py')){
   analysisDirectory=args[4];assert.ok(existsSync(join(analysisDirectory,'input.csv')));
   queueMicrotask(async()=>{
    const document_analysis={contract_version:'a4b.document_analysis.v1',source_sha256:createHash('sha256').update(source).digest('hex'),source_format:'csv',
     status:'succeeded',review_required:false,review_reasons:[],blocks:[],units:[],routes:[],warnings:[]};
    await writeFile(args[2],JSON.stringify(failParser?{error:'분석에 실패했어요.'}:{text:source,pages:[source],sections:[],tables:[],warnings:[],page_count:1,
     engine:'native',parser_version:'test',parser_status:'succeeded',document_analysis}));
    child.stdout.end();child.stderr.end();child.exitCode=0;child.emit('close');
   });
  }else{
   let input='';child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
    const payload=JSON.parse(input);workerActions.push(payload.action);assert.equal(payload.action,'validate_destination');
    queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:{folder_id:null}}));child.exitCode=0;child.emit('close');});done();
   }});
  }
  return child;
 });syncBuiltinESMExports();
 try{
  const upload=()=>invoke(directory,'POST','library_upload',{fileName:'invalid.csv',data:Buffer.from(source).toString('base64'),folder_id:null});
  await assert.rejects(upload(),/분석에 실패/);assert.equal(existsSync(analysisDirectory),false);
  failParser=false;
  await assert.rejects(upload(),/열 이름/);assert.equal(existsSync(analysisDirectory),false);
  assert.equal(objects.size,0);assert.equal(existsSync(join(directory,'builder-originals')),false);
  assert.deepEqual(workerActions,['validate_destination','validate_destination'],'parser failures never register or reconcile an original');
 }finally{
  for(const [key,value] of [['A4B_AGENT_PYTHON',previousPython],['A4B_DOCUMENT_ROUTING',previousRouting]]){
   if(value===undefined)delete process.env[key!];else process.env[key!]=value;
  }
  t.mock.restoreAll();syncBuiltinESMExports();await cleanup(root);
 }
});

test('revoking team upload permission still reconciles and removes its unregistered original',async t=>{
 const root=await sandbox(),directory=join(root,'teams',randomUUID());
 process.env.EASYFLOW_DATA_DIR=root;
 const objects=new Map<string,Buffer>(),actions:string[]=[];
 t.mock.method(S3Client.prototype,'send',async(command:PutObjectCommand|DeleteObjectCommand)=>{
  const key=command.input.Key!;assert.match(key,/^app\/v1\/teams\/[0-9a-f-]{36}\//);
  if(command instanceof PutObjectCommand)objects.set(key,Buffer.from(command.input.Body as Buffer));
  if(command instanceof DeleteObjectCommand)objects.delete(key);return {};
 });
 const python=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);actions.push(payload.action);
   assert.ok(['validate_destination','original_registered'].includes(payload.action),'revoked uploads never reach registration');
   const result=payload.action==='original_registered'?{registered:false}:{folder_id:null};
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 let checks=0;
 try{
  await assert.rejects(withTeamLibraryAccess(async()=>{if(++checks>1)throw new Error('자료를 변경할 권한이 없습니다.');},
   ()=>invoke(directory,'POST','library_upload',{fileName:'팀.csv',data:Buffer.from('name\nAlice\n').toString('base64')})),/권한/);
  assert.deepEqual(actions,['validate_destination','original_registered']);assert.equal(objects.size,0);
  assert.deepEqual(await readdir(join(directory,'builder-originals')),[]);
 }finally{
  if(python===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=python;
  t.mock.restoreAll();syncBuiltinESMExports();await cleanup(root);
 }
});

test.after(()=>{for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);});
