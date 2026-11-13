import type {CredentialSecret} from './contracts';

const SLACK_AUTH_TEST_URL='https://slack.com/api/auth.test';
const SLACK_LOOKUP_TTL_MS=3*60*1000;

function displayLabel(value:unknown):string|undefined {
 if(typeof value!=='string')return undefined;
 const label=value.trim();
 return label&&label.length<=320&&!/[\u0000-\u001f\u007f]/.test(label)?label:undefined;
}

/** Identity is derived from the credential itself, never from another Google connection. */
export class ServiceConnectionIdentity {
 private slackCache=new Map<string,{updatedAt:string;expiresAt:number;request:Promise<string|undefined>}>();
 constructor(private options:{fetch?:typeof fetch;now?:()=>number}={}){}

 async label(credential:CredentialSecret):Promise<string|undefined> {
  const stored=displayLabel(credential.data.accountLabel);
  if(stored)return stored;
  if(credential.service!=='slack'&&credential.provider!=='slack')return undefined;
  const token=credential.data.token||credential.data.apiKey;
  if(typeof token!=='string'||!token)return undefined;
  const now=(this.options.now||Date.now)();
  const cached=this.slackCache.get(credential.id);
  if(cached?.updatedAt===credential.updatedAt&&cached.expiresAt>now)return cached.request;
  const request=this.lookupSlack(token);
  this.slackCache.set(credential.id,{updatedAt:credential.updatedAt,expiresAt:now+SLACK_LOOKUP_TTL_MS,request});
  return request;
 }

 private async lookupSlack(token:string):Promise<string|undefined> {
  try {
   const response=await (this.options.fetch||fetch)(SLACK_AUTH_TEST_URL,{headers:{Authorization:`Bearer ${token}`,Accept:'application/json'},redirect:'error',credentials:'omit',signal:AbortSignal.timeout(4000)});
   if(!response.ok)return undefined;
   const data=await response.json() as {ok?:unknown;team?:unknown};
   return data?.ok===true?displayLabel(data.team):undefined;
  }catch{return undefined;}
 }
}
