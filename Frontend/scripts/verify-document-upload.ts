/** Opt-in real-engine HTTP upload check. Uses root .env and an isolated database. */
import assert from 'node:assert/strict';
import {spawn,type ChildProcess} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import {once} from 'node:events';
import {basename,join,resolve} from 'node:path';
import {parseArgs} from 'node:util';
import {PROJECT_ROOT} from '../server/paths';
import {validateDocumentAnalysis} from '../server/document-analysis-contract';

type Case={id:string;input:string;sha256:string;fields:string[];
 routes:{processor:string;phase:string}[];table?:{phase:string;selected:boolean;cells:{row:number;column:number;text:string;row_span:number;col_span:number}[]}};
const hash=(bytes:Uint8Array|string)=>createHash('sha256').update(bytes).digest('hex');
const delay=(ms:number)=>new Promise<void>(done=>setTimeout(done,ms));
const {values}=parseArgs({options:{cases:{type:'string'},output:{type:'string'}}});
if(!values.cases||!values.output)throw new Error('--cases와 --output을 지정하세요.');
const casesPath=resolve(values.cases),cases=JSON.parse(await readFile(casesPath,'utf8')) as Case[];
assert.ok(cases.length>0&&cases.length<=10,'시험 입력은 1~10개여야 합니다.');
assert.equal(new Set(cases.map(c=>c.id)).size,cases.length,'시험 ID가 중복됐습니다.');
for(const item of cases)assert.ok(/^[A-Za-z0-9_-]+$/.test(item.id),'시험 ID는 영문·숫자·밑줄·하이픈을 사용하세요.');
const output=resolve(values.output,new Date().toISOString().replace(/[:.]/g,'-')+'-'+randomUUID().slice(0,8));
const directory=join(output,'service-data');
await mkdir(directory,{recursive:true});
const socket=createServer();
await new Promise<void>((done,reject)=>{socket.once('error',reject);socket.listen(0,'127.0.0.1',done);});
const address=socket.address();assert.ok(address&&typeof address!=='string');
const port=address.port;
await new Promise<void>((done,reject)=>socket.close(error=>error?reject(error):done()));
const base=`http://127.0.0.1:${port}`;
let child:ChildProcess|undefined;
let launch=0;
async function start(){
 const log=createWriteStream(join(output,`server-${++launch}.log`));
 child=spawn(process.execPath,[join(PROJECT_ROOT,'scripts/run-configured.mjs')],{
  cwd:PROJECT_ROOT,env:{...process.env,HOST:'127.0.0.1',PORT:String(port),EASYFLOW_DATA_DIR:directory,A4B_FOCUSED_DEMO:'1'},stdio:['ignore','pipe','pipe','ipc']});
 child.stdout!.pipe(log,{end:false});child.stderr!.pipe(log,{end:false});
 child.once('exit',()=>log.end());
 let launchError:Error|undefined;child.once('error',error=>{launchError=error;});
 for(let attempt=0;attempt<120;attempt++){
  if(launchError)throw launchError;
  if(child.exitCode!==null)throw new Error('서비스가 시작되지 않았습니다. server 로그를 확인하세요.');
  try{const response=await fetch(base+'/api/health',{signal:AbortSignal.timeout(1000)});if(response.ok)return;}catch{}
  await delay(250);
 }
 throw new Error('서비스 시작 대기 시간이 초과됐습니다.');
}
async function stop(){
 const running=child;child=undefined;
 if(!running||running.exitCode!==null)return;
 const ended=once(running,'exit');
 if(process.platform==='win32'&&running.connected)running.send('shutdown');else running.kill('SIGTERM');
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{await Promise.race([ended,new Promise((_,reject)=>{timer=setTimeout(()=>{running.kill('SIGKILL');reject(new Error('시험 서버 종료 시간이 초과됐습니다.'));},15000);})]);}
 finally{if(timer)clearTimeout(timer);}
}
async function call(path:string,data?:unknown){
 const response=await fetch(base+path,{method:data===undefined?'GET':'POST',
  headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(600000)});
 const body=await response.json() as any;
 if(!response.ok)throw new Error(`HTTP ${response.status}: ${body.error||path}`);
 return body;
}
const results:any[]=[],uploaded=new Map<string,any>();
let persistence=false,failure:string|undefined;
try{
 await start();
 const connection=await call('/api/agent-builder/connection');
 assert.ok(connection.connection?.credential?.id,'기본 실행 설정에 모델 연결 정보가 없습니다.');
 let session=await call('/api/agent-builder/sessions',{credentialId:connection.connection.credential.id,model:connection.connection.model});
 for(const item of cases){
  const started=performance.now(),row:any={id:item.id,status:'failed'};
  try{
   const bytes=await readFile(resolve(item.input));
   assert.equal(hash(bytes),item.sha256,'입력 사본 해시가 달라졌습니다.');
   session=await call('/api/agent-builder/upload',{id:session.id,revision:session.revision,fileName:basename(item.input),data:bytes.toString('base64')});
   const asset=session.resources.find((a:any)=>a.hash===item.sha256&&a.name===basename(item.input));
   assert.ok(asset,'HTTP 업로드 응답에 자산이 없습니다.');
   uploaded.set(asset.id,asset);
   await writeFile(join(output,item.id+'-asset.json'),JSON.stringify(asset,null,2));
   const analysis=asset.parser_metadata?.document_analysis;
   assert.ok(validateDocumentAnalysis(analysis),'저장된 분석 계약이 올바르지 않습니다.');
   assert.equal(analysis.source_sha256,item.sha256);
   assert.equal(asset.kind,'document');
   const checks=item.routes.map(required=>({...required,succeeded:analysis.routes.some(route=>route.processor===required.processor&&route.phase===required.phase&&route.status==='succeeded')}));
   row.routes=analysis.routes;row.route_checks=checks;
   assert.ok(checks.every(check=>check.succeeded),'필수 엔진이 실제로 실행되지 않았습니다.');
   row.fields={total:item.fields.length,exact:item.fields.filter(value=>asset.text.includes(value)).length};
   assert.equal(row.fields.exact,row.fields.total,'지정 필드가 기본 본문에 보존되지 않았습니다.');
   if(item.table){
    const candidate=analysis.blocks.find(b=>b.engine==='docling'&&(b as any).phase===item.table!.phase&&b.content_types.includes('table'));
    assert.ok(candidate,'표 구조 후보가 없습니다.');
    assert.equal(candidate.selected,item.table.selected);
    const actual=candidate.data.cells as any[];
    const matched=item.table.cells.filter(gold=>actual.some(cell=>cell.start_row_offset_idx===gold.row&&cell.start_col_offset_idx===gold.column&&cell.text===gold.text&&cell.row_span===gold.row_span&&cell.col_span===gold.col_span)).length;
    row.table={phase:item.table.phase,selected:candidate.selected,total:item.table.cells.length,exact:matched};
    assert.equal(matched,item.table.cells.length,'표 셀 내용·위치·병합이 정답과 다릅니다.');
   }
   const original=await fetch(base+'/api/agent-builder/original?id='+asset.id,{signal:AbortSignal.timeout(30000)});
   assert.equal(original.status,200);
   assert.equal(hash(new Uint8Array(await original.arrayBuffer())),item.sha256,'서비스 원본 다운로드 해시가 다릅니다.');
   assert.equal(hash(await readFile(resolve(item.input))),item.sha256,'입력 파일이 변경됐습니다.');
   Object.assign(row,{status:'passed',asset_id:asset.id,parser_status:asset.parser_status,processing_status:asset.processing_status,
    primary_processor:analysis.primary_processor,review_reasons:analysis.review_reasons,
    selected_table_count:asset.tables.length,artifact_directory:asset.parser_metadata.analysis_artifact_directory,
    original_download_preserved:true});
  }catch(error){row.error=(error as Error).message;}
  row.elapsed_seconds=(performance.now()-started)/1000;results.push(row);
  await writeFile(join(output,'progress.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({id:row.id,status:row.status,parser_status:row.parser_status,fields:row.fields,error:row.error}));
 }
 await stop();await start();
 const restored=await call('/api/agent-builder/session?id='+session.id);
 for(const [id,expected] of uploaded){
  const actual=restored.resources.find((asset:any)=>asset.id===id);
  assert.ok(actual,'서비스 재시작 후 자산을 찾을 수 없습니다.');
  assert.deepEqual(actual.parser_metadata.document_analysis,expected.parser_metadata.document_analysis,'재시작 후 분석 메타데이터가 달라졌습니다.');
  assert.equal(actual.parser_status,expected.parser_status);
 }
 persistence=uploaded.size===cases.length;
}catch(error){failure=(error as Error).message;console.error(failure);}
finally{try{await stop();}catch(error){failure??=(error as Error).message;}}
const summary={created_at:new Date().toISOString(),scope:'real HTTP upload, original retrieval, persisted metadata and service restart',
 environment_file:join(PROJECT_ROOT,'.env'),environment_sha256:hash(await readFile(join(PROJECT_ROOT,'.env'))),
 case_manifest_sha256:hash(await readFile(casesPath)),verification_code_sha256:hash(await readFile(new URL(import.meta.url))),
 test_data_directory:directory,server_launcher:'scripts/run-configured.mjs',inference_mocked:false,
 node:process.version,platform:process.platform,server_bundle_sha256:hash(await readFile(join(PROJECT_ROOT,'Frontend/build-server/index.mjs'))),
 workflow_model_requests_exercised:false,
 cases:results,counts:{total:cases.length,passed:results.filter(row=>row.status==='passed').length},
 service_restart_preserved_metadata:persistence,error:failure};
await writeFile(join(output,'summary.json'),JSON.stringify(summary,null,2));
console.log('REPORT',join(output,'summary.json'));
if(failure||!persistence||summary.counts.passed!==cases.length)process.exitCode=1;
