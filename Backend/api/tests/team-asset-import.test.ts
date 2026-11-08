import assert from 'node:assert/strict';
import test from 'node:test';
import {randomUUID} from 'node:crypto';
import {existsSync} from 'node:fs';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import {join,resolve,sep} from 'node:path';
import {DeleteObjectCommand,GetObjectCommand,PutObjectCommand,S3Client} from '@aws-sdk/client-s3';
import {agentPython,PROJECT_ROOT,withDataDirectory} from '../src/paths';

const previous={...process.env};
Object.assign(process.env,{A4B_STORAGE:'sqlite',A4B_DOCUMENT_ROUTING:'0',A4B_S3_BUCKET:'test-team-copy',AWS_REGION:'ap-northeast-2',EASYFLOW_PUBLIC_URL:'http://localhost'});
delete process.env.A4B_BUSINESS_RECORDS;
const {persistLibraryUpload,builderWorker}=await import('../src/agent-builder');
const {importTeamLibraryAsset}=await import('../src/team-asset-import');

test('team originals copy into an isolated personal workspace and revoked membership cannot register a copy',
 {skip:!existsSync(agentPython()),timeout:150000},async t=>{
 const runtime=join(PROJECT_ROOT,'runtime');await mkdir(runtime,{recursive:true});
 const root=await mkdtemp(join(runtime,'team-asset-import-'));
 process.env.EASYFLOW_DATA_DIR=root;
 const teamId=randomUUID(),userId=randomUUID(),teamDir=join(root,'teams',teamId),userDir=join(root,'users',userId);
 const objects=new Map<string,Buffer>();
 t.mock.method(S3Client.prototype,'send',async(command:PutObjectCommand|GetObjectCommand|DeleteObjectCommand)=>{
  const key=command.input.Key!;
  assert.match(key,/^app\/v1\/(?:users|teams)\/[0-9a-f-]{36}\/builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/);
  if(command instanceof PutObjectCommand)objects.set(key,Buffer.from(command.input.Body as Buffer));
  if(command instanceof GetObjectCommand)return {Body:{transformToByteArray:()=>objects.get(key)}};
  if(command instanceof DeleteObjectCommand)objects.delete(key);
  return {};
 });
 try{
  const bytes=Buffer.from('name,amount\n공유 자료,120\n');
  const {asset:source}=await withDataDirectory(teamDir,()=>persistLibraryUpload({fileName:'팀 목록.csv',data:bytes.toString('base64')}));
  let checks=0;
  const allow=async()=>{checks++;return {id:teamId,name:'정산팀'};};
  const {asset:copy}=await importTeamLibraryAsset(teamId,userId,source.id,allow);
  assert.ok(checks>=4,'membership is checked again after parsing and before registration');
  assert.notEqual(copy.id,source.id);assert.notEqual(copy.original_path,source.original_path);
  assert.deepEqual(copy.copied_from_team,{team_id:teamId,team_name:'정산팀',asset_id:source.id});
  assert.equal(objects.size,2,'team and personal copies have separate object keys');
  const page=await withDataDirectory(userDir,()=>builderWorker({action:'library'}));
  assert.deepEqual(page.assets.map((asset:any)=>asset.id),[copy.id]);
  await assert.rejects(importTeamLibraryAsset(teamId,userId,source.id,async()=>{throw new Error('팀 접근 권한이 없습니다.');}),/접근 권한/);
  checks=0;
  await assert.rejects(importTeamLibraryAsset(teamId,userId,source.id,async()=>{
   if(++checks===6)throw new Error('팀에서 제외되었습니다.');
   return {id:teamId,name:'정산팀'};
  }),/제외/);
  assert.equal(objects.size,2,'a revoked copy cleans its staged original');
  const after=await withDataDirectory(userDir,()=>builderWorker({action:'library'}));
  assert.deepEqual(after.assets.map((asset:any)=>asset.id),[copy.id]);
  await assert.rejects(importTeamLibraryAsset('../outside',userId,source.id,allow),/확인/);
  await assert.rejects(importTeamLibraryAsset(teamId,userId,randomUUID(),allow),/자료/);
 }finally{
  t.mock.restoreAll();assert.ok(resolve(root).startsWith(resolve(runtime)+sep));await rm(root,{recursive:true,force:true});
 }
});
test.after(()=>{for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);});
