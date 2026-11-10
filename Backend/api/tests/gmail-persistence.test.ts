import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
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
