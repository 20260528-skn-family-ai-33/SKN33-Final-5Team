const SLACK_CHANNELS_URL='https://slack.com/api/users.conversations';
const PAGE_SIZE=200;
const MAX_CHANNELS=500;
const MAX_PAGES=10;
const MAX_RESPONSE_BYTES=2_000_000;

export type SlackChannel={id:string;name:string;isPrivate:boolean};
export type SlackChannelList={channels:SlackChannel[];truncated:boolean};

export class SlackChannelsError extends Error {
 constructor(message:string,public readonly code:string,public readonly status=409){super(message);}
}

async function readResponse(response:Response):Promise<any> {
 const reader=response.body?.getReader();
 if(!reader)throw new SlackChannelsError('Slack 채널 응답을 읽지 못했어요. 잠시 후 다시 시도해주세요.','slack_unavailable',503);
 const chunks:Uint8Array[]=[];let size=0;
 try{
  while(true){
   const {done,value}=await reader.read();
   if(done)break;
   size+=value.byteLength;
   if(size>MAX_RESPONSE_BYTES)throw new Error('Slack response too large');
   chunks.push(value);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
 }catch{
  try{await reader.cancel();}catch{}
  throw new SlackChannelsError('Slack 채널 응답을 읽지 못했어요. 잠시 후 다시 시도해주세요.','slack_unavailable',503);
 }finally{reader.releaseLock();}
}

/** Only conversations that the connected bot belongs to can be selected for chat:write. */
export async function listSlackChannels(token:string,request:typeof fetch=fetch):Promise<SlackChannelList> {
 const channels:SlackChannel[]=[];
 const seenIds=new Set<string>(),seenCursors=new Set<string>();
 let cursor='',pages=0;
 while(pages<MAX_PAGES&&channels.length<MAX_CHANNELS){
  const url=new URL(SLACK_CHANNELS_URL);
  url.searchParams.set('types','public_channel,private_channel');
  url.searchParams.set('exclude_archived','true');
  url.searchParams.set('limit',String(PAGE_SIZE));
  if(cursor)url.searchParams.set('cursor',cursor);
  let response:Response;
  try{response=await request(url,{headers:{Authorization:`Bearer ${token}`,Accept:'application/json'},redirect:'error',credentials:'omit',signal:AbortSignal.timeout(10000)});}
  catch{throw new SlackChannelsError('Slack 채널 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요.','slack_unavailable',503);}
  if(response.status===429)throw new SlackChannelsError('Slack 요청이 많아요. 잠시 후 다시 시도해주세요.','slack_rate_limited',503);
  const data=await readResponse(response);
  if(data?.ok!==true){
   if(['missing_scope','invalid_types'].includes(data?.error))throw new SlackChannelsError('Slack 채널 목록 권한을 추가하려면 Slack 계정을 다시 연결해주세요.','slack_scope_required');
   if(['invalid_auth','token_expired','token_revoked','account_inactive','not_authed'].includes(data?.error))throw new SlackChannelsError('Slack 연결이 만료됐어요. 계정을 다시 연결해주세요.','slack_reconnect_required');
   if(data?.error==='ratelimited')throw new SlackChannelsError('Slack 요청이 많아요. 잠시 후 다시 시도해주세요.','slack_rate_limited',503);
   throw new SlackChannelsError('Slack 채널 목록을 불러오지 못했어요. 연결 상태를 확인해주세요.','slack_unavailable',503);
  }
  if(!response.ok||!Array.isArray(data.channels))throw new SlackChannelsError('Slack 채널 응답을 확인하지 못했어요. 잠시 후 다시 시도해주세요.','slack_unavailable',503);
  pages++;
  let pageTruncated=false;
  for(const [index,item] of data.channels.entries()){
   if(!item||typeof item.id!=='string'||!item.id||item.id.length>128||/[\u0000-\u001f\u007f]/.test(item.id)||typeof item.name!=='string'||!item.name||item.name.length>200||/[\u0000-\u001f\u007f]/.test(item.name)||item.is_archived===true||seenIds.has(item.id))continue;
   seenIds.add(item.id);
   channels.push({id:item.id,name:item.name,isPrivate:item.is_private===true});
   if(channels.length===MAX_CHANNELS){pageTruncated=index<data.channels.length-1;break;}
  }
  const next=data.response_metadata?.next_cursor;
  if(typeof next!=='string'||!next)return {channels:channels.sort((a,b)=>a.name.localeCompare(b.name)),truncated:pageTruncated};
  if(next.length>1024||seenCursors.has(next))throw new SlackChannelsError('Slack 채널 목록을 계속 불러오지 못했어요. 잠시 후 다시 시도해주세요.','slack_unavailable',503);
  seenCursors.add(next);cursor=next;
 }
 return {channels:channels.sort((a,b)=>a.name.localeCompare(b.name)),truncated:!!cursor};
}
