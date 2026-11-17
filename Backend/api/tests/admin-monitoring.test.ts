import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {Readable} from 'node:stream';
import pg from 'pg';
import {AdminMonitoring,requireAdmin,diagnosticText,safeRecord,monitorFilters,safeRequestContext,memberEmail} from '../src/admin-monitoring';
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
  for(const path of ['', '/generation/member:request'])await assert.rejects(service.handle(request(),response().res,new URL('http://localhost/api/admin/monitoring'+path),user),{status:user?403:401});
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

test('request context masks secrets, excludes system/tool payloads and bounds readable text',()=>{
 const secret='known-test-secret-123';process.env.A4B_MONITOR_TEST_SECRET=secret;
 try{
  const raw='안녕하세요 '+secret+' {"password":"two word password","access_token":"access-secret"} API key=api-secret 비밀번호="한글 비밀" Authorization: Bearer bearer-secret https://host/?token=url-secret user@example.test sk-testsecret';
  const safe=safeRequestContext({prompt:raw,conversation_source:'session_latest',conversation:[{role:'system',content:'private system'},{role:'tool',content:'private tool'},{role:'user',content:raw,credentials:'do not expose'},{role:'assistant',content:'<script>alert(1)</script> 정상 답변'},{role:'assistant',content:{secret:'object'}}]});
  const encoded=JSON.stringify(safe);
  for(const value of [secret,'two word password','access-secret','api-secret','한글 비밀','bearer-secret','url-secret','user@example.test','sk-testsecret','private system','private tool','credentials'])assert.ok(!encoded.includes(value),value);
  assert.match(safe.prompt!,/안녕하세요/);assert.equal(safe.conversation.length,2);assert.match(safe.conversation[1].content,/정상 답변/);
  assert.equal(memberEmail('member@example.test'),'member@example.test');assert.equal(memberEmail('bad\nemail@example.test'),null);
  assert.equal(safeRequestContext(null).prompt,null);assert.equal(safeRequestContext({prompt:'  '}).prompt,null);
  const long=safeRequestContext({prompt:'a'.repeat(9000),conversation:Array.from({length:45},()=>({role:'user',content:'나'.repeat(5000)}))});
  assert.equal(long.prompt!.length,8000);assert.equal(long.prompt_truncated,true);assert.equal(long.conversation.length,40);assert.equal(long.conversation_truncated,true);assert.equal(long.conversation[0].content.length,4000);assert.equal(long.conversation[0].truncated,true);
 }finally{delete process.env.A4B_MONITOR_TEST_SECRET;}
});

test('authorization values are fully masked in prompts and both conversation roles',()=>{
 const cases=[
  'Authorization: Basic dXNlcjpwYXNz',
  'Authorization: Bearer "quoted-token-secret"',
  "Authorization: Bearer 'single-token-secret'",
  '"Authorization": "Bearer json-token-secret"',
  "'Authorization': 'Basic json-basic-secret'",
  'Proxy-Authorization: Basic proxy-secret',
  'Bearer "standalone-secret"',
 ];
 for(const value of cases){
  const safe=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  for(const text of [safe.prompt,...safe.conversation.map((t:{content:string})=>t.content)]){
   assert.ok(text?.includes('보호된'),value);
   for(const secret of ['dXNlcjpwYXNz','quoted-token-secret','single-token-secret','json-token-secret','json-basic-secret','proxy-secret','standalone-secret'])assert.ok(!text?.includes(secret),value);
  }
 }
 const safe=safeRequestContext({prompt:'x'.repeat(4000),prompt_truncated:true,prompt_storage_limit:4000,prompt_storage_truncated:true});
 assert.equal(safe.prompt_truncated,true);assert.equal(safe.prompt_storage_limit,4000);assert.equal(safe.prompt_storage_truncated,true);
 assert.equal(safeRequestContext({prompt:'legacy',prompt_storage_limit:4000}).prompt_storage_truncated,null);
});

test('escaped and unfinished authentication quotes never leave token fragments',()=>{
 for(const scheme of ['Bearer','Basic'])for(const quote of ['"',"'"])for(const slashes of [0,1,2,3])for(const closed of [false,true]){
  const delimiter='\\'.repeat(slashes)+quote;
  const token=scheme==='Basic'?'dXNlcjpwYXNz':'sample-token';
  const value=`안내 ${scheme} ${delimiter}${token}${closed?delimiter:''}\n다음 줄 설명`;
  assert.ok(!diagnosticText(value).includes(token),JSON.stringify(value));
  const context=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  for(const text of [context.prompt,...context.conversation.map((t:{content:string})=>t.content)]){
   assert.ok(!text?.includes(token),JSON.stringify(value));
   assert.match(text!,/안내/);assert.match(text!,/보호된/);assert.match(text!,/다음 줄 설명/);
  }
 }
});

test('ordinary basic prose is preserved while standalone Basic credentials are masked',()=>{
 for(const value of ['Please create a basic workflow to summarize invoices.','Basic workflow automation','Basic d29ya2Zsb3c=','BASIC invoice summary', 'Use basic "workflow" steps.', 'basic knowledge for invoice processing']){
  const result=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  assert.equal(result.prompt,value);assert.deepEqual(result.conversation.map((t:{content:string})=>t.content),[value,value]);
 }
 for(const credentials of ['user:pass',':password','user:','사용자:비밀번호']){
  const encoded=Buffer.from(credentials).toString('base64');
  assert.equal(diagnosticText('Basic '+encoded+' 끝 설명'),'Basic [보호된 인증값] 끝 설명');
 }
 for(const value of ['Basic dXNlcjpwYXNz then summarize invoices.', 'basic dXNlcjpwYXNz, keep this explanation.']){
  const result=diagnosticText(value);assert.ok(!result.includes('dXNlcjpwYXNz'));assert.match(result,/보호된/);assert.match(result,/then summarize|keep this/);
 }
});

test('Basic punctuation and incomplete or serialized secret fields are masked',()=>{
 const cases=[
  ['인증값은 `Basic dXNlcjpwYXNz`입니다.','dXNlcjpwYXNz'],
  ['Basic dXNlcjpwYXNz! continue','dXNlcjpwYXNz'],
  ['password="alpha beta gamma','alpha beta gamma'],
  [String.raw`{\"password\":\"alpha beta gamma\"}`, 'alpha beta gamma'],
  [String.raw`{\"password\":\"alpha beta gamma`, 'alpha beta gamma'],
 ];
 for(const [value,secret] of cases){
  const result=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  for(const text of [diagnosticText(value),result.prompt,...result.conversation.map((t:{content:string})=>t.content)]){
   for(const piece of secret.split(' '))assert.ok(!text?.includes(piece),value);
  }
 }
 assert.equal(diagnosticText('인증값은 `Basic dXNlcjpwYXNz`입니다.'),'인증값은 `Basic [보호된 인증값]`입니다.');
 assert.equal(diagnosticText('Basic dXNlcjpwYXNz! continue'),'Basic [보호된 인증값]! continue');
});

test('secret key quoting preserves closed-value suffixes and masks incomplete values',()=>{
 for(const key of ['password','token','비밀번호'])for(const quote of ['"',"'"])for(const count of [0,1,2,3])for(const closed of [true,false]){
  const delimiter='\\'.repeat(count)+quote;
  const value=`${delimiter}${key}${delimiter}: ${delimiter}alpha beta gamma${closed?delimiter+'; 설명 유지':''}`;
  const safe=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  for(const text of [safe.prompt,...safe.conversation.map((t:{content:string})=>t.content)]){
   for(const fragment of ['alpha','beta','gamma'])assert.ok(!text?.includes(fragment),value);
   if(closed)assert.match(text!,/설명 유지/);
  }
 }
 assert.match(diagnosticText(String.raw`password="alpha \"beta\" gamma"; 설명 유지`),/설명 유지/);
 assert.ok(!diagnosticText(String.raw`password="alpha \"beta\" gamma"; 설명 유지`).includes('gamma'));
 assert.ok(!diagnosticText(String.raw`\"password\"\=\"alpha beta gamma`).includes('gamma'));
});

test('session cookie credentials disappear from prompt and both conversation roles',()=>{
 const secret='A'.repeat(43);
 for(const name of ['__Host-a4b-session','a4b-session'])for(const value of [`Cookie: ${name}=${secret}; other=value`,`Set-Cookie: ${name}=${secret}; HttpOnly`,`${name}=${secret}`,JSON.stringify({Cookie:`${name}=${secret}`})]){
  const safe=safeRequestContext({prompt:value,conversation:[{role:'user',content:value},{role:'assistant',content:value}]});
  assert.ok(!JSON.stringify(safe).includes(secret),value);
 }
});
test('Unicode display limits keep complete code points and accurate flags',()=>{
 for(const extra of ['', '나']){
  const prompt='가'.repeat(7999)+'📄'+extra,content='가'.repeat(3999)+'📄'+extra;
  const safe=safeRequestContext({prompt,conversation:[{role:'user',content},{role:'assistant',content}]});
  assert.equal(safe.prompt,'가'.repeat(7999)+'📄');assert.equal(safe.prompt_truncated,!!extra);
  for(const turn of safe.conversation){assert.equal(turn.content,'가'.repeat(3999)+'📄');assert.equal(turn.truncated,!!extra);}
 }
});
test('long non-email text and mixed email text are processed without quadratic scanning',()=>{
 const plain='a'.repeat(100000),start=performance.now();
 assert.equal(diagnosticText('.'.repeat(100000)+'a',Infinity),'.'.repeat(100000)+'a');
 assert.equal(diagnosticText('user@example.test.'),'[이메일 숨김].');
 assert.equal(diagnosticText(plain,Infinity),plain);
 const mixed=diagnosticText(plain+' user.name+tag@example.test '+plain,Infinity);
 assert.equal(mixed,plain+' [이메일 숨김] '+plain);
 assert.ok(performance.now()-start<2000,'100k inputs should finish within a generous 2s budget');
});

test('email filename and rotated log suffixes survive masking in every context field',()=>{
 for(const [input,expected] of [
  ['첨부 이름 ops@example.com.7z','첨부 이름 [이메일 숨김].7z'],
  ['백업 로그 ops@example.com.log.1','백업 로그 [이메일 숨김].1'],
  ['수신자 ops@example.com','수신자 [이메일 숨김]'],
  ['ops@example.com.7z.1','[이메일 숨김].7z.1'],
  ['ops@example.com123','[이메일 숨김]123'],
  ['plain.7z','plain.7z'],['ops@host.1','ops@host.1']]){
  const safe=safeRequestContext({prompt:input,conversation:[{role:'user',content:input},{role:'assistant',content:input}]});
  for(const text of [safe.prompt,...safe.conversation.map((t:{content:string})=>t.content)])assert.equal(text,expected);
 }
 const start=performance.now(),input='ops@example.com'+'.1'.repeat(50000);
 assert.equal(diagnosticText(input,Infinity),'[이메일 숨김]'+'.1'.repeat(50000));
 assert.ok(performance.now()-start<2000,'many suffix segments remain linear');
});
