import {createHash,randomBytes} from 'node:crypto';
import {isIP} from 'node:net';
import {isLoopback} from './auth-config';
import type {CredentialInput,CredentialSecret} from './contracts';
import type {Json} from '../../../shared/workflow';

// Google: https://developers.google.com/identity/protocols/oauth2/native-app
// PKCE S256: https://datatracker.ietf.org/doc/html/rfc7636
export const GOOGLE_SHEETS_OAUTH={
 authorizationUrl:'https://accounts.google.com/o/oauth2/v2/auth',
 tokenUrl:'https://oauth2.googleapis.com/token',
 scope:'https://www.googleapis.com/auth/spreadsheets',
 readonlyScope:'https://www.googleapis.com/auth/spreadsheets.readonly',
 accountScope:'https://www.googleapis.com/auth/spreadsheets openid email',
} as const;
export const GMAIL_OAUTH={
 authorizationUrl:GOOGLE_SHEETS_OAUTH.authorizationUrl,
 tokenUrl:GOOGLE_SHEETS_OAUTH.tokenUrl,
 scope:'https://www.googleapis.com/auth/gmail.readonly',
} as const;
export const SLACK_OAUTH={authorizationUrl:'https://slack.com/oauth/v2/authorize',tokenUrl:'https://slack.com/api/oauth.v2.access',scope:'chat:write,channels:read,groups:read'} as const;
export const OAUTH_STATE_TTL_MS=10*60*1000;
const TOKEN_EXPIRY_BUFFER_MS=60*1000;
const MAX_PENDING=128;
const REQUEST_TIMEOUT_MS=15*1000;
const MAX_RESPONSE_BYTES=256*1024;
const MAX_TOKEN_LENGTH=32*1024;
const loginError='로그인 요청이 만료되었거나 이미 사용됐어요. 다시 연결해주세요.';
const exchangeError='로그인을 완료하지 못했어요. 앱의 클라이언트 정보, 반환 주소, 권한 설정을 확인하고 다시 연결해주세요.';
const refreshError='로그인 정보를 갱신하지 못했어요. 계정을 다시 연결하거나 네트워크 연결을 확인해주세요.';
const canceledError=()=>new Error('계정 연결 요청을 중지했어요.');

export type OAuthBeginInput={
 id?:string;name:string;service?:string;clientId:string;clientSecret?:string;
 authorizationUrl?:string;tokenUrl?:string;scope?:string;
 tokenAuthMethod?:'body'|'basic'|'none';
};
export type OAuthBeginResult={stateId:string;authorizationUrl:string;redirectUri:string;expiresAt:number};
export type OAuthRequestOptions={fetch?:typeof fetch;signal?:AbortSignal;now?:()=>number};
type OAuthConfig={clientId:string;clientSecret?:string;authorizationUrl:string;tokenUrl:string;scope:string;tokenAuthMethod:'body'|'basic'|'none';service?:string};
type Pending={input:OAuthBeginInput;config:OAuthConfig;authority:string;redirectUri:string;verifier:string;expiresAt:number};
type TokenResult={access_token:string;refresh_token?:string;expires_in?:number;scope?:string;accountLabel?:string};
const GOOGLE_USERINFO_URL='https://openidconnect.googleapis.com/v1/userinfo';
const GOOGLE_EMAIL_SCOPE='https://www.googleapis.com/auth/userinfo.email';

function accountLabel(value:unknown):string|undefined {
 if(typeof value!=='string')return undefined;
 const label=value.trim();
 return label&&label.length<=320&&!/[\u0000-\u001f\u007f]/.test(label)?label:undefined;
}

function validSheetsScope(scope:string):boolean {
 const values=scope.split(/\s+/),allowed=new Set<string>([GOOGLE_SHEETS_OAUTH.scope,GOOGLE_SHEETS_OAUTH.readonlyScope,'openid','email',GOOGLE_EMAIL_SCOPE]);
 const hasSheet=values.includes(GOOGLE_SHEETS_OAUTH.scope)||values.includes(GOOGLE_SHEETS_OAUTH.readonlyScope);
 const hasIdentity=values.some(value=>value==='openid'||value==='email'||value===GOOGLE_EMAIL_SCOPE);
 return hasSheet&&values.every(value=>allowed.has(value))&&(!hasIdentity||values.includes('openid')&&(values.includes('email')||values.includes(GOOGLE_EMAIL_SCOPE)));
}

async function googleAccountEmail(token:string,request:typeof fetch,signal?:AbortSignal):Promise<string|undefined> {
 try {
  const timeout=AbortSignal.timeout(5000);
  const response=await request(GOOGLE_USERINFO_URL,{headers:{Authorization:`Bearer ${token}`,Accept:'application/json'},redirect:'error',credentials:'omit',signal:signal?AbortSignal.any([timeout,signal]):timeout});
  if(!response.ok)return undefined;
  const profile=await response.json() as {email?:unknown;email_verified?:unknown};
  const email=accountLabel(profile?.email);
  return email&&email.includes('@')&&profile.email_verified===true?email:undefined;
 }catch{return undefined;}
}

/** Endpoints come only from the user's registered app configuration. Never follow redirects with secrets. */
export function validateOAuthEndpoint(value:unknown):string {
 const message='로그인과 토큰 주소에는 공개 서비스의 HTTPS 주소를 입력해주세요. 로컬 주소, IP 주소, 인증 정보, 쿼리와 # 표시는 사용할 수 없어요.';
 if(typeof value!=='string'||value.length>2048)throw new Error(message);
 let url:URL;try{url=new URL(value);}catch{throw new Error(message);}
 const hostname=url.hostname.replace(/\.$/,'').toLowerCase();
 if(url.protocol!=='https:'||url.username||url.password||url.hash||url.search||!hostname.includes('.')||isIP(hostname.replace(/^\[|\]$/g,''))||/(^|\.)(localhost|local|internal|lan|home|onion)$/.test(hostname)||!hostname.split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)))throw new Error(message);
 return url.toString();
}

function string(value:unknown,max:number,message:string,required=false):string|undefined {
 if(value===undefined||value===''){if(required)throw new Error(message);return undefined;}
 if(typeof value!=='string'||value.length>max||/[\u0000-\u001f\u007f]/.test(value)||required&&!value.trim())throw new Error(message);
 return value;
}
function config(input:OAuthBeginInput,allowLegacySlackScope=false):OAuthConfig {
 const clientId=string(input.clientId,2048,'등록한 앱의 클라이언트 ID를 입력해주세요.',true)!.trim();
 const clientSecret=string(input.clientSecret,MAX_TOKEN_LENGTH,'클라이언트 비밀 값을 확인해주세요.');
 const service=string(input.service,120,'연결할 서비스 종류를 확인해주세요.');
 const google=service==='googleSheets'?GOOGLE_SHEETS_OAUTH:service==='gmail'?GMAIL_OAUTH:service==='slack'?SLACK_OAUTH:undefined;
 const authorizationUrl=validateOAuthEndpoint(input.authorizationUrl||google?.authorizationUrl);
 const tokenUrl=validateOAuthEndpoint(input.tokenUrl||google?.tokenUrl);
 const scope=string(input.scope,4096,'요청할 권한 범위를 확인해주세요.')?.trim()||(google?.scope||'');
 const tokenAuthMethod=input.tokenAuthMethod||'body';
 if(!['body','basic','none'].includes(tokenAuthMethod))throw new Error('클라이언트 인증 방법을 확인해주세요.');
 if(tokenAuthMethod==='basic'&&!clientSecret)throw new Error('Basic 클라이언트 인증에는 클라이언트 비밀 값이 필요해요.');
 if(service==='googleSheets'&&(authorizationUrl!==GOOGLE_SHEETS_OAUTH.authorizationUrl||tokenUrl!==GOOGLE_SHEETS_OAUTH.tokenUrl||!validSheetsScope(scope)||tokenAuthMethod!=='body'))throw new Error('Google Sheets 연결에는 공식 로그인·토큰 주소와 Sheets 권한을 사용해주세요.');
 if(service==='gmail'&&(authorizationUrl!==GMAIL_OAUTH.authorizationUrl||tokenUrl!==GMAIL_OAUTH.tokenUrl||scope!==GMAIL_OAUTH.scope||tokenAuthMethod!=='body'||!clientSecret))throw new Error('Gmail 연결에는 등록된 Google 앱과 Gmail 읽기 권한을 사용해주세요.');
 // Slack can return granted scopes in a different order and add scopes after a
 // later installation. Only token refresh may accept that returned scope set.
 const validSlackScope=scope===SLACK_OAUTH.scope||allowLegacySlackScope&&scope.split(/[\s,]+/).includes('chat:write');
 if(service==='slack'&&(authorizationUrl!==SLACK_OAUTH.authorizationUrl||tokenUrl!==SLACK_OAUTH.tokenUrl||!validSlackScope||tokenAuthMethod!=='basic'||!clientSecret))throw new Error('Slack 연결에는 등록된 앱과 메시지 전송·채널 조회 권한을 사용해주세요.');
 return {clientId,...(clientSecret?{clientSecret}:{}),authorizationUrl,tokenUrl,scope,tokenAuthMethod,...(service?{service}:{})};
}

function authenticate(body:URLSearchParams,headers:Headers,config:Pick<OAuthConfig,'clientId'|'clientSecret'|'tokenAuthMethod'>):void {
 if(config.tokenAuthMethod==='basic'){
  const encode=(value:string)=>new URLSearchParams({value}).toString().slice(6);
  headers.set('Authorization','Basic '+Buffer.from(encode(config.clientId)+':'+encode(config.clientSecret!)).toString('base64'));
 } else {
  body.set('client_id',config.clientId);
  if(config.tokenAuthMethod!=='none'&&config.clientSecret)body.set('client_secret',config.clientSecret);
 }
}

async function readTokenResponse(response:Response,service?:string):Promise<TokenResult> {
 if(!response.ok||response.status<200||response.status>=300||!response.body)throw new Error();
 const reader=response.body.getReader(),chunks:Uint8Array[]=[];let bytes=0;
 try{while(true){const chunk=await reader.read();if(chunk.done)break;bytes+=chunk.value.byteLength;if(bytes>MAX_RESPONSE_BYTES){await reader.cancel();throw new Error();}chunks.push(chunk.value);}}
 finally{reader.releaseLock();}
 const data=JSON.parse(Buffer.concat(chunks).toString('utf8')) as Json;
 if(!data||typeof data!=='object'||Array.isArray(data)||data.error||service==='slack'&&data.ok!==true||typeof data.access_token!=='string'||!data.access_token||data.access_token.length>MAX_TOKEN_LENGTH||/[\u0000-\u001f\u007f]/.test(data.access_token)||data.token_type!==undefined&&(typeof data.token_type!=='string'||data.token_type.toLowerCase()!==(service==='slack'?'bot':'bearer')))throw new Error();
 const result:TokenResult={access_token:data.access_token};
 if(data.refresh_token!==undefined){if(typeof data.refresh_token!=='string'||!data.refresh_token||data.refresh_token.length>MAX_TOKEN_LENGTH||/[\u0000-\u001f\u007f]/.test(data.refresh_token))throw new Error();result.refresh_token=data.refresh_token;}
 if(data.expires_in!==undefined){if(!['number','string'].includes(typeof data.expires_in)||!Number.isFinite(Number(data.expires_in))||Number(data.expires_in)<=0||Number(data.expires_in)>365*86400)throw new Error();result.expires_in=Number(data.expires_in);}
 if(typeof data.scope==='string'&&data.scope.length<=4096&&!/[\u0000-\u001f\u007f]/.test(data.scope))result.scope=data.scope;
 if(service==='slack'&&data.team&&typeof data.team==='object'&&!Array.isArray(data.team))result.accountLabel=accountLabel(data.team.name);
 return result;
}

async function requestToken(tokenUrl:string,body:URLSearchParams,oauth:Pick<OAuthConfig,'clientId'|'clientSecret'|'tokenAuthMethod'|'service'>,options:OAuthRequestOptions,message:string):Promise<TokenResult> {
 if(options.signal?.aborted)throw canceledError();
 const headers=new Headers({'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'});
 authenticate(body,headers,oauth);
 const timeout=AbortSignal.timeout(REQUEST_TIMEOUT_MS);
 const signal=options.signal?AbortSignal.any([timeout,options.signal]):timeout;
 try {
  const response=await (options.fetch||fetch)(tokenUrl,{method:'POST',headers,body:body.toString(),redirect:'error',credentials:'omit',signal});
  const tokens=await readTokenResponse(response,oauth.service);
  if(options.signal?.aborted)throw canceledError();
  return tokens;
 } catch {
  if(options.signal?.aborted)throw canceledError();
  throw new Error(message);
 }
}

function tokenData(base:Json,tokens:TokenResult,now:number):Json {
 const data:Json={...base,token:tokens.access_token,accessToken:tokens.access_token};
 if(tokens.refresh_token)data.refreshToken=tokens.refresh_token;
 if(tokens.expires_in!==undefined)data.expiresAt=now+tokens.expires_in*1000;
 else delete data.expiresAt;
 if(tokens.scope!==undefined)data.scope=tokens.scope;
 if(tokens.accountLabel!==undefined)data.accountLabel=tokens.accountLabel;
 return data;
}

export class OAuthManager {
 private pending=new Map<string,Pending>();
 private port:number;
 private request:typeof fetch;
 private now:()=>number;
 private publicOrigin?:URL;
 constructor(options:{port:number;publicOrigin?:string;fetch?:typeof fetch;now?:()=>number}) {
  if(!Number.isInteger(options.port)||options.port<1||options.port>65535)throw new Error('로컬 프로그램의 포트 번호를 확인해주세요.');
  if(options.publicOrigin!==undefined){
   const origin=new URL(options.publicOrigin);
   if(origin.username||origin.password||origin.search||origin.hash||origin.pathname!=='/'||origin.protocol!=='https:'&&!(origin.protocol==='http:'&&isLoopback(origin.hostname)))throw new Error('OAuth 서비스 주소는 HTTPS 출처여야 합니다. HTTP는 로컬 개발에서만 사용할 수 있습니다.');
   this.publicOrigin=origin;
  }
  this.port=options.port;this.request=options.fetch||fetch;this.now=options.now||Date.now;
 }
 private checkAuthority(authority:string):void {
  if(this.publicOrigin){
   if(authority!==this.publicOrigin.host)throw new Error('등록된 서비스 주소에서 계정을 연결해주세요.');
   return;
  }
  if(![`127.0.0.1:${this.port}`,`localhost:${this.port}`,`[::1]:${this.port}`].includes(authority))throw new Error('로그인은 이 로컬 프로그램에서 시작해주세요.');
 }
 begin(input:OAuthBeginInput,initiatingAuthority:string):OAuthBeginResult {
  this.checkAuthority(initiatingAuthority);
  if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('앱의 로그인 정보를 입력해주세요.');
  const name=string(input.name,120,'연결 이름을 입력해주세요.',true)!.trim();
  const id=string(input.id,200,'연결 ID를 확인해주세요.');
  const oauth=config(input);
  const now=this.now();for(const [state,pending] of this.pending)if(pending.expiresAt<=now)this.pending.delete(state);
  if(this.pending.size>=MAX_PENDING)throw new Error('진행 중인 로그인 요청이 많아요. 잠시 후 다시 연결해주세요.');
  const stateId=randomBytes(32).toString('base64url'),verifier=randomBytes(32).toString('base64url');
  const redirectUri=`${this.publicOrigin?.origin||`http://${initiatingAuthority}`}/api/oauth/callback`,expiresAt=now+OAUTH_STATE_TTL_MS;
  this.pending.set(stateId,{input:{...oauth,name,...(id?{id}:{})},config:oauth,authority:initiatingAuthority,redirectUri,verifier,expiresAt});
  const authorization=new URL(oauth.authorizationUrl);
  authorization.searchParams.set('client_id',oauth.clientId);authorization.searchParams.set('response_type','code');authorization.searchParams.set('redirect_uri',redirectUri);authorization.searchParams.set('state',stateId);
  if(oauth.service!=='slack'){authorization.searchParams.set('code_challenge',createHash('sha256').update(verifier).digest('base64url'));authorization.searchParams.set('code_challenge_method','S256');}
  if(oauth.scope)authorization.searchParams.set('scope',oauth.scope);
  if(oauth.service==='googleSheets'||oauth.service==='gmail'){authorization.searchParams.set('access_type','offline');authorization.searchParams.set('prompt','consent');}
  return {stateId,authorizationUrl:authorization.toString(),redirectUri,expiresAt};
 }
 cancel(id:string):void {
  for(const [state,pending] of this.pending)if(pending.input.id===id)this.pending.delete(state);
 }
 /** Metadata only, before completion consumes the one-time state. */
 pendingService(state?:string):string|null|undefined {
  if(typeof state!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(state))return undefined;
  const pending=this.pending.get(state);
  // A known generic OAuth request is distinct from an unknown/consumed state.
  return pending?(pending.config.service??null):undefined;
 }
 /** The server consumes this result internally and persists it encrypted; never send this object to a browser. */
 async complete(query:{state?:string;code?:string;error?:string},callbackAuthority:string,options:{signal?:AbortSignal}={}):Promise<CredentialInput> {
  this.checkAuthority(callbackAuthority);
  if(!query||typeof query.state!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(query.state))throw new Error(loginError);
  const pending=this.pending.get(query.state);
  if(!pending)throw new Error(loginError);
  if(pending.authority!==callbackAuthority)throw new Error('로그인을 시작한 로컬 주소로 돌아와야 해요. 처음 열었던 주소에서 다시 연결해주세요.');
  this.pending.delete(query.state);
  if(pending.expiresAt<=this.now())throw new Error(loginError);
  if(query.error)throw new Error('로그인 권한 승인이 취소됐어요. 다시 연결해주세요.');
  const code=string(query.code,MAX_TOKEN_LENGTH,exchangeError,true)!;
  const body=new URLSearchParams({grant_type:'authorization_code',code,redirect_uri:pending.redirectUri,...(pending.config.service==='slack'?{}:{code_verifier:pending.verifier})});
  const tokens=await requestToken(pending.config.tokenUrl,body,pending.config,{fetch:this.request,signal:options.signal},exchangeError);
  const data=tokenData({authType:'oauth2',...pending.config,redirectUri:pending.redirectUri},tokens,this.now());
  if(pending.config.service==='googleSheets'&&pending.config.scope.split(/\s+/).some(value=>value==='email'||value===GOOGLE_EMAIL_SCOPE)){
   const email=await googleAccountEmail(tokens.access_token,this.request,options.signal);
   if(options.signal?.aborted)throw canceledError();
   if(email)data.accountLabel=email;
  }
  return {name:pending.input.name,provider:pending.config.service==='slack'?'slack':'bearer',...(pending.input.id?{id:pending.input.id}:{}),data};
 }
}

function withAbort<T>(promise:Promise<T>,signal?:AbortSignal):Promise<T> {
 if(!signal)return promise;
 if(signal.aborted)return Promise.reject(canceledError());
 return new Promise((resolve,reject)=>{
  const abort=()=>{signal.removeEventListener('abort',abort);reject(canceledError());};
  signal.addEventListener('abort',abort,{once:true});
  promise.then(value=>{signal.removeEventListener('abort',abort);resolve(value);},error=>{signal.removeEventListener('abort',abort);reject(error);});
 });
}

/** Create once per workspace: connection IDs are unique only inside that workspace. */
export function createCredentialRefresher(){
 const refreshLocks=new Map<string,Promise<CredentialSecret>>();
 return async function getUsableCredential(credential:CredentialSecret,saveCallback:(updated:CredentialSecret)=>unknown|Promise<unknown>,options:OAuthRequestOptions={}):Promise<CredentialSecret> {
  if(options.signal?.aborted)throw canceledError();
  if(credential.data.authType!=='oauth2')return credential;
  if(credential.provider!=='bearer'&&!(credential.provider==='slack'&&credential.service==='slack'))throw new Error('OAuth 로그인 연결 계정을 확인해주세요.');
  const data=credential.data;
  const accessToken=typeof data.accessToken==='string'&&data.accessToken?data.accessToken:typeof data.token==='string'?data.token:'';
  const expiresAt=data.expiresAt===undefined?undefined:typeof data.expiresAt==='number'?data.expiresAt:typeof data.expiresAt==='string'?Date.parse(data.expiresAt):NaN;
  if(expiresAt!==undefined&&!Number.isFinite(expiresAt))throw new Error('로그인 만료 시각을 확인할 수 없어요. 계정을 다시 연결해주세요.');
  if(accessToken&&(expiresAt===undefined||expiresAt>(options.now||Date.now)()+TOKEN_EXPIRY_BUFFER_MS))return {...credential,data:{...data,token:accessToken,accessToken}};
  if(typeof data.refreshToken!=='string'||!data.refreshToken)throw new Error('로그인이 만료됐어요. 계정을 다시 연결해주세요.');
  if(typeof credential.id!=='string'||!credential.id)throw new Error('저장된 연결 계정을 다시 선택해주세요.');
  const existing=refreshLocks.get(credential.id);if(existing)return withAbort(existing,options.signal);
  const refresh=(async()=>{
   // A chat:write-only Slack install can keep posting while its owner reconnects
   // to grant channel listing. New installation requests still require all scopes.
   const oauth=config({name:credential.name,clientId:data.clientId,clientSecret:data.clientSecret,service:credential.service||data.service,authorizationUrl:data.authorizationUrl,tokenUrl:data.tokenUrl,scope:data.scope,tokenAuthMethod:data.tokenAuthMethod},true);
   const body=new URLSearchParams({grant_type:'refresh_token',refresh_token:data.refreshToken});
   const tokens=await requestToken(oauth.tokenUrl,body,oauth,options,refreshError);
   const updated:CredentialSecret={...credential,data:tokenData({...data,...oauth},tokens,(options.now||Date.now)()),updatedAt:new Date((options.now||Date.now)()).toISOString()};
   if(options.signal?.aborted)throw canceledError();
   try{await saveCallback(updated);}catch{throw new Error('갱신한 로그인 정보를 저장하지 못했어요. 계정을 다시 연결해주세요.');}
   return updated;
  })();
  refreshLocks.set(credential.id,refresh);
  void refresh.finally(()=>{if(refreshLocks.get(credential.id)===refresh)refreshLocks.delete(credential.id);}).catch(()=>{});
  return withAbort(refresh,options.signal);
 };
}
