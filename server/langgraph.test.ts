import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {SqliteCheckpointSaver} from './graph-checkpointer';
import {orchestrateWorkflow,createAgentGraph} from './agents';
import {makeFlow} from '../lib/workflow';
const credential:any={id:'local',provider:'openai',data:{apiKey:'sk-test-protected-123456'}};
const prompt='고객 점수를 분류하고 화면 결과를 보고 싶어요';
const requirement={id:'r1',description:'점수 분류',evidence:'고객 점수를 분류',state:'ready'};
const analysis={summary:'점수를 분류해요.',requirements:[requirement],questions:[]};
const waiting={...analysis,requirements:[{...requirement,state:'needs_input'}],questions:[{question:'기준 점수를 알려주세요.',choices:['80 이상']} ]};
function draft(){const workflow=makeFlow();return {workflow,summary:'초안을 만들었어요.',checklist:[],requirements:[{id:'r1',description:'점수 분류',evidence:'고객 점수를 분류',status:'mapped',nodeIds:[workflow.nodes[2].id]}]};}
function setup(values:any[]){const calls:any[]=[];return {calls,options:{credential,credentials:[],fetch:async(_u:any,init:any)=>{const request=JSON.parse(init.body);calls.push(request);const value=values.shift();assert.ok(value,'unexpected model call');return new Response(JSON.stringify({status:'completed',output:[{type:'message',content:[{type:'output_text',text:JSON.stringify(value)}]}]}));}}};}
test('LangGraph contains actual analysis, interrupt, design and review nodes',()=>{const g=createAgentGraph(setup([]).options).getGraph();for(const name of ['analyze','await_answer','design','review','finish'])assert.ok(g.nodes[name]);});
test('durable clarification resumes after saver reload without replaying the earlier model call',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-graph-'));const path=join(dir,'checkpoints.sqlite');let saver=new SqliteCheckpointSaver(path);
 try{const first=setup([waiting]);const r=await orchestrateWorkflow(prompt,{...first.options,checkpointer:saver,threadId:'conversation-a'});assert.equal(r.orchestration?.status,'waiting_for_answer');assert.equal(first.calls.length,1);saver.close();saver=new SqliteCheckpointSaver(path);
 const next=setup([analysis,draft(),{approved:true,issues:[]}]);const result=await orchestrateWorkflow('기준은 80점 이상입니다',{...next.options,checkpointer:saver,threadId:'conversation-a'});assert.ok(result.workflow);assert.equal(result.orchestration?.status,'completed');assert.equal(next.calls.length,3);const input=JSON.parse(next.calls[0].input);assert.equal(input.conversation[0].content,prompt);assert.equal(input.previousRequirements[0].id,'r1');
 const snapshot=await createAgentGraph(next.options,saver).getState({configurable:{thread_id:'conversation-a'}});assert.equal(snapshot.values.conversation.length,4);assert.equal(snapshot.values.currentWorkflow.id,result.workflow.id);
 }finally{saver.close();rmSync(dir,{recursive:true,force:true});}
});
test('completed conversations remember the accepted draft and requirements on the next turn',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-graph-'));const saver=new SqliteCheckpointSaver(join(dir,'checkpoints.sqlite'));
 try{const a=setup([analysis,draft(),{approved:true,issues:[]}]);const original=await orchestrateWorkflow(prompt,{...a.options,threadId:'continuous',checkpointer:saver});const b=setup([analysis,{...draft(),workflow:original.workflow},{approved:true,issues:[]}]);await orchestrateWorkflow('기존 단계는 그대로 유지해주세요',{...b.options,threadId:'continuous',checkpointer:saver});const input=JSON.parse(b.calls[0].input);assert.equal(input.currentWorkflow.id,original.workflow?.id);assert.equal(input.previousRequirements[0].id,'r1');assert.equal(input.conversation[0].content,prompt);
 }finally{saver.close();rmSync(dir,{recursive:true,force:true});}
});
test('separate graph threads do not share conversation state or store API secrets',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-graph-'));const saver=new SqliteCheckpointSaver(join(dir,'checkpoints.sqlite'));
 try{const a=setup([waiting]);await orchestrateWorkflow(prompt,{...a.options,threadId:'one',checkpointer:saver});const b=setup([analysis,draft(),{approved:true,issues:[]}]);await orchestrateWorkflow(prompt,{...b.options,threadId:'two',checkpointer:saver});assert.equal(JSON.parse(b.calls[0].input).conversation.length,0);const one=await createAgentGraph(a.options,saver).getState({configurable:{thread_id:'one'}});assert.ok(one.tasks.some(t=>t.interrupts.length));const bytes=Buffer.concat([readFileSync(join(dir,'checkpoints.sqlite')),readFileSync(join(dir,'checkpoints.sqlite-wal'))]);assert.equal(bytes.includes(Buffer.from(credential.data.apiKey)),false);
 }finally{saver.close();rmSync(dir,{recursive:true,force:true});}
});
test('a fresh Node process loads an interrupted graph checkpoint',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-graph-process-'));const path=join(dir,'checkpoints.sqlite');const saver=new SqliteCheckpointSaver(path);
 try{const a=setup([waiting]);await orchestrateWorkflow(prompt,{...a.options,threadId:'restart',checkpointer:saver});saver.close();const script=`import {SqliteCheckpointSaver} from './server/graph-checkpointer.ts';import {createAgentGraph} from './server/agents.ts';const saver=new SqliteCheckpointSaver(${JSON.stringify(path)});const graph=createAgentGraph({credential:{provider:'openai',data:{apiKey:'test'}},credentials:[]},saver);const s=await graph.getState({configurable:{thread_id:'restart'}});console.log(JSON.stringify({prompt:s.values.prompt,waiting:s.tasks.some(t=>t.interrupts.length)}));saver.close();`;const child=spawnSync(process.execPath,['--import','tsx','--input-type=module','-e',script],{cwd:process.cwd(),encoding:'utf8'});assert.equal(child.status,0,child.stderr);assert.deepEqual(JSON.parse(child.stdout.trim()),{prompt,waiting:true});
 }finally{rmSync(dir,{recursive:true,force:true});}
});
