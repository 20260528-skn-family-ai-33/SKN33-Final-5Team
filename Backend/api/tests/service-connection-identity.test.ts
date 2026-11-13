import test from 'node:test';
import assert from 'node:assert/strict';
import {ServiceConnectionIdentity} from '../src/service-connection-identity';
import type {CredentialSecret} from '../src/contracts';

const credential=(id:string,service:string,data:Record<string,unknown>={},updatedAt='2026-01-01T00:00:00.000Z'):CredentialSecret=>({
 id,name:service,provider:service==='slack'?'slack':'bearer',service,authType:'oauth2',createdAt:updatedAt,updatedAt,data,
});
const mock=(fn:(url:string,init:RequestInit)=>Response|Promise<Response>)=>((url:any,init:any)=>fn(String(url),init)) as typeof fetch;

test('stored provider identity is shown without another provider request',async()=>{
 const identity=new ServiceConnectionIdentity({fetch:mock(()=>{throw new Error('unexpected request');})});
 assert.equal(await identity.label(credential('sheets','googleSheets',{accountLabel:'owner@example.com'})),'owner@example.com');
 assert.equal(await identity.label(credential('slack','slack',{accountLabel:'SKN33'})),'SKN33');
 assert.equal(await identity.label(credential('old-sheets','googleSheets',{token:'private-access'})),undefined);
});

test('legacy Slack workspace identity is cached and invalidated after reconnect',async()=>{
 let now=1000,calls=0;
 const identity=new ServiceConnectionIdentity({now:()=>now,fetch:mock((url,init)=>{
  calls++;
  assert.equal(url,'https://slack.com/api/auth.test');
  assert.equal(new Headers(init.headers).get('Authorization'),'Bearer private-slack-token');
  assert.equal(init.redirect,'error');
  return Response.json({ok:true,team:calls===1?'Original workspace':'New workspace',user:'bot'});
 })});
 const old=credential('slack','slack',{token:'private-slack-token'});
 assert.deepEqual(await Promise.all([identity.label(old),identity.label(old)]),['Original workspace','Original workspace']);
 assert.equal(calls,1);
 assert.equal(await identity.label(old),'Original workspace');assert.equal(calls,1);
 const reconnected={...old,updatedAt:'2026-01-02T00:00:00.000Z'};
 assert.equal(await identity.label(reconnected),'New workspace');assert.equal(calls,2);
 now+=4*60*1000;
 assert.equal(await identity.label(reconnected),'New workspace');assert.equal(calls,3);
});

test('Slack identity lookup failure leaves the account unknown and keeps credentials private',async()=>{
 let calls=0;
 const identity=new ServiceConnectionIdentity({fetch:mock(()=>{calls++;throw new Error('private-slack-token');})});
 const account=credential('slack','slack',{token:'private-slack-token'});
 assert.equal(await identity.label(account),undefined);
 assert.equal(await identity.label(account),undefined);
 assert.equal(calls,1);
});
