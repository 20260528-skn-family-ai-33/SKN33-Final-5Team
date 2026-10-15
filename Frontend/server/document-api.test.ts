import {examplePath} from './paths';
import {SERVER_STDIO,requestShutdown,removeDirectory,scaled} from './test-process';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn,type ChildProcess} from 'node:child_process';
import {createServer as createSocket} from 'node:net';
import {createServer as createHttpServer,type Server as HttpServer} from 'node:http';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import ExcelJS from 'exceljs';
import type {Execution,Flow,FlowNode,Json} from '../lib/workflow';

const project=resolve(fileURLToPath(new URL('..',import.meta.url)));
const wait=(milliseconds:number)=>new Promise<void>(resolve=>setTimeout(resolve,milliseconds));
async function freePort():Promise<number> {
 const socket=createSocket();await new Promise<void>((resolve,reject)=>{socket.once('error',reject);socket.listen(0,'127.0.0.1',resolve);});const address=socket.address();assert.ok(address&&typeof address!=='string');const port=address.port;await new Promise<void>((resolve,reject)=>socket.close(error=>error?reject(error):resolve()));return port;
}
class ProductionServer {
 child?:ChildProcess;output='';
 constructor(readonly port:number,readonly directory:string){}
 get base(){return `http://127.0.0.1:${this.port}`;}
 async start(){
  const environment={...process.env,PORT:String(this.port),HOST:'127.0.0.1',EASYFLOW_DATA_DIR:this.directory};for(const key of Object.keys(environment))if(/^N8N_/i.test(key))delete environment[key as keyof typeof environment];
  // Exercise the production bundle; TypeScript source is never launched here.
  const child=this.child=spawn(process.execPath,['build-server/index.mjs'],{cwd:project,env:environment,stdio:SERVER_STDIO});let failure:Error|undefined;
  child.stdout?.on('data',chunk=>{this.output=(this.output+String(chunk)).slice(-16000);});child.stderr?.on('data',chunk=>{this.output=(this.output+String(chunk)).slice(-16000);});child.once('error',error=>{failure=error;});
  const until=Date.now()+10000;while(Date.now()<until){if(failure)throw failure;if(child.exitCode!==null||child.signalCode)throw new Error('production test server exited: '+this.output);try{const response=await fetch(this.base+'/api/health',{signal:AbortSignal.timeout(400)});if(response.ok){const health=await response.json() as Json;assert.equal(health.standalone,true);return;}}catch{}await wait(30);}throw new Error('production server did not start: '+this.output);
 }
 async stop(){
  const child=this.child;if(!child)return;this.child=undefined;if(child.exitCode!==null||child.signalCode)return;
  const exited=new Promise<{code:number|null;signal:NodeJS.Signals|null}>(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));requestShutdown(child);let timer:ReturnType<typeof setTimeout>|undefined;
  try{const result=await Promise.race([exited,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{child.kill('SIGKILL');reject(new Error('production server did not close gracefully: '+this.output));},scaled(4000));})]);assert.equal(result.code,0,this.output);assert.equal(result.signal,null);}finally{if(timer)clearTimeout(timer);}
 }
 async json(path:string,method='GET',data?:unknown){const response=await fetch(this.base+path,{method,headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)}),signal:AbortSignal.timeout(8000)});const value=await response.json() as Json;return {status:response.status,data:value};}
 async execute(flow:Flow,input:Json[]=[{}]):Promise<Execution>{
  const saved=await this.json('/api/workflows','POST',flow);assert.equal(saved.status,200,JSON.stringify(saved.data));const start=await this.json('/api/runs','POST',{workflowId:flow.id,mode:'learning',input});assert.equal(start.status,202,JSON.stringify(start.data));assert.equal(typeof start.data.executionId,'string');
  const until=Date.now()+15000;while(Date.now()<until){const response=await this.json('/api/executions?id='+encodeURIComponent(start.data.executionId));assert.equal(response.status,200);const execution=response.data.execution as Execution;if(['success','error','canceled'].includes(execution.status)){assert.equal(execution.status,'success',execution.error);return execution;}await wait(30);}throw new Error('production execution did not finish');
 }
 async download(execution:Execution,runIndex:number,property='data',side='output'){
  const parameters=new URLSearchParams({executionId:execution.id,runIndex:String(runIndex),output:'0',item:'0',property,side});const response=await fetch(this.base+'/api/files/download?'+parameters,{signal:AbortSignal.timeout(5000)});assert.equal(response.status,200);assert.equal(response.headers.get('x-content-type-options'),'nosniff');assert.match(response.headers.get('content-disposition')||'',/attachment.*filename\*=UTF-8''/);const bytes=Buffer.from(await response.arrayBuffer());assert.equal(Number(response.headers.get('content-length')),bytes.length);return {response,bytes};
 }
 async upload(bytes:Buffer,fileName:string,mimeType:string){const response=await this.json('/api/files','POST',{data:bytes.toString('base64'),fileName,mimeType,property:'data'});assert.equal(response.status,200,JSON.stringify(response.data));assert.equal(response.data.input[0].__easyflowBinary.data.fileName,fileName);assert.equal(response.data.input[0].__easyflowBinary.data.fileSize,bytes.length);return response.data.input as Json[];}
}
const node=(type:string,name:string,parameters:Json={}):FlowNode=>({id:randomUUID(),type:'n8n-nodes-base.'+type,name,typeVersion:type==='httpRequest'?4.2:type==='convertToFile'?1.1:1,parameters,position:[0,0]});
function workflow(nodes:FlowNode[]):Flow{return {id:randomUUID(),name:'production 실제 페이지 순회',nodes,connections:Object.fromEntries(nodes.slice(0,-1).map((item,index)=>[item.name,{main:[[{node:nodes[index+1].name,type:'main',index:0}]]}])),settings:{timezone:'Asia/Seoul',executionOrder:'v1'},active:false};}
async function example(name:string):Promise<Flow>{const value=JSON.parse(await readFile(examplePath(name),'utf8')) as Flow;return {...value,id:randomUUID(),active:false};}
function assertNotesExcluded(flow:Flow,execution:Execution){const notes=flow.nodes.filter(node=>node.type==='n8n-nodes-base.stickyNote');assert.ok(notes.length>0);assert.equal(execution.runs.length,flow.nodes.length-notes.length);for(const note of notes){assert.ok(execution.runs.every(run=>run.nodeId!==note.id&&run.name!==note.name));assert.ok(!JSON.stringify(execution.runs).includes(note.parameters.content));}}

test('production bundle executes actual uploaded XLSX/PDF and HTTP pagination with downloadable results', {timeout:scaled(45000)},async t=>{
 const port=await freePort(),directory=await mkdtemp(join(tmpdir(),'easyflow-production-documents-')),server=new ProductionServer(port,directory);let fixture:HttpServer|undefined;const requests:number[]=[];
 try {
  await server.start();
  await t.test('Excel example upload, filter, production conversion and download preserve real cells',async()=>{
   const bytes=await readFile(examplePath('초보자_Excel_예제.xlsx')),input=await server.upload(bytes,'초보자_Excel_예제.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),flow=await example('xlsx-filter_워크플로.json'),execution=await server.execute(flow,input);assertNotesExcluded(flow,execution);
   const extract=execution.runs.find(run=>run.name==='Excel 행 읽기');assert.deepEqual(extract?.output[0],[{name:'예시 고객 A',score:92},{name:'예시 고객 B',score:68},{name:'예시 고객 C',score:84}]);assert.ok(execution.runs.every(run=>run.status==='success'));
   const original=await server.download(execution,0,'data','input');assert.deepEqual(original.bytes,bytes);
   const result=await server.download(execution,execution.runs.findIndex(run=>run.name==='Excel 파일로 저장'));assert.match(result.response.headers.get('content-type')||'',/spreadsheetml\.sheet/);assert.equal(result.bytes.readUInt32LE(0),0x04034b50);const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(result.bytes as any);assert.equal(workbook.worksheets[0].name,'결과');assert.deepEqual(workbook.worksheets[0].getSheetValues().slice(1).map(row=>(row as unknown[]).slice(1)),[['name','score'],['예시 고객 A',92],['예시 고객 C',84]]);
  });
  await t.test('PDF example upload and production extraction download actual text and omit sticky-note runs',async()=>{
   const bytes=await readFile(examplePath('초보자_PDF_예제.pdf')),input=await server.upload(bytes,'초보자_PDF_예제.pdf','application/pdf'),flow=await example('pdf-text_워크플로.json'),execution=await server.execute(flow,input);assertNotesExcluded(flow,execution);
   const expected='Easyflow report page one\n\nSecond page contains actual text',extract=execution.runs.find(run=>run.name==='PDF 텍스트 읽기');assert.deepEqual(extract?.output,[[{numpages:2,numrender:2,text:expected}]]);assert.deepEqual((await server.download(execution,0)).bytes,bytes);
   const result=await server.download(execution,execution.runs.findIndex(run=>run.name==='텍스트 파일로 저장'));assert.match(result.response.headers.get('content-type')||'',/text\/plain/);assert.deepEqual(result.bytes,Buffer.from(expected,'utf8'));assert.ok(!result.bytes.toString('utf8').includes('OCR'));
  });
  fixture=createHttpServer((request,response)=>{const page=Number(new URL(request.url||'/', 'http://local.invalid').searchParams.get('page'));requests.push(page);response.writeHead(page>=1&&page<=3?200:404,{'Content-Type':'application/json'});response.end(JSON.stringify(page>=1&&page<=3?[{page,name:'페이지 '+page}]:{error:'unexpected page'}));});
  await new Promise<void>((resolve,reject)=>{fixture!.once('error',reject);fixture!.listen(0,'127.0.0.1',resolve);});const address=fixture.address();assert.ok(address&&typeof address!=='string');const fixtureUrl=`http://127.0.0.1:${address.port}/pages`;
  const pagedFlow=(maxRequests:number,completeAt:number)=>workflow([node('manualTrigger','시작'),node('httpRequest','실제 페이지 요청',{url:fixtureUrl,method:'GET',responseFormat:'json',options:{pagination:{pagination:{paginationMode:'updateAParameterInEachRequest',parameters:{parameters:[{type:'qs',name:'page',value:'={{ $pageCount + 1 }}'}]},paginationCompleteWhen:'other',completeExpression:`={{ $pageCount >= ${completeAt} }}`,limitPagesFetched:true,maxRequests,requestInterval:0}}}}),node('convertToFile','페이지 데이터를 파일로',{operation:'toJson',mode:'once',binaryPropertyName:'data',fileName:'페이지결과.json'})]);
  await t.test('three real local HTTP requests complete and persist pagination metadata and a real JSON file',async()=>{
   requests.length=0;const execution=await server.execute(pagedFlow(5,2));assert.deepEqual(requests,[1,2,3]);const http=execution.runs.find(run=>run.name==='실제 페이지 요청');assert.deepEqual(http?.pagination,{pagesFetched:3,itemsFetched:3,complete:true,limited:false,maxRequests:5});assert.equal(http?.warning,undefined);const rows=[{page:1,name:'페이지 1'},{page:2,name:'페이지 2'},{page:3,name:'페이지 3'}];assert.deepEqual(http?.output,[rows]);assert.deepEqual(JSON.parse((await server.download(execution,2)).bytes.toString('utf8')),rows);
  });
  await t.test('an explicit page cap returns its warning and incomplete metadata without requesting more pages',async()=>{
   requests.length=0;const execution=await server.execute(pagedFlow(2,9));assert.deepEqual(requests,[1,2]);const http=execution.runs.find(run=>run.name==='실제 페이지 요청');assert.deepEqual(http?.pagination,{pagesFetched:2,itemsFetched:2,complete:false,limited:true,maxRequests:2});assert.match(http?.warning||'',/설정한 2페이지.*더 있을/);assert.deepEqual(JSON.parse((await server.download(execution,2)).bytes.toString('utf8')),[{page:1,name:'페이지 1'},{page:2,name:'페이지 2'}]);
  });
 } finally {try{await server.stop();}finally{try{if(fixture?.listening){fixture.closeAllConnections();await new Promise<void>((resolve,reject)=>fixture!.close(error=>error?reject(error):resolve()));}}finally{await removeDirectory(directory);}}}
});
