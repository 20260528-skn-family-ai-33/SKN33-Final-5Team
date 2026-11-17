import type {IncomingMessage,ServerResponse} from 'node:http';
import pg from 'pg';
import {postgresTls,type AuthUser} from './auth-users';
import {AuthError,authJson} from './cognito-auth';
import {teamRequestBody} from './team-api';
import type {AuthConfig} from './auth-config';

export function requireAdmin(user?:AuthUser):asserts user is AuthUser {
 if(!user)throw new AuthError(401,'관리자 계정으로 로그인하세요.');
 if(user.role!=='admin'||user.status!=='active')throw new AuthError(403,'관리자만 모니터링을 사용할 수 있습니다.');
}
// Standalone Basic is ambiguous prose: require canonical Base64 containing the
// username/password separator. Headers remain masked independently below.
function maskStandaloneBasic(value:string):string {
 return value.replace(/\bBasic[ \t]+\\*["']?([A-Za-z0-9+/]+={0,2})\\*["']?(?![A-Za-z0-9+/=])/gi,(match,encoded:string)=>{
  const decoded=Buffer.from(encoded,'base64');
  const canonical=decoded.toString('base64').replace(/=+$/,'');
  return canonical===encoded.replace(/=+$/,'')&&decoded.includes(0x3a)?'Basic [보호된 인증값]':match;
 });
}
// Parse quoted secret values separately from unquoted values. Missing closing
// delimiters hide the remaining value rather than falling back to its first word.
function maskSecretFields(text:string):string {
 const keys=/(?<![\p{L}\p{N}_])(?:password|passwd|token|access[_-]?token|refresh[_-]?token|secret|client[_-]?secret|api[ _-]?key|비밀번호|비밀키|토큰)(?:\\*["'])?\s*\\*[:=：]\s*/giu;
 let result='',cursor=0,match:RegExpExecArray|null;
 while((match=keys.exec(text))!==null){
  const start=keys.lastIndex;
  const opening=/^(\\*)(["'])/.exec(text.slice(start));
  let end=start;
  if(opening){
   end=text.length;
   for(let i=start+opening[0].length;i<text.length;i++){
    if(text[i]!==opening[2])continue;
    let slashes=0;for(let j=i-1;j>=start&&text[j]==='\\';j--)slashes++;
    if(opening[1].length===0?slashes%2===0:slashes===opening[1].length){end=i+1;break;}
   }
  }else{
   while(end<text.length&&!/[\s,;]/.test(text[end]))end++;
  }
  result+=text.slice(cursor,start)+'[보호된 값]';cursor=end;keys.lastIndex=end;
 }
 return result+text.slice(cursor);
}
// Diagnostic projections never include raw payloads. Request context is a separate admin-only projection.
export function diagnosticText(value:unknown,max=1000):string {
 let text=typeof value==='string'?value:'';
 for(const [key,secret] of Object.entries(process.env))if(/KEY|PASSWORD|SECRET|TOKEN|DATABASE_URL/.test(key)&&secret&&secret.length>=6)text=text.split(secret).join('[보호된 값]');
 // Header values can contain a scheme and spaces; unquoted headers are
 // conservatively redacted to EOL. Secret field values are parsed independently.
 return maskStandaloneBasic(maskSecretFields(text)).replace(/(["']?(?:proxy-)?authorization["']?[ \t]*[:=：][ \t]*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]+)/gi,'$1[보호된 값]')
  // Free text may contain escaped, nested or unfinished quotes. Treat the rest
  // of a Bearer credential line as sensitive instead of guessing its closing delimiter.
  .replace(/\bBearer[ \t]+[^\r\n]*/gi,'[보호된 인증값]').replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g,'[보호된 키]')

  .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g,'[보호된 토큰]')
  .replace(/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g,'[보호된 키]')
  .replace(/(?:https?:\/\/|postgres(?:ql)?:\/\/)[^\s]+/gi,'[주소 숨김]')
  .replace(/\bsk-[\w-]+/g,'[보호된 키]')
  .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g,'[이메일 숨김]')
  .slice(0,max);
}
const identifier=(v:unknown)=>typeof v==='string'?v.replace(/[^a-zA-Z0-9_.:/-]/g,'').slice(0,120):null;
export function safeRecord(raw:any){
 const diagnostics=raw.diagnostics&&typeof raw.diagnostics==='object'?raw.diagnostics:{};
 return {kind:identifier(raw.kind),id:identifier(raw.id),request_id:identifier(raw.request_id||raw.id),member_id:identifier(raw.member_id),status:identifier(raw.status),stage:identifier(raw.stage),node:identifier(raw.node),
  reason_code:identifier(raw.reason_code),reason:diagnosticText(raw.reason),error_type:identifier(raw.error_type),occurred_at:raw.occurred_at||raw.started_at,started_at:raw.started_at,finished_at:raw.finished_at,workflow_id:identifier(raw.workflow_id),
  diagnostics:{trace_id:identifier(diagnostics.trace_id),model:identifier(diagnostics.model),release:identifier(diagnostics.release),http_status:Number.isInteger(diagnostics.http_status)?diagnostics.http_status:null,
   validation:Array.isArray(diagnostics.validation)?diagnostics.validation.slice(0,20).map((e:any)=>({field:diagnosticText(e.field,150),type:identifier(e.type)})):[]},
  events:Array.isArray(raw.events)?raw.events.slice(0,100).map((e:any)=>({at:e.at,stage:identifier(e.stage),node:identifier(e.node),status:identifier(e.status),reason_code:identifier(e.reason_code),attempt:e.attempt})):[]};
}
// Login email is deliberately exposed only from the users RPC, never extracted from free text.
export function memberEmail(value:unknown):string|null {
 return typeof value==='string'&&value.length<=254&&/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(value)?value:null;
}
export function safeRequestContext(raw:any){
 const input=raw&&typeof raw==='object'?raw:{};
 const valid=Array.isArray(input.conversation)?input.conversation.filter((m:any)=>m&&['user','assistant'].includes(m.role)&&typeof m.content==='string'):[];
 const prompt=typeof input.prompt==='string'&&input.prompt.trim()?diagnosticText(input.prompt,Infinity):null;
 return {
  prompt:prompt===null?null:prompt.slice(0,8000),
  prompt_storage_limit:input.prompt_storage_limit===4000?4000:null,
  prompt_storage_truncated:typeof input.prompt_storage_truncated==='boolean'?input.prompt_storage_truncated:null,
  prompt_truncated:input.prompt_truncated===true||(prompt!==null&&prompt.length>8000),
  conversation:valid.slice(-40).map((m:any)=>{const content=diagnosticText(m.content,Infinity);return {role:m.role as 'user'|'assistant',content:content.slice(0,4000),truncated:content.length>4000};}),
  conversation_source:input.conversation_source==='session_latest'?'session_latest':'unavailable',
  conversation_truncated:input.conversation_truncated===true||valid.length>40,
 };
}
export function monitorFilters(url:URL){
 const p=url.searchParams,days=Number(p.get('days')||7),page=Number(p.get('page')||1),kind=p.get('kind')||'all',status=p.get('status')||'failed',triage=p.get('triage')||'all';
 if(![1,7,30,90].includes(days)||!Number.isInteger(page)||page<1||page>10000||!['all','generation','execution','document'].includes(kind)||!['all','failed','blocked'].includes(status)||!['all','unreviewed','investigating','resolved'].includes(triage))throw new AuthError(400,'모니터링 조회 조건을 확인하세요.');
 const query=(p.get('q')||'').trim();if(query.length>100)throw new AuthError(400,'검색어는 100자 이내로 입력하세요.');
 return {days,page,kind,status,triage,query};
}
export interface MonitorDatabase {query(text:string,values?:any[]):Promise<{rows:any[]}>;end():Promise<void>}
export class AdminMonitoring {
 private database?:MonitorDatabase;
 constructor(private config?:AuthConfig,database?:MonitorDatabase){this.database=database;}
 private db(){
  if(!this.database){
   if(this.config?.userStore!=='postgres')throw new AuthError(503,'모니터링은 운영 PostgreSQL 저장소에서 사용할 수 있습니다.');
   const pool=new pg.Pool({connectionString:this.config.databaseUrl,ssl:postgresTls(this.config),max:2,connectionTimeoutMillis:5000,statement_timeout:15000});
   pool.on('error',()=>console.error('모니터링 DB 연결 오류'));this.database=pool;
  }return this.database;
 }
 async close(){await this.database?.end();}
 async handle(req:IncomingMessage,res:ServerResponse,url:URL,user?:AuthUser){
  requireAdmin(user);
  const path=url.pathname,match=/^\/api\/admin\/monitoring\/(generation|execution|document)\/([a-zA-Z0-9:-]{1,120})$/.exec(path);
  if(path!=='/api/admin/monitoring'&&!match)throw new AuthError(404,'관리자 기능을 찾을 수 없습니다.');
  const method=req.method||'GET';
  if(method!=='GET'&&!(match&&method==='PATCH')){res.setHeader('Allow',match?'GET, PATCH':'GET');throw new AuthError(405,'지원하지 않는 요청입니다.');}
  try{
   const db=this.db();
   if(method==='PATCH'&&match){
    const body=await teamRequestBody(req,['status','note','revision'],12000);
    if(!['unreviewed','investigating','resolved'].includes(body.status)||typeof body.note!=='string'||body.note.length>2000||!Number.isInteger(body.revision)||body.revision<0)throw new AuthError(400,'처리 상태와 메모를 확인하세요.');
    const {rows}=await db.query('SELECT * FROM a4b.monitor_triage_save($1,$2,$3,$4,$5,$6)',[user.id,match[1],match[2],body.status,diagnosticText(body.note,2000),body.revision]);
    authJson(res,200,{triage:rows[0]});return;
   }
   const filters=monitorFilters(url),until=new Date(),since=new Date(until.valueOf()-filters.days*86400000);
   // One bounded database statement and snapshot for records, counts and triage.
   const {rows:[result]}=await db.query(`WITH records AS MATERIALIZED (
    SELECT r AS record FROM a4b.monitor_records($1,$2,$3,$4) r
   ), triage AS MATERIALIZED (SELECT * FROM a4b.monitor_triage_read($1)), scoped AS (
    SELECT record,coalesce(to_jsonb(t),'{}'::jsonb) triage FROM records
    LEFT JOIN triage t ON t.kind=record->>'kind' AND t.record_id=record->>'id'
    WHERE ($5='all' OR record->>'kind'=$5)
   ), filtered AS (
    SELECT * FROM scoped WHERE ($6='all' OR record->>'status'=$6)
    AND ($7='all' OR coalesce(triage->>'status','unreviewed')=$7)
    AND ($8='' OR strpos(lower(concat_ws(' ',record->>'id',record->>'member_id',record->>'node',record->>'reason_code',record->>'error_type')),lower($8))>0)
   ) SELECT
    (SELECT count(*)::integer FROM filtered) total,
    (SELECT jsonb_build_object('requests',count(*),'failed',count(*) FILTER(WHERE record->>'status'='failed'),
     'blocked',count(*) FILTER(WHERE record->>'status'='blocked'),'affected_users',count(DISTINCT record->>'member_id') FILTER(WHERE record->>'status'='failed')) FROM scoped) summary,
    coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM (SELECT * FROM filtered ORDER BY coalesce(record->>'occurred_at',record->>'started_at')::timestamptz DESC,record->>'kind',record->>'id' LIMIT 25 OFFSET $9) p),'[]'::jsonb) items,
    coalesce((SELECT jsonb_agg(to_jsonb(g)) FROM (SELECT record->>'stage' stage,record->>'node' node,record->>'reason_code' reason_code,count(*)::integer count
     FROM scoped WHERE record->>'status'='failed' GROUP BY 1,2,3 ORDER BY count(*) DESC LIMIT 8) g),'[]'::jsonb) groups`,
    [user.id,since.toISOString(),until.toISOString(),match?.[2]||null,match?.[1]||filters.kind,match?'all':filters.status,filters.triage,filters.query,(filters.page-1)*25]);
   const items=(result.items||[]).map((x:any)=>({...safeRecord(x.record),triage:x.triage?.status?{...x.triage,note:diagnosticText(x.triage.note,2000)}:{status:'unreviewed',note:'',revision:0}}));
   const memberIds=[...new Set<string>(items.map((x:any)=>x.member_id).filter((id:any)=>typeof id==='string'))];
   const emails=memberIds.length?(await db.query('SELECT * FROM a4b.monitor_member_emails($1,$2::text[])',[user.id,memberIds])).rows:[];
   const emailById=new Map(emails.map((row:any)=>[row.member_id,memberEmail(row.email)]));
   const identified=items.map((item:any)=>({...item,member_email:emailById.get(item.member_id)||null}));
   if(match){
    const item=identified.find((x:any)=>x.kind===match[1]&&x.id===match[2]);if(!item)throw new AuthError(404,'선택 기간에서 요청을 찾을 수 없습니다.');
    const history=await db.query('SELECT * FROM a4b.monitor_triage_history($1,$2,$3)',[user.id,match[1],match[2]]);
    const context=await db.query('SELECT a4b.monitor_request_context($1,$2,$3) AS context',[user.id,match[1],match[2]]);
    authJson(res,200,{item:{...item,context:safeRequestContext(context.rows[0]?.context)},history:history.rows.map(x=>({...x,note:diagnosticText(x.note,2000)}))});return;
   }
   authJson(res,200,{...result,items:identified,groups:result.groups.map((g:any)=>({...g,stage:identifier(g.stage),node:identifier(g.node),reason_code:identifier(g.reason_code)})),page:filters.page,page_size:25,checked_at:until.toISOString()});
  }catch(error){
   if(error instanceof AuthError)throw error;
   const code=(error as any)?.code;
   if(code==='42501')throw new AuthError(403,'관리자 권한을 다시 확인하세요.');
   if(code==='40001')throw new AuthError(409,'다른 관리자가 변경했습니다. 상세 내용을 다시 열어주세요.');
   if(code==='P0002')throw new AuthError(404,'요청을 찾을 수 없습니다.');
   throw new AuthError(503,'모니터링 정보를 불러오지 못했습니다. DB 연결과 마이그레이션 상태를 확인하세요.');
  }
 }
}
