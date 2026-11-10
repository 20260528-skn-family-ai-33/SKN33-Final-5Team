import test from 'node:test';
import assert from 'node:assert/strict';
import {createBuilderScheduleRunner,createBuilderScheduleState,builderScheduleHealth} from '../src/agent-builder-scheduler';
import {WorkerBusyError} from '../src/worker-slots';
import {createCredentialRefresher,GMAIL_OAUTH} from '../src/oauth';
import type {CredentialSecret} from '../src/contracts';

test('builder schedule runner keeps polling out of a still-running execution',async()=>{
 let release:()=>void=()=>{};const barrier=new Promise<void>(r=>release=r);const actions:string[]=[];
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')await barrier;return {};
 };
 const runner=createBuilderScheduleRunner({} as any,worker);const running=runner.tick();
 while(!actions.includes('scheduled_run'))await new Promise(r=>setTimeout(r,1));
 await runner.tick();assert.equal(actions.filter(a=>a==='claim_due').length,1);
 release();await running;assert.equal(builderScheduleHealth().running,false);
});

test('schedule failure is recorded rather than reported as success',async()=>{
 const actions:string[]=[];
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')throw new Error('controlled failure');return {};
 };
 await createBuilderScheduleRunner({} as any,worker).tick();
 assert.ok(actions.includes('fail_job'));assert.ok(builderScheduleHealth().lastError);
});

test('a run that never got a worker slot is left for the lease to offer again',async()=>{
 const actions:string[]=[];const scheduleState=createBuilderScheduleState();
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')throw new WorkerBusyError('busy');return {};
 };
 await createBuilderScheduleRunner({} as any,worker,scheduleState).tick();
 assert.ok(actions.includes('scheduled_run'));assert.ok(!actions.includes('fail_job'));assert.match(scheduleState.lastError||'',/다시 시도/);
});

test('Gmail schedule refreshes its workspace token before injecting only the access token',async()=>{
 const actions:Record<string,any>[]=[],scheduleState=createBuilderScheduleState();
 let credential:CredentialSecret={id:'a4b-gmail',name:'Gmail',provider:'bearer',service:'gmail',createdAt:'2026-01-01',updatedAt:'2026-01-01',
  data:{authType:'oauth2',service:'gmail',...GMAIL_OAUTH,clientId:'fixture-client',clientSecret:'fixture-secret',accessToken:'expired-access',refreshToken:'fixture-refresh',expiresAt:1}};
 let refreshes=0;
 const refresh=createCredentialRefresher();
 const accessToken=async()=>{
  credential=await refresh(credential,updated=>{credential=updated;},{fetch:(async(input,init)=>{
   refreshes++;assert.equal(String(input),GMAIL_OAUTH.tokenUrl);
   const body=new URLSearchParams(String(init?.body));
   assert.equal(body.get('grant_type'),'refresh_token');assert.equal(body.get('refresh_token'),'fixture-refresh');
   return Response.json({access_token:'fresh-scoped-access',refresh_token:'rotated-refresh',expires_in:3600,scope:GMAIL_OAUTH.scope});
  }) as typeof fetch});
  return credential.data.accessToken as string;
 };
 const worker=async(payload:Record<string,any>)=>{
  actions.push(payload);
  if(payload.action==='claim_due')return {jobs:[{id:'gmail-job',lease_token:'owned-lease',
   agent_snapshot:{credential_id:'model-fixture',config:{workflow:{nodes:[{tool:'email.list',settings:{operation:'read_messages'}},{tool:'compose_answer',settings:{}}]}}}}]};
  return {};
 };
 const store={getCredential:(id:string)=>{assert.equal(id,'model-fixture');return {data:{apiKey:'model-access'}};}} as any;
 await createBuilderScheduleRunner(store,worker,scheduleState,accessToken).tick();
 const run=actions.find(payload=>payload.action==='scheduled_run');
 assert.deepEqual(run,{action:'scheduled_run',id:'gmail-job',lease_token:'owned-lease',api_key:'model-access',gmail_access_token:'fresh-scoped-access'});
 assert.equal(refreshes,1);assert.equal(credential.data.refreshToken,'rotated-refresh');
 assert.doesNotMatch(JSON.stringify(run),/expired-access|fixture-refresh|rotated-refresh|fixture-secret/);
 assert.equal(scheduleState.lastError,null);
});

test('Gmail schedules fail before starting an execution when the account is disconnected',async()=>{
 const actions:string[]=[],state=createBuilderScheduleState();
 const worker=async(payload:Record<string,unknown>)=>{
  actions.push(String(payload.action));
  if(payload.action==='claim_due')return {jobs:[{id:'gmail-job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[{tool:'email.list',settings:{operation:'read_messages'}}]}}}}]};
  return {};
 };
 await createBuilderScheduleRunner({} as any,worker,state,async()=>undefined).tick();
 assert.ok(actions.includes('fail_job'));assert.ok(!actions.includes('scheduled_run'));assert.ok(state.lastError);
});
