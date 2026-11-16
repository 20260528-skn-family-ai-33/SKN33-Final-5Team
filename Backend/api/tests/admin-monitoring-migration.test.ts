import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import pg from 'pg';
const url=process.env.A4B_MONITOR_MIGRATION_TEST_DATABASE_URL;
const actor='11111111-1111-4111-8111-111111111111';
async function withOwnedSchema(c:pg.Client, body:()=>Promise<void>){
 let ownedOid:number|undefined;
 await c.query('BEGIN');
 try{
  await c.query('CREATE SCHEMA a4b');
  ownedOid=(await c.query("SELECT oid FROM pg_namespace WHERE nspname='a4b'")).rows[0].oid;
  await body();
 }finally{
  await c.query('ROLLBACK');
  if(ownedOid!==undefined){
   const current=(await c.query("SELECT oid FROM pg_namespace WHERE nspname='a4b'")).rows[0]?.oid;
   if(current===ownedOid)await c.query('DROP SCHEMA a4b CASCADE');
  }
 }
}
// Dedicated empty database: each case creates and removes its own schema.
for(const business of [false,true])test(`009 -> 011: backfill, indexed dates and triage (business tables=${business})`,{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_review_test$/);
 const c=new pg.Client({connectionString:url});await c.connect();
 try{await withOwnedSchema(c,async()=>{
  await c.query(`GRANT USAGE ON SCHEMA a4b TO a4b_app;
   CREATE TABLE a4b.users(id uuid PRIMARY KEY,role text,status text);
   CREATE TABLE a4b.workflow_generation_requests(workspace_id text,id text,session_id text,json json);
   ALTER TABLE a4b.workflow_generation_requests ENABLE ROW LEVEL SECURITY;
   ALTER TABLE a4b.workflow_generation_requests FORCE ROW LEVEL SECURITY;
   GRANT SELECT,INSERT,UPDATE ON a4b.workflow_generation_requests TO a4b_app;
   CREATE POLICY workspace_access ON a4b.workflow_generation_requests TO a4b_app USING(workspace_id=current_setting('app.workspace_id',true));`);
  await c.query("INSERT INTO a4b.users VALUES($1,'admin','active')",[actor]);
  if(business)await c.query(`CREATE TABLE a4b.workflow_versions(id uuid,workflow_id uuid);
   CREATE TABLE a4b.workflow_runs(id uuid,requested_by uuid,status text,state_json jsonb,created_at timestamptz,finished_at timestamptz,workflow_version_id uuid);
   CREATE TABLE a4b.documents(id uuid,created_by uuid);
   CREATE TABLE a4b.document_versions(id uuid,document_id uuid,status text,created_at timestamptz,error_json jsonb,parser_metadata_json jsonb);
   CREATE TABLE a4b.node_runs(run_id uuid,node_key text,status text,started_at timestamptz,finished_at timestamptz,attempt_no integer);`);
  await c.query(await readFile('infrastructure/db/migrations/009_admin_monitoring.sql','utf8'));
  await c.query(`INSERT INTO a4b.workflow_generation_requests(workspace_id,id,session_id,json) VALUES($1,'older','s',json_build_object('status','failed','started_at',now()-interval '10 days','finished_at',now()-interval '1 hour'))`,[actor]);
  await c.query(await readFile('infrastructure/db/migrations/010_admin_monitoring_fixes.sql','utf8'));
  await c.query(await readFile('infrastructure/db/migrations/011_monitoring_document_failures.sql','utf8'));
  // Commit DDL/backfill before checking planner visibility of the new index.
  await c.query('COMMIT');await c.query('BEGIN');
  assert.equal((await c.query("SELECT count(*) FROM a4b.workflow_generation_requests WHERE monitor_at>now()-interval '1 day'")).rows[0].count,'1','backfill under FORCE RLS');
  await c.query('SET LOCAL ROLE a4b_app');
  await c.query("SELECT set_config('app.workspace_id',$1,true)",[actor]);
  await c.query(`INSERT INTO a4b.workflow_generation_requests(workspace_id,id,session_id,json) VALUES($1,'offset','s','{"status":"failed","started_at":"2026-01-01T09:00:00+09:00"}')`,[actor]);
  assert.equal((await c.query("SELECT monitor_at='2026-01-01T00:00:00Z'::timestamptz ok FROM a4b.workflow_generation_requests WHERE id='offset'")).rows[0].ok,true);
  await c.query(`UPDATE a4b.workflow_generation_requests SET json=json_build_object('status','failed','started_at','2026-01-01T09:00:00+09:00','finished_at',now()) WHERE id='offset'`);
  const recent=await c.query("SELECT r FROM a4b.monitor_records($1,now()-interval '1 day',now()+interval '1 second') r",[actor]);
  assert.equal(recent.rowCount,2,'updated completion time included');
  const saved=await c.query("SELECT * FROM a4b.monitor_triage_save($1,'generation',$2,'resolved','fixed',0)",[actor,actor+':older']);
  assert.equal(saved.rows[0].revision,1,'generation triage works without optional tables');
  await c.query('SAVEPOINT absent');
  await assert.rejects(c.query("SELECT * FROM a4b.monitor_triage_save($1,'document','missing','resolved','',0)",[actor]),{code:'P0002'});
  await c.query('ROLLBACK TO absent');await c.query('RESET ROLE');
  await c.query('SET LOCAL ROLE a4b_monitor_owner');await c.query('SET LOCAL enable_seqscan=off');
  const plan=(await c.query("EXPLAIN SELECT * FROM a4b.workflow_generation_requests WHERE monitor_at>=now()-interval '1 day' AND monitor_at<now()" )).rows.map(r=>r['QUERY PLAN']).join('\n');
  assert.match(plan,/generation_monitor_time/);assert.match(plan,/Index Cond/);
  await c.query('RESET ROLE');
  if(business){
   await c.query('INSERT INTO a4b.documents VALUES($1,$1)',[actor]);
   await c.query("INSERT INTO a4b.document_versions VALUES($1,$1,'uploaded',now()-interval '10 days','{}','{}')",[actor]);
   await c.query("UPDATE a4b.document_versions SET status='processing'");
   await c.query("UPDATE a4b.document_versions SET status='failed'");
   await c.query('SET LOCAL ROLE a4b_app');
   const docs=(await c.query("SELECT r FROM a4b.monitor_records($1,now()-interval '1 day',clock_timestamp()+interval '1 second') r WHERE r->>'kind'='document'",[actor])).rows;
   assert.equal(docs.length,1);assert.ok(docs[0].r.finished_at>docs[0].r.started_at);
   await c.query('RESET ROLE');
   const stamp=(await c.query('SELECT failed_at FROM a4b.monitor_document_failures')).rows[0].failed_at;
   await c.query("UPDATE a4b.document_versions SET status='failed'");
   assert.deepEqual((await c.query('SELECT failed_at FROM a4b.monitor_document_failures')).rows[0].failed_at,stamp);
   await c.query("UPDATE a4b.monitor_document_failures SET failed_at=now()-interval '2 days'");
   assert.equal((await c.query("SELECT r FROM a4b.monitor_records($1,now()-interval '1 day',clock_timestamp()) r WHERE r->>'kind'='document'",[actor])).rowCount,0);
   await c.query('INSERT INTO a4b.workflow_versions VALUES($1,$1)',[actor]);
   await c.query(`INSERT INTO a4b.workflow_runs VALUES($1,$1,'failed','{}',now()-interval '10 days',now()-interval '1 hour',$1)`,[actor]);
   await c.query('SET LOCAL ROLE a4b_app');
   const rows=(await c.query("SELECT r FROM a4b.monitor_records($1,now()-interval '1 day',now()) r WHERE r->>'kind'='execution'",[actor])).rows;
   assert.equal(rows.length,1,'old run failing today included');assert.ok(rows[0].r.occurred_at>rows[0].r.started_at);
   await c.query('RESET ROLE');
   await c.query("UPDATE a4b.workflow_runs SET finished_at=now()-interval '2 days'");
   assert.equal((await c.query("SELECT r FROM a4b.monitor_records($1,now()-interval '1 day',now()) r WHERE r->>'kind'='execution'",[actor])).rowCount,0,'old failure excluded');
  }
  const rights=(await c.query("SELECT pg_get_userbyid(proowner) owner,prosecdef FROM pg_proc WHERE oid='a4b.monitor_records(uuid,timestamptz,timestamptz,text)'::regprocedure")).rows[0];
  assert.deepEqual(rights,{owner:'a4b_monitor_owner',prosecdef:true});
 });}finally{await c.end();}
});

test('existing schema and sentinel survive failed fixture creation',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_review_test$/);
 const c=new pg.Client({connectionString:url});await c.connect();
 try{await withOwnedSchema(c,async()=>{
  await c.query("CREATE TABLE a4b.sentinel(value text); INSERT INTO a4b.sentinel VALUES('preserve')");
  await c.query('COMMIT');
  await assert.rejects(withOwnedSchema(c,async()=>{assert.fail('must not enter');}),{code:'42P06'});
  assert.equal((await c.query('SELECT value FROM a4b.sentinel')).rows[0].value,'preserve');
 });}finally{await c.end();}
});
