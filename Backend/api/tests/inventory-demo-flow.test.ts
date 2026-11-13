import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executeFlow} from '../src/engine';
import {validateFlow,clone} from '../../../shared/workflow';
import type {CredentialSecret,RunOptions} from '../src/contracts';

const example=JSON.parse(readFileSync(new URL('../../../examples/inventory-sheets-slack.flow.json',import.meta.url),'utf8'));
const sheetId='1MU0Y8jy7Ot3sXjsj4x9Hdr2ne94CK2MhOPcoKi74GhE';
const account=(id:string,provider:'bearer'|'slack',data:Record<string,string>):CredentialSecret=>({id,name:id,provider,data,createdAt:'',updatedAt:''});
const accounts={
  sheets:account('sheets','bearer',{token:'fixture-sheets-token',service:'googleSheets'}),
  slack:account('slack','slack',{token:'fixture-slack-token'}),
};
function run(values:(string|number)[][]){
  const flow=validateFlow(clone(example));
  for(const node of flow.nodes)if(node.type==='easyflow.googleSheets'||node.type==='n8n-nodes-base.slack')node.credentialId=node.type==='easyflow.googleSheets'?'sheets':'slack';
  const calls:{url:string;body?:any}[]=[];
  const fetch=async (url:string,init:RequestInit)=>{
    const body=init.body?JSON.parse(String(init.body)):undefined;
    calls.push({url,body});
    if(url.includes('/values/%EC%9E%AC%EA%B3%A0!'))return new Response(JSON.stringify({range:'재고!A1:D1000',values}));
    if(url.includes(':append'))return new Response(JSON.stringify({updatedCells:12}));
    if(url==='https://slack.com/api/chat.postMessage')return new Response(JSON.stringify({ok:true,channel:'C0CH769U6BX',ts:'1'}));
    throw new Error('Unexpected URL: '+url);
  };
  const options:RunOptions={mode:'live',credential:id=>accounts[id as keyof typeof accounts],fetch:fetch as typeof globalThis.fetch};
  return executeFlow(flow,[{}],options).then(result=>({result,calls}));
}

test('inventory demo reads the source, appends only shortages and posts their summary to Slack',async()=>{
  const {result,calls}=await run([
    ['SKU','상품명','현재수량','발주기준'],
    ['KBD-001','키보드','20','30'],
    ['MSE-001','마우스','3','10'],
    ['MON-001','모니터','25','15'],
    ['HUB-001','USB 허브','9','10'],
  ]);
  assert.equal(result.status,'success',result.error);
  assert.equal(calls.length,3);
  assert.ok(calls[0].url.includes(sheetId));
  const append=calls.find(call=>call.url.includes(':append'))!;
  assert.deepEqual(append.body.values.map((row:any[])=>row.slice(1)),[
    ['KBD-001','키보드',20,30,10],
    ['MSE-001','마우스',3,10,7],
    ['HUB-001','USB 허브',9,10,1],
  ]);
  assert.match(calls.find(call=>call.url==='https://slack.com/api/chat.postMessage')!.body.text,/키보드.*마우스.*USB 허브/s);
  assert.doesNotMatch(JSON.stringify(calls),/모니터 \(MON-001\):/);
});

test('inventory demo skips external writes when no product is short',async()=>{
  const {result,calls}=await run([['SKU','상품명','현재수량','발주기준'],['MON-001','모니터','25','15']]);
  assert.equal(result.status,'success',result.error);
  assert.equal(calls.length,1);
});
