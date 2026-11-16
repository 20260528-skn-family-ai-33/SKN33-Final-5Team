import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {EventEmitter} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';
import {PassThrough,Writable} from 'node:stream';
import {builderWorker,resetWorkerSlots} from '../src/agent-builder';

test('worker JSON preserves Korean and emoji at every UTF-8 chunk boundary',async t=>{
 const previous=process.env.A4B_AGENT_PYTHON;
 process.env.A4B_AGENT_PYTHON=process.execPath;resetWorkerSlots();
 let chunks:Buffer[]=[];
 let killed=0;
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;
  child.stdout=new PassThrough();child.stderr=new PassThrough();
  child.exitCode=null;child.signalCode=null;child.kill=()=>{killed++;return true;};
  child.stdin=new Writable({write(_chunk,_encoding,done){done();},final(done){
   queueMicrotask(()=>{for(const chunk of chunks)child.stdout.write(chunk);child.stdout.end();child.exitCode=0;child.emit('close',0);});done();
  }});
  return child;
 });syncBuiltinESMExports();
 try{
  const result={text:'환불 신청 후 3일 이내에 안내합니다. 😀',reviews:[{evidence:'검수 근거 한글'}]};
  const bytes=Buffer.from(JSON.stringify({ok:true,result}));
  for(let split=1;split<bytes.length;split++){
   chunks=[bytes.subarray(0,split),bytes.subarray(split)];
   assert.deepEqual(await builderWorker({action:'run',id:'utf8-test'}),result,`split byte ${split}`);
  }
  chunks=Array.from(bytes,byte=>Buffer.from([byte]));
  assert.deepEqual(await builderWorker({action:'run',id:'utf8-test'}),result);
  const limit=12*1024*1024;
  const prefix=Buffer.from('{"ok":true,"result":"'),suffix=Buffer.from('"}');
  chunks=[prefix,Buffer.alloc(limit-prefix.length-suffix.length,97),suffix];
  assert.equal((await builderWorker({action:'run'})).length,limit-prefix.length-suffix.length);
  chunks=[Buffer.alloc(limit,97),Buffer.from('a')];
  await assert.rejects(builderWorker({action:'run'}),/결과 크기 제한/);
  assert.equal(killed,1);
  chunks=[Buffer.from('{broken')];
  await assert.rejects(builderWorker({action:'run'}),/응답을 읽지 못했습니다/);
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previous===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previous;
 }
});
