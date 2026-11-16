import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Store} from '../src/store';
import type {Flow,Execution} from '../../../shared/workflow';
test('workflow execution filtering happens before the history limit and preserves detail snapshots',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-filtered-executions-')),store=new Store(directory);
 try{
  const flow=(id:string):Flow=>({id,name:id,nodes:[],connections:{},settings:{}});
  const record=(id:string,workflowId:string,startedAt:string):Execution=>({id,workflowId,workflowName:workflowId,startedAt,status:'success',duration:1,mode:'learning',runs:[]});
  const original=record('old-a','A','2026-01-01T00:00:00Z');store.reserveExecution(original,flow('A'),[{original:true}]);
  for(let i=0;i<501;i++)store.reserveExecution(record('b-'+i,'B','2026-11-16T00:00:00Z'),flow('B'),[]);
  assert.equal(store.listExecutions().length,500);
  assert.deepEqual(store.listExecutions('A'),[original]);
  assert.equal(store.listExecutions('B').length,500);
  assert.deepEqual(store.listExecutions("A' OR 1=1"),[]);
  assert.deepEqual(store.getExecutionSnapshot('old-a')?.input,[{original:true}]);
 }finally{store.close();await rm(directory,{recursive:true,force:true});}
});
