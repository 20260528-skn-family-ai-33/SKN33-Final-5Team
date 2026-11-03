import {createHash,randomBytes,timingSafeEqual} from 'node:crypto';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {CognitoJwtVerifier} from 'aws-jwt-verify';
import type {AuthConfig} from './auth-config';
import type {AuthUser,AuthUsers} from './auth-users';

type Claims={sub:string;exp:number;[key:string]:unknown};
type Verify=(token:string)=>Promise<Claims>;
type Tokens={access:string;refresh:string;subject:string;expiresAt:number};
type Session=Tokens&{userId:string;endsAt:number;refreshing?:Promise<void>};
type Pending={binding:string;verifier:string;nonce:string;returnTo:string;endsAt:number};
export class AuthError extends Error {
 constructor(readonly status:number,message:string){super(message);}
}
const opaque=()=>randomBytes(32).toString('base64url');
const hash=(value:string)=>createHash('sha256').update(value).digest('base64url');
const tokenPattern=/^[A-Za-z0-9_-]{43}$/;
export function safeReturnTo(value:string|null,origin:string){
 if(!value||value.length>4096||!value.startsWith('/')||value.startsWith('//')||value.includes('\\'))return '/?screen=workflows';
 const url=new URL(value,origin);
 url.searchParams.delete('auth_error');
 return url.origin===origin&&!url.pathname.startsWith('/auth/')?url.pathname+url.search:'/?screen=workflows';
}
function cookie(req:IncomingMessage,name:string){
 const values=String(req.headers.cookie||'').split(';').map(s=>s.trim()).filter(s=>s.startsWith(name+'='));
 return values.length===1?values[0].slice(name.length+1):'';
}
function equal(a:string,b:string){return a.length===b.length&&timingSafeEqual(Buffer.from(a),Buffer.from(b));}
export function authJson(res:ServerResponse,status:number,value:unknown){
 res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(value));
}
function redirect(res:ServerResponse,location:string){res.writeHead(303,{Location:location,'Cache-Control':'no-store','Referrer-Policy':'no-referrer'});res.end();}
export class CognitoAuth {
 private pending=new Map<string,Pending>();
 private sessions=new Map<string,Session>();
 private verifyAccess:Verify;
 private verifyId:Verify;
 private fetch:typeof fetch;
 private now:()=>number;
 private secure:boolean;
 private sessionCookie:string;
 private loginCookie:string;
 constructor(readonly config:AuthConfig,readonly users:AuthUsers,options:{fetch?:typeof fetch;now?:()=>number;verifyAccess?:Verify;verifyId?:Verify}={}){
  this.fetch=options.fetch||fetch;this.now=options.now||Date.now;this.secure=config.origin.startsWith('https:');
  this.sessionCookie=this.secure?'__Host-a4b-session':'a4b-session';this.loginCookie=this.secure?'__Host-a4b-login':'a4b-login';
  // Reuse verifier instances so JWKS caching and key-rotation protection work across requests.
  const accessVerifier=CognitoJwtVerifier.create({userPoolId:config.userPoolId,clientId:config.clientId,tokenUse:'access'});
  const idVerifier=CognitoJwtVerifier.create({userPoolId:config.userPoolId,clientId:config.clientId,tokenUse:'id'});
  this.verifyAccess=options.verifyAccess||(token=>accessVerifier.verify(token));
  this.verifyId=options.verifyId||(token=>idVerifier.verify(token));
 }
 private setCookie(res:ServerResponse,name:string,value:string,maxAge:number){
  const previous=res.getHeader('Set-Cookie');const values=Array.isArray(previous)?previous:previous?[String(previous)]:[];
  res.setHeader('Set-Cookie',[...values,`${name}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${this.secure?'; Secure':''}`]);
 }
 private prune(){
  const now=this.now();for(const [key,p] of this.pending)if(p.endsAt<=now)this.pending.delete(key);
  for(const [key,s] of this.sessions)if(s.endsAt<=now)this.sessions.delete(key);
 }
 private async tokenRequest(parameters:URLSearchParams){
  const response=await this.fetch(this.config.domain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:parameters.toString(),redirect:'error',signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw new AuthError(401,'로그인 정보를 갱신하지 못했습니다. 다시 로그인하세요.');
  const text=await response.text();if(text.length>65536)throw new AuthError(401,'인증 응답을 확인할 수 없습니다.');
  const value=JSON.parse(text);
  if(value.token_type?.toLowerCase()!=='bearer'||typeof value.access_token!=='string'||value.access_token.length>32768)throw new AuthError(401,'인증 응답을 확인할 수 없습니다.');
  return value;
 }
 async currentUser(req:IncomingMessage):Promise<AuthUser|undefined>{
  this.prune();const raw=cookie(req,this.sessionCookie);if(!tokenPattern.test(raw))return;
  const key=hash(raw),session=this.sessions.get(key);if(!session)return;
  try{
   if(session.expiresAt<=this.now()+30000){
    session.refreshing??=(async()=>{
     const value=await this.tokenRequest(new URLSearchParams({grant_type:'refresh_token',client_id:this.config.clientId,refresh_token:session.refresh}));
     const claims=await this.verifyAccess(value.access_token);if(claims.sub!==session.subject)throw new AuthError(401,'인증된 회원이 달라졌습니다. 다시 로그인하세요.');
     session.access=value.access_token;session.expiresAt=claims.exp*1000;
     if(value.refresh_token)session.refresh=value.refresh_token;
    })().finally(()=>{session.refreshing=undefined;});
    await session.refreshing;
   }
   const claims=await this.verifyAccess(session.access);if(claims.sub!==session.subject)throw new AuthError(401,'다시 로그인하세요.');
  }catch{this.sessions.delete(key);throw new AuthError(401,'로그인이 만료되었습니다. 다시 로그인하세요.');}
  let user:AuthUser|undefined;
  try{user=await this.users.get(session.userId);}catch{throw new AuthError(503,'회원 정보를 확인할 수 없습니다. 잠시 후 다시 시도하세요.');}
  if(!user){this.sessions.delete(key);return;}
  if(user.status!=='active'){this.sessions.delete(key);throw new AuthError(403,'이 계정은 서비스 이용이 중지되었습니다.');}
  return user;
 }
 async handle(req:IncomingMessage,res:ServerResponse,url:URL):Promise<boolean>{
  const path=url.pathname,method=req.method||'GET';
  if(path==='/api/auth/me'&&method==='GET'){
   const user=await this.currentUser(req);authJson(res,200,{enabled:true,authenticated:!!user,user:user?{id:user.id,email:user.email,name:user.name,role:user.role}:null});return true;
  }
  if(path==='/auth/login'&&method==='GET'){
   this.prune();if(this.pending.size>=500)throw new AuthError(429,'로그인 요청이 많습니다. 잠시 후 다시 시도하세요.');
   const state=opaque(),binding=opaque(),verifier=opaque(),nonce=opaque();
   this.pending.set(state,{binding:hash(binding),verifier,nonce,returnTo:safeReturnTo(url.searchParams.get('returnTo'),this.config.origin),endsAt:this.now()+600000});
   const target=new URL('/oauth2/authorize',this.config.domain);
   target.search=new URLSearchParams({response_type:'code',client_id:this.config.clientId,redirect_uri:this.config.origin+'/auth/callback',scope:'openid email profile',lang:'ko',state,nonce,code_challenge:hash(verifier),code_challenge_method:'S256'}).toString();
   this.setCookie(res,this.loginCookie,binding,600);redirect(res,target.href);return true;
  }
  if(path==='/auth/callback'&&method==='GET'){
   const state=url.searchParams.get('state')||'',pending=this.pending.get(state);this.pending.delete(state);
   const binding=cookie(req,this.loginCookie);this.setCookie(res,this.loginCookie,'',0);
   try{
    if(!pending||pending.endsAt<=this.now()||!tokenPattern.test(binding)||!equal(hash(binding),pending.binding)||url.searchParams.has('error'))throw new AuthError(401,'로그인 요청이 만료되었습니다.');
    const code=url.searchParams.get('code');if(!code||code.length>8192)throw new AuthError(401,'인증 코드를 확인할 수 없습니다.');
    const value=await this.tokenRequest(new URLSearchParams({grant_type:'authorization_code',client_id:this.config.clientId,redirect_uri:this.config.origin+'/auth/callback',code,code_verifier:pending.verifier}));
    if(typeof value.id_token!=='string'||typeof value.refresh_token!=='string'||value.refresh_token.length>32768)throw new AuthError(401,'인증 응답을 확인할 수 없습니다.');
    const [access,id]=await Promise.all([this.verifyAccess(value.access_token),this.verifyId(value.id_token)]);
    if(access.sub!==id.sub||id.nonce!==pending.nonce||id.email_verified!==true||typeof id.email!=='string'||id.email.length>320||!id.email.includes('@'))throw new AuthError(401,'이메일 인증이 완료된 계정으로 로그인하세요.');
    const email=id.email.trim().toLowerCase();
    const user=await this.users.provision({provider:'cognito:'+this.config.userPoolId,subject:access.sub,email,name:typeof id.name==='string'&&id.name.trim()?id.name.trim().slice(0,200):email.split('@')[0]});
    if(user.status!=='active')throw new AuthError(403,'이 계정은 서비스 이용이 중지되었습니다.');
    this.prune();if(this.sessions.size>=10000)throw new AuthError(503,'로그인 사용자가 많습니다. 잠시 후 다시 시도하세요.');
    const old=cookie(req,this.sessionCookie);if(tokenPattern.test(old))this.sessions.delete(hash(old));
    const sessionToken=opaque();this.sessions.set(hash(sessionToken),{userId:user.id,subject:access.sub,access:value.access_token,refresh:value.refresh_token,expiresAt:access.exp*1000,endsAt:this.now()+this.config.sessionSeconds*1000});
    this.setCookie(res,this.sessionCookie,sessionToken,this.config.sessionSeconds);redirect(res,pending.returnTo);
   }catch(error){
    // Never include authorization codes, tokens, Cognito responses or DB errors in URLs/logs.
    const reason=error instanceof AuthError&&error.status===403?'account_disabled':'login_failed';redirect(res,'/?auth_error='+reason);
   }
   return true;
  }
  if(path==='/api/auth/logout'&&method==='POST'){
   const raw=cookie(req,this.sessionCookie),key=hash(raw),session=this.sessions.get(key);this.sessions.delete(key);this.setCookie(res,this.sessionCookie,'',0);
   // The local session is invalidated immediately, including copied browser cookies.
   if(session)await this.fetch(this.config.domain+'/oauth2/revoke',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:this.config.clientId,token:session.refresh}).toString(),redirect:'error',signal:AbortSignal.timeout(5000)}).catch(()=>{});
   const target=new URL('/logout',this.config.domain);target.search=new URLSearchParams({client_id:this.config.clientId,logout_uri:this.config.origin+'/'}).toString();authJson(res,200,{logoutUrl:target.href});return true;
  }
  if(path.startsWith('/auth/')){authJson(res,405,{error:'지원하지 않는 로그인 요청입니다.'});return true;}
  return false;
 }
 close(){this.sessions.clear();this.pending.clear();}
}
