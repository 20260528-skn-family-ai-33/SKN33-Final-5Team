import test from 'node:test';
import assert from 'node:assert/strict';
import {listSlackChannels,SlackChannelsError} from '../src/slack-channels';

test('Slack channel discovery stops at a bounded page and channel count',async()=>{
 let requests=0;
 const request=(async(input:URL,init:RequestInit)=>{
  requests++;
  assert.equal(input.searchParams.get('limit'),'200');
  assert.equal(input.searchParams.get('cursor'),requests===1?null:`page-${requests}`);
  assert.equal(new Headers(init.headers).get('Authorization'),'Bearer private-bot-token');
  assert.doesNotMatch(input.href,/private-bot-token/);
  const first=(requests-1)*200;
  const channels=Array.from({length:200},(_,index)=>({id:`C${first+index}`,name:`channel-${String(first+index).padStart(3,'0')}`,is_private:false}));
  return Response.json({ok:true,channels,response_metadata:{next_cursor:`page-${requests+1}`}});
 }) as typeof fetch;
 const result=await listSlackChannels('private-bot-token',request);
 assert.equal(requests,3);
 assert.equal(result.channels.length,500);
 assert.equal(result.truncated,true);
 assert.equal(result.channels[0].id,'C0');
 assert.equal(result.channels.at(-1)?.id,'C499');
});

test('Slack channel discovery gives actionable, redacted provider errors',async()=>{
 for(const [providerError,expectedCode] of [['missing_scope','slack_scope_required'],['invalid_types','slack_scope_required'],['invalid_auth','slack_reconnect_required'],['token_expired','slack_reconnect_required']] as const){
  const request=(async()=>Response.json({ok:false,error:providerError,diagnostic:'private-bot-token'})) as typeof fetch;
  await assert.rejects(listSlackChannels('private-bot-token',request),error=>{
   assert.ok(error instanceof SlackChannelsError);
   assert.equal(error.code,expectedCode);
   assert.equal(error.status,409);
   assert.doesNotMatch(error.message,/private-bot-token|diagnostic/);
   return true;
  });
 }
 const rateLimited=(async()=>Response.json({ok:false,error:'ratelimited'},{status:429})) as typeof fetch;
 await assert.rejects(listSlackChannels('private-bot-token',rateLimited),error=>error instanceof SlackChannelsError&&error.code==='slack_rate_limited'&&error.status===503);
});
