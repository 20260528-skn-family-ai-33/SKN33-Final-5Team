import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import type {IncomingMessage,ServerResponse} from 'node:http';
import type {Store} from '../src/store';
import {handleAgentBuilder} from '../src/agent-builder';
import {withDataDirectory} from '../src/paths';

test('history uses one scoped worker, validates filters and prevents cached run lists',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-history-api-'));
 const previous=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;
 const calls:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  let input='';child.kill=()=>true;
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   calls.push(JSON.parse(input));child.stdout.end(JSON.stringify({ok:true,result:{agents:[],rows:[]}}));child.exitCode=0;queueMicrotask(()=>child.emit('close'));done();
  }});return child;
 });syncBuiltinESMExports();
 async function call(query='',method='GET'){
  const headers:Record<string,string>={};let status=0,result:any;
  await withDataDirectory(directory,()=>handleAgentBuilder({method} as IncomingMessage,{setHeader(key:string,value:string){headers[key]=value;}} as unknown as ServerResponse,new URL('http://localhost/api/agent-builder/history'+query),{} as Store,async()=>({}),(_res,code,body)=>{status=code;result=body;}));
  return {status,result,headers};
 }
 try{
  const all=await call();assert.equal(all.status,200);assert.deepEqual(all.result,{agents:[],rows:[]});assert.equal(all.headers['Cache-Control'],'no-store');assert.equal(calls.length,1);assert.equal(calls[0].action,'history');assert.equal(calls[0].directory,directory);
  const id=randomUUID();await call('?id='+id);assert.equal(calls.length,2);assert.equal(calls[1].id,id);
  await assert.rejects(()=>call('?id=invalid'),/ID/);assert.equal(calls.length,2);
  assert.equal((await call('','POST')).status,405);assert.equal(calls.length,2);
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();if(previous===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previous;
  await rm(directory,{recursive:true,force:true});
 }
});
