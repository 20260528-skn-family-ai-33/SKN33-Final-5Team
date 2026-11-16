import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {Readable} from 'node:stream';
import pg from 'pg';
import {AdminMonitoring,requireAdmin,diagnosticText,safeRecord,monitorFilters} from '../src/admin-monitoring';
import type {AuthUser} from '../src/auth-users';
import type {IncomingMessage,ServerResponse} from 'node:http';
const admin:AuthUser={id:randomUUID(),email:'admin@example.test',name:'Admin',role:'admin',status:'active'};
const member:AuthUser={...admin,id:randomUUID(),role:'member'};
function response(){let body:any;return {res:{setHeader(){},writeHead(){},end(value:string){body=JSON.parse(value);}} as unknown as ServerResponse,body:()=>body};}
function request(method='GET',body?:unknown){const req=Readable.from(body?[JSON.stringify(body)]:[]);return Object.assign(req,{method}) as unknown as IncomingMessage;}
test('monitoring denies anonymous, member and disabled administrator before database access',async()=>{
 const service=new AdminMonitoring(undefined,{query:async()=>{throw Error('database must not be called');},end:async()=>{}});
 for(const user of [undefined,member,{...admin,status:'disabled' as const}]){
  assert.throws(()=>requireAdmin(user),{status:user?403:401});
  await assert.rejects(service.handle(request(),response().res,new URL('http://localhost/api/admin/monitoring'),user),{status:user?403:401});
 }
});
test('diagnostic projections omit raw payloads and redact credentials',()=>{
 const safe=safeRecord({kind:'generation',id:randomUUID(),request_text:'private prompt',input:'private document',reason:'Bearer secret123 contact@example.com https://host/?token=secret password=hunter2',diagnostics:{body:'secret body',validation:[{field:'draft.nodes',type:'missing',input:'secret input',msg:'provider body'}]},events:[{node:'requirements',status:'failed',input:'private'}]});
 assert.ok(!JSON.stringify(safe).includes('private'));assert.ok(!JSON.stringify(safe).includes('secret input'));assert.ok(!JSON.stringify(safe).includes('provider body'));
 assert.ok(!safe.reason.includes('secret123'));assert.ok(!safe.reason.includes('contact@example.com'));assert.ok(!safe.reason.includes('hunter2'));
 assert.equal(diagnosticText('a'.repeat(3000),2000).length,2000);
 for(const query of ['days=1000','page=-1','kind=unknown','status=success','triage=bad','q='+'a'.repeat(101)])assert.throws(()=>monitorFilters(new URL('http://localhost/?'+query)),{status:400});
});
const url=process.env.A4B_MONITOR_TEST_DATABASE_URL;
test('PostgreSQL: admin projections, workspace isolation, filters, details and concurrent triage', {skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/test/,'only disposable test databases');
 const c=new pg.Client({connectionString:url});await c.connect();await c.query('BEGIN');
 try{
  const id=randomUUID(),resourceId=member.id+':'+id,sid=randomUUID(),when=new Date().toISOString();
  await c.query(`INSERT INTO a4b.users(id,email,name,auth_provider,auth_subject,role) VALUES($1,$2,'Admin','fixture',$1::uuid::text,'admin'),($3,$4,'Member','fixture',$3::uuid::text,'member')`,[admin.id,admin.id+'@example.test',member.id,member.id+'@example.test']);
  await c.query(`INSERT INTO a4b.workflow_generation_requests(workspace_id,id,session_id,json) VALUES($1,$2,$3,$4)`,[member.id,id,sid,JSON.stringify({request_id:id,session_id:sid,status:'failed',stage:'request_analysis',node:'requirements',reason_code:'invalid_model_response',reason:'format invalid',started_at:when,request_text:'SECRET PROMPT',events:[{node:'requirements',status:'failed',at:when,input:'SECRET INPUT'}]})]);
  await c.query('SET LOCAL ROLE a4b_app');
  const owner=(await c.query("SELECT rolsuper,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname='a4b_monitor_owner'")).rows[0];
  assert.deepEqual(owner,{rolsuper:false,rolbypassrls:false,rolcanlogin:false});
  assert.equal((await c.query("SELECT pg_has_role(current_user,'a4b_monitor_owner','MEMBER') member")).rows[0].member,false);
  assert.equal((await c.query('SELECT * FROM a4b.workflow_generation_requests')).rowCount,0,'ordinary workspace visibility unchanged');
  await c.query('SAVEPOINT denied');
  await assert.rejects(c.query('SELECT * FROM a4b.monitor_records($1,now()-interval \'1 day\',now()+interval \'1 second\')',[member.id]),{code:'42501'});await c.query('ROLLBACK TO denied');
  await c.query('SAVEPOINT direct');await assert.rejects(c.query('SELECT * FROM a4b.monitor_triage'),{code:'42501'});await c.query('ROLLBACK TO direct');
  const service=new AdminMonitoring(undefined,{query:(s,v)=>c.query(s,v),end:async()=>{}});
  const out=response();await service.handle(request(),out.res,new URL('http://localhost/api/admin/monitoring?days=1'),admin);
  assert.equal(out.body().total,1);assert.equal(out.body().summary.failed,1);assert.equal(out.body().items[0].member_id,member.id);assert.ok(!JSON.stringify(out.body()).includes('SECRET'));
  const hidden=response();await service.handle(request(),hidden.res,new URL('http://localhost/api/admin/monitoring?kind=execution'),admin);assert.equal(hidden.body().total,0);
  await c.query('RESET ROLE');
  await c.query("INSERT INTO a4b.workflow_generation_requests(workspace_id,id,session_id,json) SELECT $1,id,session_id,json FROM a4b.workflow_generation_requests WHERE workspace_id=$2 AND id=$3",[admin.id,member.id,id]);
  await c.query('SET LOCAL ROLE a4b_app');
  const detail=response();const endpoint=new URL('http://localhost/api/admin/monitoring/generation/'+resourceId+'?days=1');await service.handle(request(),detail.res,endpoint,admin);assert.equal(detail.body().item.events[0].node,'requirements');assert.equal(detail.body().item.member_id,member.id);
  const saved=response();await service.handle(request('PATCH',{status:'investigating',note:'verify contract',revision:0}),saved.res,endpoint,admin);assert.equal(saved.body().triage.revision,1);
  await c.query('SAVEPOINT conflict');await assert.rejects(service.handle(request('PATCH',{status:'resolved',note:'stale',revision:0}),response().res,endpoint,admin),{status:409});await c.query('ROLLBACK TO conflict');
  const final=response();await service.handle(request('PATCH',{status:'resolved',note:'fixed',revision:1}),final.res,endpoint,admin);assert.equal(final.body().triage.revision,2);
  const history=response();await service.handle(request(),history.res,endpoint,admin);assert.equal(history.body().history.length,2);
  await c.query('RESET ROLE');await c.query("UPDATE a4b.users SET role='member' WHERE id=$1",[admin.id]);await c.query('SET LOCAL ROLE a4b_app');
  await c.query('SAVEPOINT revoked');await assert.rejects(service.handle(request(),response().res,new URL('http://localhost/api/admin/monitoring'),admin),{status:403});await c.query('ROLLBACK TO revoked');
 }finally{await c.query('ROLLBACK');await c.end();}
});
