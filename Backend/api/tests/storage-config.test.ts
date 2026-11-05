import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import {storageConfig,workerLimits,checkBuilderDatabase,REQUIRED_SCHEMA_VERSION} from '../src/storage-config';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {API_ROOT} from '../src/paths';
import {WorkerSlots,WorkerBusyError,createWorkerLanes} from '../src/worker-slots';
import {PostgresCheckpointSaver} from '../src/graph-checkpointer';
import {createAgentGraph,orchestrateWorkflow} from '../src/agents';

test('business data defaults to SQLite and refuses it for a public server',()=>{
 assert.deepEqual(storageConfig({}),{kind:'sqlite'});
 assert.deepEqual(storageConfig({EASYFLOW_PUBLIC_URL:'http://localhost:3030'}),{kind:'sqlite'});
 assert.throws(()=>storageConfig({EASYFLOW_PUBLIC_URL:'https://service.example.com'}),/로컬 개발에서만/);
 assert.throws(()=>storageConfig({A4B_STORAGE:'mysql'}),/sqlite 또는 postgres/);
 assert.throws(()=>storageConfig({A4B_STORAGE:'postgres'}),/A4B_DATABASE_URL/);
});
test('PostgreSQL business data uses the member-store address and TLS rules',()=>{
 const env={A4B_STORAGE:'postgres',EASYFLOW_PUBLIC_URL:'https://service.example.com',A4B_DATABASE_URL:'postgresql://app@db.example.com/a4b',A4B_DATABASE_CA_FILE:'/app/data/global-bundle.pem'};
 assert.deepEqual(storageConfig(env),{kind:'postgres',databaseUrl:env.A4B_DATABASE_URL,databaseCaFile:env.A4B_DATABASE_CA_FILE,databaseServerName:undefined});
 assert.throws(()=>storageConfig({...env,A4B_DATABASE_URL:'postgresql://app@db.example.com/a4b?sslmode=disable'}),/SSL 옵션/);
 assert.throws(()=>storageConfig({...env,A4B_DATABASE_URL:'mysql://app@db.example.com/a4b'}),/PostgreSQL/);
 assert.throws(()=>storageConfig({...env,A4B_DATABASE_TLS_SERVER_NAME:'10.0.0.1'}),/TLS_SERVER_NAME/);
 assert.equal((storageConfig({...env,A4B_DATABASE_TLS_SERVER_NAME:'a4b.abc.rds.amazonaws.com'}) as any).databaseServerName,'a4b.abc.rds.amazonaws.com');
});
test('worker limits default to 12 general and 4 control processes and reject unbounded values',()=>{
 assert.deepEqual(workerLimits({}),{general:12,long:8,control:4,queueMs:60000});
 assert.deepEqual(workerLimits({A4B_WORKER_MAX_CONCURRENCY:'3',A4B_WORKER_CONTROL_CONCURRENCY:'2'}),{general:3,long:2,control:2,queueMs:60000});
 assert.equal(workerLimits({A4B_WORKER_MAX_CONCURRENCY:'1'}).long,1);
 for(const value of ['0','-1','1.5','abc','65'])assert.throws(()=>workerLimits({A4B_WORKER_MAX_CONCURRENCY:value}),/A4B_WORKER_MAX_CONCURRENCY/);
 assert.throws(()=>workerLimits({A4B_WORKER_CONTROL_CONCURRENCY:'33'}),/A4B_WORKER_CONTROL_CONCURRENCY/);
});
test('worker slots never exceed the limit, hand over in order and time out',async()=>{
 const slots=new WorkerSlots(2),order:string[]=[];
 const a=await slots.acquire(1000,'busy'),b=await slots.acquire(1000,'busy');
 const c=slots.acquire(1000,'busy').then(release=>{order.push('c');return release;});
 const d=slots.acquire(1000,'busy').then(release=>{order.push('d');return release;});
 assert.deepEqual(slots.size,{active:2,waiting:2});
 a();a();// releasing twice frees one slot only
 const releaseC=await c;assert.deepEqual(order,['c']);assert.deepEqual(slots.size,{active:2,waiting:1});
 b();const releaseD=await d;assert.deepEqual(order,['c','d']);
 await assert.rejects(slots.acquire(20,'서버에서 처리 중인 작업이 많습니다.'),/작업이 많습니다/);
 assert.deepEqual(slots.size,{active:2,waiting:0});
 releaseC();releaseD();assert.deepEqual(slots.size,{active:0,waiting:0});
});
test('long actions cannot take the slots short requests need',async()=>{
 const lanes=createWorkerLanes({general:3,long:2,control:1,queueMs:30});
 const first=await lanes.acquire('test'),second=await lanes.acquire('message');
 await assert.rejects(lanes.acquire('scheduled_run'),WorkerBusyError);
 assert.deepEqual(lanes.general.size,{active:2,waiting:0});// a long action waiting for its lane holds no general slot
 const short=await lanes.acquire('claim_due');
 await assert.rejects(lanes.acquire('recover_runs'),WorkerBusyError);// all three general slots are now taken
 const control=await lanes.acquire('heartbeat_run');
 control();
 const generation=await lanes.acquire('interrupt_generation');generation();
 const startGeneration=await lanes.acquire('begin_generation');startGeneration();
 first();first();// releasing twice frees one long and one general slot only
 assert.deepEqual(lanes.long.size,{active:1,waiting:0});assert.deepEqual(lanes.general.size,{active:2,waiting:0});
 const third=await lanes.acquire('retry_run');
 for(const release of [second,third,short,control])release();
 assert.deepEqual(lanes.general.size,{active:0,waiting:0});assert.deepEqual(lanes.long.size,{active:0,waiting:0});assert.deepEqual(lanes.control.size,{active:0,waiting:0});
});

// Runs only against the disposable test database prepared by the Python storage tests
// (docker compose --profile test-db up -d postgres-test, then the pytest run applies the schema).
const testUrl=process.env.A4B_TEST_DATABASE_URL;
test('assistant checkpoints persist in PostgreSQL per workspace',{skip:!testUrl&&'A4B_TEST_DATABASE_URL이 없으면 건너뜁니다.'},async()=>{
 // The app role the Python fixture creates: not a superuser, so row-level security applies.
 const url=new URL(testUrl!);url.username='a4b_test_app';url.password='a4b-test-app';
 const pool=new pg.Pool({connectionString:url.toString(),max:2});
 const credential:any={id:'local',provider:'openai',data:{apiKey:'sk-test-protected-123456'}};
 const waiting={summary:'점수를 분류해요.',requirements:[{id:'r1',description:'점수 분류',evidence:'고객 점수를 분류',state:'needs_input'}],questions:[{question:'기준 점수를 알려주세요.',choices:['80 이상']}]};
 const fetch=async()=>new Response(JSON.stringify({status:'completed',output:[{type:'message',content:[{type:'output_text',text:JSON.stringify(waiting)}]}]}));
 const workspaceA='0b4e7f1e-0000-4000-8000-'+Date.now().toString().padStart(12,'0').slice(-12),workspaceB=workspaceA.replace('0b4e','1b4e');
 try{
  const saver=new PostgresCheckpointSaver(pool,workspaceA);
  const prompt='고객 점수를 분류하고 화면 결과를 보고 싶어요';
  const result=await orchestrateWorkflow(prompt,{credential,credentials:[],fetch:fetch as any,checkpointer:saver,threadId:'shared-thread'});
  assert.equal(result.orchestration?.status,'waiting_for_answer');await saver.close();
  const options={credential,credentials:[]} as any;
  const reloaded=await createAgentGraph(options,new PostgresCheckpointSaver(pool,workspaceA)).getState({configurable:{thread_id:'shared-thread'}});
  assert.equal(reloaded.values.prompt,prompt);
  const other=await createAgentGraph(options,new PostgresCheckpointSaver(pool,workspaceB)).getState({configurable:{thread_id:'shared-thread'}});
  assert.equal(other.values.prompt,undefined);
  // Row-level security hides other workspaces even without a workspace_id filter.
  const client=await pool.connect();
  try{await client.query('BEGIN');await client.query("SELECT set_config('a4b.workspace_id',$1,true)",[workspaceB]);
   assert.equal((await client.query('SELECT count(*)::int AS n FROM a4b_builder.agent_checkpoints')).rows[0].n,0);await client.query('ROLLBACK');}
  finally{client.release();}
 }finally{await pool.end();}
});

test('a failed first checkpoint load is retried on the next request',async()=>{
 let attempts=0;
 const client={query:async(sql:string)=>({rows:[]}),release:()=>{}};
 const pool:any={connect:async()=>{attempts++;if(attempts===1)throw new Error('RDS briefly unreachable');return client;}};
 const saver=new PostgresCheckpointSaver(pool,'local');
 const config={configurable:{thread_id:'t1'}};
 await assert.rejects(saver.getTuple(config),/unreachable/);
 assert.equal(await saver.getTuple(config),undefined);
 assert.equal(attempts,2);
});
test('startup refuses an unreachable business-data DB',async()=>{
 const pool:any={query:async()=>{throw new Error('ECONNREFUSED');}};
 await assert.rejects(checkBuilderDatabase({kind:'postgres',databaseUrl:'postgresql://app@127.0.0.1:1/a4b'},pool),/접속하지 못했습니다/);
 await checkBuilderDatabase({kind:'sqlite'});
});
test('preflight exits non-zero before serving when the DB is unreachable',()=>{
 const child=spawnSync(process.execPath,['--import','tsx','src/index.ts','--preflight'],{cwd:API_ROOT,encoding:'utf8',timeout:60000,
  env:{...process.env,A4B_STORAGE:'postgres',A4B_DATABASE_URL:'postgresql://app@127.0.0.1:1/a4b',EASYFLOW_PUBLIC_URL:'',A4B_AUTH_MODE:'off'}});
 assert.notEqual(child.status,0);assert.match(child.stderr,/접속하지 못했습니다/);
 const sqlite=spawnSync(process.execPath,['--import','tsx','src/index.ts','--preflight'],{cwd:API_ROOT,encoding:'utf8',timeout:60000,
  env:{...process.env,A4B_STORAGE:'sqlite',EASYFLOW_PUBLIC_URL:'https://service.example.com',A4B_AUTH_MODE:'off'}});
 assert.notEqual(sqlite.status,0);assert.match(sqlite.stderr,/로컬 개발에서만/);
});
test('startup requires table privileges, RLS, the current schema and a receipt for this volume and DB',{skip:!testUrl&&'A4B_TEST_DATABASE_URL이 없으면 건너뜁니다.'},async()=>{
 const config={kind:'postgres' as const,databaseUrl:testUrl!};
 const admin=new pg.Pool({connectionString:testUrl,max:1});
 const url=new URL(testUrl!);url.username='a4b_test_app';url.password='a4b-test-app';
 const app=new pg.Pool({connectionString:url.toString(),max:1});
 const directory=await mkdtemp(join(tmpdir(),'a4b-cutover-'));
 const receipt=randomUUID();
 const check=(stoppedAt?:string)=>checkBuilderDatabase(config,app,directory,stoppedAt);
 try{
  await assert.rejects(checkBuilderDatabase(config,admin),/행 수준 보안을 우회/);
  await assert.rejects(check(),/이전 완료 기록/);
  await writeFile(join(directory,'.a4b-sqlite-import'),receipt);
  await assert.rejects(check(),/이전 완료 기록/);
  await admin.query('INSERT INTO a4b_builder.sqlite_imports(id,workspace_count) VALUES($1,0)',[receipt]);
  await check();
  await check('2020-01-01T00:00:00Z');
  await assert.rejects(check(new Date(Date.now()+60000).toISOString()),/마지막으로 정지/);
  for(const privilege of ['SELECT','INSERT','UPDATE','DELETE']){
   await admin.query(`REVOKE ${privilege} ON a4b_builder.builder_assets FROM a4b_app`);
   try{await assert.rejects(check(),/SELECT·INSERT·UPDATE·DELETE/);}
   finally{await admin.query(`GRANT ${privilege} ON a4b_builder.builder_assets TO a4b_app`);}
  }
  await admin.query('ALTER TABLE a4b_builder.checkpoint_blobs DISABLE ROW LEVEL SECURITY');
  try{await assert.rejects(check(),/행 수준 보안/);}
  finally{await admin.query('ALTER TABLE a4b_builder.checkpoint_blobs ENABLE ROW LEVEL SECURITY');}
  const {rows:[latest]}=await admin.query('DELETE FROM a4b_builder.schema_migrations WHERE version=$1 RETURNING version,name,applied_at',[REQUIRED_SCHEMA_VERSION]);
  try{await assert.rejects(check(),/스키마가 최신이 아니거나/);}
  finally{await admin.query('INSERT INTO a4b_builder.schema_migrations(version,name,applied_at) VALUES($1,$2,$3)',[latest.version,latest.name,latest.applied_at]);}
  await check();
 }finally{
  await admin.query('DELETE FROM a4b_builder.sqlite_imports WHERE id=$1',[receipt]);
  await admin.end();await app.end();await rm(directory,{recursive:true,force:true});
 }
});
