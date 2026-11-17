import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Readable} from 'node:stream';
import type {IncomingMessage,ServerResponse} from 'node:http';
import pg from 'pg';
import {AdminMonitoring} from '../src/admin-monitoring';
const url=process.env.A4B_MONITOR_CONTEXT_TEST_DATABASE_URL;
const admin='11111111-1111-4111-8111-111111111111',member='22222222-2222-4222-8222-222222222222',other='33333333-3333-4333-8333-333333333333';
const workflow='44444444-4444-4444-8444-444444444444',session='55555555-5555-4555-8555-555555555555',newSession='66666666-6666-4666-8666-666666666666',run='77777777-7777-4777-8777-777777777777';
// Both schemas are created inside this transaction. Failed CREATE never deletes existing data.
test('012: admin-only identity/context, exact owner/version linkage and safe API projection',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_context_review_test$/);
 const c=new pg.Client({connectionString:url});await c.connect();await c.query('BEGIN');
 try{
  await c.query(`CREATE SCHEMA a4b; CREATE SCHEMA a4b_builder;
   GRANT USAGE ON SCHEMA a4b,a4b_builder TO a4b_app;
   CREATE TABLE a4b.users(id uuid PRIMARY KEY,email text,role text,status text);
   CREATE TABLE a4b.workflow_generation_requests(workspace_id text,id text,session_id text,json json);
   ALTER TABLE a4b.workflow_generation_requests ENABLE ROW LEVEL SECURITY;
   ALTER TABLE a4b.workflow_generation_requests FORCE ROW LEVEL SECURITY;
   GRANT SELECT ON a4b.workflow_generation_requests TO a4b_app;
   CREATE POLICY workspace_read ON a4b.workflow_generation_requests TO a4b_app USING(workspace_id=current_setting('a4b.workspace_id',true));
   CREATE TABLE a4b.workflow_versions(id uuid,workflow_id uuid,version_no integer);
   CREATE TABLE a4b.workflow_runs(id uuid,requested_by uuid,status text,state_json jsonb,input_json jsonb,created_at timestamptz,finished_at timestamptz,workflow_version_id uuid);
   CREATE TABLE a4b.node_runs(run_id uuid,node_key text,status text,started_at timestamptz,finished_at timestamptz,attempt_no integer);
   CREATE TABLE a4b.design_sessions(id uuid PRIMARY KEY,created_by uuid,workflow_id uuid,messages_json jsonb);
   CREATE TABLE a4b_builder.agent_versions(workspace_id text,agent_id text,version integer,json json);
   ALTER TABLE a4b_builder.agent_versions ENABLE ROW LEVEL SECURITY;
   ALTER TABLE a4b_builder.agent_versions FORCE ROW LEVEL SECURITY;
   GRANT SELECT ON a4b_builder.agent_versions TO a4b_app;
   CREATE POLICY workspace_read ON a4b_builder.agent_versions TO a4b_app USING(workspace_id=current_setting('a4b.workspace_id',true));`);
  await c.query("INSERT INTO a4b.users VALUES($1,'admin@example.test','admin','active'),($2,'member@example.test','member','active'),($3,'other@example.test','member','active')",[admin,member,other]);
  for(const name of ['009_admin_monitoring.sql','010_admin_monitoring_fixes.sql','011_monitoring_document_failures.sql','012_monitoring_request_context.sql'])await c.query(await readFile('infrastructure/db/migrations/'+name,'utf8'));
  await c.query('INSERT INTO a4b.design_sessions VALUES($1,$2,$3,$4),($5,$2,$3,$6)',[session,member,workflow,JSON.stringify([{role:'system',content:'HIDDEN SYSTEM'},{role:'user',content:'기존 요청 password="secret value"'},{role:'assistant',content:'추가 질문입니다.'},{role:'tool',content:'HIDDEN TOOL'},{role:'assistant',content:{secret:'HIDDEN OBJECT'}}]),newSession,JSON.stringify([{role:'user',content:'다른 버전의 대화'}])]);
  for(const owner of [member,other])await c.query('INSERT INTO a4b.workflow_generation_requests(workspace_id,id,session_id,json) VALUES($1,$2,$3,$4)',[owner,'same-request',session,JSON.stringify({request_text:owner===member?'요약해줘 token=prompt-secret':'다른 회원 요청',started_at:new Date(Date.now()-1000).toISOString(),status:'failed',node:'requirements'})]);
  await c.query('INSERT INTO a4b.workflow_versions VALUES($1,$3,1),($2,$3,2)',[session,newSession,workflow]);
  await c.query('INSERT INTO a4b_builder.agent_versions VALUES($1,$2,1,$3),($1,$2,2,$4)',[member,workflow,JSON.stringify({session_id:session,credential_id:'HIDDEN CREDENTIAL'}),JSON.stringify({session_id:newSession})]);
  await c.query("INSERT INTO a4b.workflow_runs VALUES($1,$2,'failed','{}',$3,now()-interval '1 hour',now()-interval '1 minute',$4)",[run,member,JSON.stringify({request:'실행 요청 api_key=execution-secret',other:'HIDDEN INPUT'}),session]);
  await c.query('SET LOCAL ROLE a4b_app');
  const ctx=async(kind:string,id:string)=>(await c.query('SELECT a4b.monitor_request_context($1,$2,$3) AS context',[admin,kind,id])).rows[0].context;
  assert.equal((await c.query('SELECT * FROM a4b_builder.agent_versions')).rowCount,0,'ordinary workspace RLS unchanged');
  const generation=await ctx('generation',member+':same-request');assert.equal(generation.conversation.length,2);assert.equal(generation.conversation_source,'session_latest');
  const foreign=await ctx('generation',other+':same-request');assert.equal(foreign.prompt,'다른 회원 요청');assert.deepEqual(foreign.conversation,[],'session from another owner must not be exposed');
  const execution=await ctx('execution',run);assert.equal(execution.prompt,'실행 요청 api_key=execution-secret');assert.equal(execution.conversation[0].content,generation.conversation[0].content,'executed version 1, not latest version 2');
  assert.ok(!JSON.stringify(execution).includes('HIDDEN'));assert.ok(!JSON.stringify(execution).includes('다른 버전'));
  const service=new AdminMonitoring(undefined,{query:(sql,v)=>c.query(sql,v),end:async()=>{}});
  async function get(path:string){let result:any;const req=Object.assign(Readable.from([]),{method:'GET'}) as IncomingMessage;const res={setHeader(){},writeHead(){},end(body:string){result=JSON.parse(body);}} as unknown as ServerResponse;
   await service.handle(req,res,new URL('http://localhost/api/admin/monitoring'+path),{id:admin,email:'admin@example.test',name:'관리자',role:'admin',status:'active'});return result;
  }
  const listing=await get('?days=7');assert.equal(listing.total,3);assert.equal(listing.items.find((i:any)=>i.member_id===member).member_email,'member@example.test');
  assert.ok(!JSON.stringify(listing).includes('prompt-secret'));assert.ok(!JSON.stringify(listing).includes('기존 요청'));assert.ok(listing.items.every((i:any)=>!('context' in i)));
  const detail=await get('/generation/'+member+':same-request?days=7');assert.equal(detail.item.member_email,'member@example.test');assert.match(detail.item.context.prompt,/요약해줘/);assert.ok(!JSON.stringify(detail).includes('prompt-secret'));assert.ok(!JSON.stringify(detail).includes('secret value'));
  const runDetail=await get('/execution/'+run+'?days=7');assert.ok(!JSON.stringify(runDetail).includes('execution-secret'));assert.equal(runDetail.item.context.conversation.length,2);
  for(const statement of ['SELECT * FROM a4b.monitor_member_emails($1,ARRAY[\''+member+'\'])',"SELECT a4b.monitor_request_context($1,'generation','"+member+":same-request')"]){
   await c.query('SAVEPOINT forbidden');await assert.rejects(c.query(statement,[member]),{code:'42501'});await c.query('ROLLBACK TO forbidden');
  }
  await c.query('RESET ROLE');
  await c.query('UPDATE a4b.design_sessions SET workflow_id=$1 WHERE id=$2',[other,session]);await c.query('SET LOCAL ROLE a4b_app');assert.deepEqual((await ctx('execution',run)).conversation,[],'wrong workflow session rejected');await c.query('RESET ROLE');
  await c.query('DELETE FROM a4b_builder.agent_versions WHERE version=1');await c.query('SET LOCAL ROLE a4b_app');assert.deepEqual((await ctx('execution',run)).conversation,[],'missing exact version never falls back to latest');await c.query('RESET ROLE');
  await c.query("UPDATE a4b.workflow_generation_requests SET json='{}',session_id='missing' WHERE workspace_id=$1",[member]);await c.query('SET LOCAL ROLE a4b_app');
  const missing=await ctx('generation',member+':same-request');assert.equal(missing.prompt,null);assert.deepEqual(missing.conversation,[]);assert.equal(missing.conversation_source,'unavailable');
  assert.equal((await c.query('SELECT * FROM a4b.monitor_member_emails($1,$2)',[admin,['deleted-user']])).rowCount,0);
  await c.query('RESET ROLE');await c.query("UPDATE a4b.users SET status='disabled' WHERE id=$1",[admin]);await c.query('SET LOCAL ROLE a4b_app');
  await c.query('SAVEPOINT revoked');await assert.rejects(ctx('generation',member+':same-request'),{code:'42501'});await c.query('ROLLBACK TO revoked');
 }finally{await c.query('ROLLBACK');await c.end();}
});
