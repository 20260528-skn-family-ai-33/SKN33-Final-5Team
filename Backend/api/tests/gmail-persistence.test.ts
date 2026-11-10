import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {GMAIL_CONNECTION_ID,Store} from '../src/store';

test('saved Gmail connection survives workspace restart; disconnect remains persistent',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-gmail-restart-'));
 let store:Store|undefined;
 try{
  store=new Store(directory);
  store.saveGmailCredential({id:GMAIL_CONNECTION_ID,name:'Gmail',provider:'bearer',data:{
   service:'gmail',authType:'oauth2',token:'fixture-access',refreshToken:'fixture-refresh',expiresAt:1,
  }});
  const key=await readFile(join(directory,'credential.key'));
  store.close();store=undefined;
  store=new Store(directory);
  assert.equal(store.gmailConnected(),true);
  assert.equal(store.getGmailCredential().data.refreshToken,'fixture-refresh');
  assert.deepEqual(await readFile(join(directory,'credential.key')),key);
  assert.deepEqual(store.listCredentials(),[],'Gmail secrets remain restricted to its dedicated path');
  store.disconnectGmail();store.close();store=undefined;
  store=new Store(directory);
  assert.equal(store.gmailConnected(),false);
 }finally{store?.close();await rm(directory,{recursive:true,force:true});}
});

test('Gmail connection history persists with fixed event codes and no secrets',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-gmail-history-'));
 let store:Store|undefined;
 try{
  store=new Store(directory);store.recordGmailEvent('runtime_opened');
  store.recordGmailEvent('oauth_started');
  const connection={id:GMAIL_CONNECTION_ID,name:'private-address@example.com',provider:'bearer' as const,
   data:{service:'gmail',authType:'oauth2',token:'private-access-token',refreshToken:'private-refresh-token',clientSecret:'private-client-secret'}};
  store.saveGmailCredential(connection);
  store.saveGmailCredential({...connection,data:{...connection.data,token:'rotated-private-token'}},'refreshed');
  store.recordGmailEvent('token_refresh_failed');
  assert.equal(store.gmailConnected(),true,'a failed refresh does not remove a saved connection');
  store.disconnectGmail();store.close();store=undefined;
  store=new Store(directory);store.recordGmailEvent('runtime_opened');
  const db=new DatabaseSync(join(directory,'easyflow.sqlite'),{readOnly:true});
  try{
   const rows=db.prepare('SELECT event,connection_present,created_at,process_id FROM gmail_connection_events ORDER BY seq').all();
   assert.deepEqual(rows.map(row=>[row.event,row.connection_present]),[
    ['runtime_opened',0],['oauth_started',0],['connected',1],['refreshed',1],['token_refresh_failed',1],['disconnected',0],['runtime_opened',0],
   ]);
   assert.ok(rows.every(row=>Number.isFinite(Date.parse(String(row.created_at)))&&typeof row.process_id==='string'));
   assert.doesNotMatch(JSON.stringify(rows),/private-|example\.com|refreshToken|clientSecret/);
  }finally{db.close();}
  assert.throws(()=>store!.recordGmailEvent('private-access-token' as any));
 }finally{store?.close();await rm(directory,{recursive:true,force:true});}
});

test('a failed connection history write cannot leave a partially saved or deleted Gmail connection',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-gmail-history-atomic-'));
 const store=new Store(directory),db=new DatabaseSync(join(directory,'easyflow.sqlite'));
 const connection={id:GMAIL_CONNECTION_ID,name:'Gmail',provider:'bearer' as const,
  data:{service:'gmail',authType:'oauth2',token:'original-access',refreshToken:'original-refresh'}};
 try{
  db.exec("CREATE TRIGGER reject_gmail_history BEFORE INSERT ON gmail_connection_events BEGIN SELECT RAISE(ABORT,'history unavailable'); END");
  assert.throws(()=>store.saveGmailCredential(connection));
  assert.equal(store.gmailConnected(),false,'failed first save is rolled back');
  db.exec('DROP TRIGGER reject_gmail_history');store.saveGmailCredential(connection);
  db.exec("CREATE TRIGGER reject_gmail_history BEFORE INSERT ON gmail_connection_events BEGIN SELECT RAISE(ABORT,'history unavailable'); END");
  assert.throws(()=>store.saveGmailCredential({...connection,data:{...connection.data,token:'rotated-access'}},'refreshed'));
  assert.equal(store.getGmailCredential().data.token,'original-access');
  assert.throws(()=>store.disconnectGmail());
  assert.equal(store.gmailConnected(),true,'failed disconnect is rolled back');
  assert.equal(db.prepare('SELECT count(*) AS count FROM gmail_connection_events').get()!.count,1);
 }finally{db.close();store.close();await rm(directory,{recursive:true,force:true});}
});

test('Gmail connection history retains its latest bounded evidence',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-gmail-history-bound-'));
 const store=new Store(directory),db=new DatabaseSync(join(directory,'easyflow.sqlite'),{readOnly:true});
 try{
  store.recordGmailEvent('oauth_started');
  for(let index=0;index<510;index++)store.recordGmailEvent('runtime_opened');
  const rows=db.prepare('SELECT event FROM gmail_connection_events ORDER BY seq').all();
  assert.equal(rows.length,500);assert.ok(rows.every(row=>row.event==='runtime_opened'));
 }finally{db.close();store.close();await rm(directory,{recursive:true,force:true});}
});
