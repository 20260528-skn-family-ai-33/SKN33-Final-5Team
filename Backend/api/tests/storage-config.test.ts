import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import {storageConfig,workerLimits} from '../src/storage-config';
import {WorkerSlots} from '../src/worker-slots';
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
 assert.deepEqual(workerLimits({}),{general:12,control:4,queueMs:60000});
 assert.deepEqual(workerLimits({A4B_WORKER_MAX_CONCURRENCY:'3',A4B_WORKER_CONTROL_CONCURRENCY:'2'}),{general:3,control:2,queueMs:60000});
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
