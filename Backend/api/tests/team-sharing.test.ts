import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import childProcess from 'node:child_process';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {syncBuiltinESMExports} from 'node:module';
import {request as httpRequest} from 'node:http';
import {TeamService} from '../src/team-service';
import {createApplication} from '../src/app-server';
import type {AuthConfig} from '../src/auth-config';
import type {AuthUser,AuthUsers} from '../src/auth-users';
import {AuthError,type CognitoAuth} from '../src/cognito-auth';

function directoryUsers(){
 const accounts=new Map<string,AuthUser>();
 const add=(email:string,name=email)=>{const user:AuthUser={id:randomUUID(),email,name,role:'member',status:'active'};accounts.set(user.id,user);return user;};
 const users:AuthUsers={async provision(){throw new Error('not used');},async get(id){return accounts.get(id);},async close(){}};
 return {users,add,accounts};
}

test('team memberships and in-app invitations enforce recipient, expiry, cancellation and last-admin rules',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-team-service-')),{users,add}=directoryUsers();
 const alice=add('alice@example.com','앨리스'),bob=add('bob@example.com','밥'),eve=add('eve@example.com','이브');
 let now=Date.now();const teams=new TeamService(directory,users,undefined,()=>now);
 try{
  const team=await teams.create(alice,'  공통 자료  ');assert.equal(team.role,'admin');assert.equal(team.name,'공통 자료');
  assert.deepEqual(await teams.list(bob),[]);await assert.rejects(teams.requireTeam(bob,team.id),{status:404});
  const invite=await teams.invite(alice,team.id,' BOB@EXAMPLE.COM ','editor');
  assert.equal(invite.email,'bob@example.com');assert.equal((await teams.received(bob))[0].team_name,team.name);
  assert.deepEqual(await teams.received(eve),[]);
  await assert.rejects(teams.respond(eve,invite.id,true),{status:404});
  await assert.rejects(teams.invite(alice,team.id,bob.email,'viewer'),{status:409});
  const accepted=await teams.respond(bob,invite.id,true);assert.equal(accepted.team?.role,'editor');
  await assert.rejects(teams.respond(bob,invite.id,true),{status:409});
  await assert.rejects(teams.invite(bob,team.id,eve.email,'viewer'),{status:403});
  await assert.rejects(teams.changeMember(alice,team.id,alice.id,'editor'),{status:409});
  await assert.rejects(teams.changeMember(alice,team.id,alice.id),{status:409});
  await teams.changeMember(alice,team.id,bob.id,'admin');await teams.changeMember(alice,team.id,alice.id,'viewer');
  await assert.rejects(teams.requireTeam(alice,team.id,true),{status:403});
  await assert.rejects(teams.changeMember(alice,team.id,bob.id,'viewer'),{status:403});
  const declined=await teams.invite(bob,team.id,eve.email,'viewer');assert.deepEqual(await teams.respond(eve,declined.id,false),{declined:true});
  const cancelled=await teams.invite(bob,team.id,eve.email,'viewer');await teams.cancel(bob,team.id,cancelled.id);
  await assert.rejects(teams.respond(eve,cancelled.id,true),{status:409});
  const expired=await teams.invite(bob,team.id,eve.email,'viewer');now+=8*86400000;
  assert.deepEqual(await teams.received(eve),[]);await assert.rejects(teams.respond(eve,expired.id,true),{status:409});
  const replacement=await teams.invite(bob,team.id,eve.email,'viewer');assert.notEqual(replacement.id,expired.id);
  // A recipient can register after invitation creation; no mail or account lookup is needed.
  const lateInvite=await teams.invite(bob,team.id,'later@example.com','viewer'),later=add('LATER@example.com','나중 가입');
  assert.equal((await teams.received(later))[0].id,lateInvite.id);await teams.respond(later,lateInvite.id,true);
  assert.equal((await teams.list(later))[0].role,'viewer');
  const safe=await teams.members(bob,team.id);assert.equal(safe.members.length,3);
  assert.ok(safe.members.every(member=>Object.keys(member).sort().join(',')==='email,id,joined_at,name,role,user_id'));
  await teams.changeMember(bob,team.id,later.id);await assert.rejects(teams.requireTeam(later,team.id),{status:404});
  await assert.rejects(teams.invite(bob,team.id,eve.email,['viewer']),{status:400});
  await assert.rejects(teams.invite(bob,team.id,'wrong@address@example.com','viewer'),{status:400});
  await assert.rejects(teams.create(bob,'bad\nname'),{status:400});
 }finally{await teams.close();await rm(directory,{recursive:true,force:true});}
});

test('concurrent admin demotions keep at least one administrator',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-team-race-')),{users,add}=directoryUsers();
 const alice=add('alice@example.com'),bob=add('bob@example.com'),teams=new TeamService(directory,users);
 try{
  const team=await teams.create(alice,'보호');const invite=await teams.invite(alice,team.id,bob.email,'admin');await teams.respond(bob,invite.id,true);
  const results=await Promise.allSettled([teams.changeMember(alice,team.id,alice.id,'editor'),teams.changeMember(bob,team.id,bob.id,'editor')]);
  assert.equal(results.filter(result=>result.status==='fulfilled').length,1);
  assert.equal((await teams.members(bob,team.id)).members.filter(member=>member.role==='admin').length,1);
 }finally{await teams.close();await rm(directory,{recursive:true,force:true});}
});

test('team APIs require login, restrict roles and isolate library routing from workflow execution',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-team-api-')),previous={...process.env},{users,add}=directoryUsers();
 Object.assign(process.env,{EASYFLOW_DATA_DIR:directory,A4B_STORAGE:'sqlite',A4B_AGENT_PYTHON:process.execPath});
 delete process.env.EASYFLOW_PUBLIC_URL;
 const alice=add('alice@example.com'),bob=add('bob@example.com'),eve=add('eve@example.com');
 const config={mode:'cognito',origin:'http://127.0.0.1:3030',userStore:'sqlite'} as AuthConfig;
 const auth={config,users,async currentUser(req:any){return (await users.get(String(req.headers['x-test-user']||'')));},async handle(){return false;},close(){}} as unknown as CognitoAuth;
 const app=createApplication({port:3030,auth,users,restore:false});
 await new Promise<void>((resolve,reject)=>{app.server.once('error',reject);app.server.listen(0,'127.0.0.1',resolve);});
 const address=app.server.address();assert.ok(address&&typeof address!=='string');config.origin='http://127.0.0.1:'+address.port;
 const call=(path:string,user?:AuthUser,method='GET',body?:unknown)=>fetch(config.origin+path,{method,headers:{Origin:config.origin,...(user?{'x-test-user':user.id}:{}),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const workerPayloads:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){const payload=JSON.parse(input);workerPayloads.push(payload);
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:{space:{kind:'team'},folders:[],assets:[]}}));child.exitCode=0;child.emit('close');});done();}});return child;
 });syncBuiltinESMExports();
 try{
  assert.equal((await call('/api/teams')).status,401);
  const created=await call('/api/teams',alice,'POST',{name:'테스트 팀'});assert.equal(created.status,200);const {team}=await created.json() as any;
  const base='/api/teams/'+team.id;
  const invite=await call(base+'/invitations',alice,'POST',{email:bob.email,role:'viewer'});const invitation=(await invite.json() as any).invitation;
  assert.equal((await call('/api/team-invitations/'+invitation.id+'/accept',bob,'POST',{})).status,200);
  assert.equal((await call(base+'/members',eve)).status,404);
  assert.equal((await call(base+'/invitations',bob,'POST',{email:eve.email,role:'viewer'})).status,403);
  assert.equal((await call(base+'/members/'+alice.id,alice,'DELETE')).status,409);
  assert.equal((await call(base+'/members/'+bob.id,alice,'PATCH',{role:['editor']})).status,400);
  assert.equal((await call(base+'/invitations',alice,'POST',{email:eve.email,role:'viewer',owner_id:eve.id})).status,400);
  for(const action of ['test','scheduled_run','message','sessions','connection','nodes','report'])assert.equal((await call(base+'/library/'+action,bob)).status,404);
  assert.equal((await call(base+'/library/library_upload',bob,'POST',{fileName:'a.txt',data:'YQ=='})).status,403);
  assert.equal(workerPayloads.length,0);
  assert.equal((await call(base+'/library/library',bob)).status,200);assert.equal(workerPayloads.length,1);
  assert.equal(workerPayloads[0].action,'library');assert.equal(workerPayloads[0].directory,join(directory,'teams',team.id));
  assert.ok(!(await readdir(directory)).includes('users'),'team-only requests never initialize personal workflow runtimes');
  const original=app.teams!.requireTeam.bind(app.teams);let checks=0;
  t.mock.method(app.teams!,'requireTeam',async(user:AuthUser,id:string,write=false)=>{
   if(++checks===3)await app.teams!.changeMember(alice,id,bob.id);
   return original(user,id,write);
  });
  assert.equal((await call(base+'/library/library',bob)).status,404);assert.equal(workerPayloads.length,1,'membership removal before worker start prevents access');
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();await new Promise<void>(resolve=>app.server.close(()=>resolve()));await app.close();
  for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);
  await rm(directory,{recursive:true,force:true});
 }
});

test('auth-off service refuses team APIs even on localhost',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-team-noauth-')),previous=process.env.EASYFLOW_DATA_DIR;process.env.EASYFLOW_DATA_DIR=directory;
 const app=createApplication({port:3030,restore:false});
 await new Promise<void>(resolve=>app.server.listen(0,'127.0.0.1',resolve));const address=app.server.address();assert.ok(address&&typeof address!=='string');
 try{
  const status=await new Promise<number|undefined>((resolve,reject)=>{
   const request=httpRequest('http://127.0.0.1:'+address.port+'/api/teams',{headers:{Host:'127.0.0.1:3030'}},response=>{
    response.resume();response.once('end',()=>resolve(response.statusCode));
   });request.once('error',reject);request.end();
  });assert.equal(status,401);
 }
 finally{await new Promise<void>(resolve=>app.server.close(()=>resolve()));await app.close();if(previous===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previous;await rm(directory,{recursive:true,force:true});}
});
