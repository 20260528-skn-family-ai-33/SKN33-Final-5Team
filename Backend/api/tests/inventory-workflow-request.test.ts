import test from 'node:test';
import assert from 'node:assert/strict';
import {inventoryWorkflowFromRequest} from '../src/inventory-workflow-request';
import {runCode} from '../src/sandbox';
import {executeFlow} from '../src/engine';
import type {CredentialPublic} from '../../../shared/api-contracts';
import type {CredentialSecret,RunOptions} from '../src/contracts';

const accounts:CredentialPublic[]=[
 {id:'sheets-credential',name:'Sheets',provider:'bearer',service:'googleSheets',createdAt:'',updatedAt:''},
 {id:'slack-credential',name:'Slack',provider:'slack',service:'slack',createdAt:'',updatedAt:''},
];
const request='매일 오전 9시(Asia/Seoul)에 Google Sheets 스프레드시트 1MU0Y8jy7Ot3sXjsj4x9Hdr2ne94CK2MhOPcoKi74GhE의 재고!A1:D1000을 읽어줘. 첫 행은 헤더이고 A열 SKU, B열 상품명, C열 현재 수량, D열 기준 수량이야. 현재 수량이 기준 수량보다 적은 행만 골라서 같은 시트의 부족재고_기록!A:F에 날짜, SKU, 상품명, 현재 수량, 기준 수량, 부족 수량을 추가하고, Slack 채널 ID C0CH769U6BX에 부족 상품 목록을 메시지로 보내는 워크플로우를 만들어줘. Google Sheets와 Slack은 연결된 계정을 사용해.';

test('specific inventory request creates an inactive, connected five-step flow without the model',async()=>{
 const result=inventoryWorkflowFromRequest(request,accounts);
 assert.ok(result?.workflow);
 const flow=result.workflow;
 assert.equal(flow.active,false);
 assert.deepEqual(flow.nodes.map(node=>node.type),['n8n-nodes-base.scheduleTrigger','easyflow.googleSheets','n8n-nodes-base.code','easyflow.googleSheets','n8n-nodes-base.slack']);
 assert.equal(flow.nodes[0].parameters.rule.interval[0].triggerAtHour,9);
 assert.equal(flow.nodes[1].parameters.range,'재고!A1:D1000');
 assert.equal(flow.nodes[1].credentialId,'sheets-credential');
 assert.equal(flow.nodes[3].parameters.range,'부족재고_기록!A:F');
 assert.equal(flow.nodes[4].credentialId,'slack-credential');
 assert.equal(flow.nodes[4].parameters.channelId.value,'C0CH769U6BX');
 assert.equal(flow.connections['부족 재고 정리'].main[0][0].node,'부족 재고 기록');
 assert.equal(flow.connections['부족 재고 기록'].main[0][0].node,'Slack 알림 보내기');
 assert.equal(flow.nodes[4].parameters.text,'={{ $("부족 재고 정리").first().json.message }}');
 const output=await runCode(flow.nodes[2].parameters.jsCode,[
  {rowIndex:0,values:['SKU','상품명','현재 수량','기준 수량']},
  {rowIndex:1,values:['A-1','키보드',2,5]},
  {rowIndex:2,values:['B-1','모니터',10,5]},
 ]);
 assert.equal(output.length,1);
 assert.equal(output[0].count,1);
 assert.deepEqual(output[0].rows[0].slice(1),['A-1','키보드',2,5,3]);
 assert.match(output[0].message,/키보드/);
});

test('incomplete inventory request asks for identifiers instead of creating an unsafe flow',()=>{
 const result=inventoryWorkflowFromRequest('매일 오전 9시에 Google Sheets 재고 부족을 찾아 시트에 기록하고 Slack으로 보내줘.',accounts);
 assert.ok(result&&!result.workflow);
 assert.match(JSON.stringify(result.questions),/스프레드시트 ID/);
 assert.match(JSON.stringify(result),/Slack 채널 ID/);
});

test('generated flow records shortages before Slack and does not notify after an append failure',async()=>{
 const generated=inventoryWorkflowFromRequest(request,accounts);
 if(!generated?.workflow)throw new Error('Expected an inventory workflow');
 const flow=generated.workflow;
 const secrets:Record<string,CredentialSecret>={
  'sheets-credential':{...accounts[0],data:{token:'fixture-sheets-token',service:'googleSheets'}},
  'slack-credential':{...accounts[1],data:{token:'fixture-slack-token'}},
 };
 const values=[['SKU','상품명','현재 수량','기준 수량'],['KBD','키보드','2','5'],['MON','모니터','10','5']];
 async function run(appendFails=false){
  const calls:string[]=[];
  const fetch=async(url:string,init:RequestInit)=>{
   calls.push(url);
   if(url.includes('/values/%EC%9E%AC%EA%B3%A0!'))return new Response(JSON.stringify({range:'재고!A1:D1000',values}));
   if(url.includes(':append'))return new Response(JSON.stringify(appendFails?{error:{message:'fixture append failure'}}:{updatedCells:6}),{status:appendFails?500:200});
   if(url==='https://slack.com/api/chat.postMessage'){
    assert.match(JSON.parse(String(init.body)).text,/키보드/);
    return new Response(JSON.stringify({ok:true,channel:'C0CH769U6BX',ts:'1'}));
   }
   throw new Error('Unexpected URL: '+url);
  };
  const options:RunOptions={mode:'live',credential:id=>secrets[id],fetch:fetch as typeof globalThis.fetch};
  return {execution:await executeFlow(flow,[{}],options),calls};
 }
 const success=await run();assert.equal(success.execution.status,'success',success.execution.error);
 assert.equal(success.calls.length,3);assert.match(success.calls[1],/:append/);assert.equal(success.calls[2],'https://slack.com/api/chat.postMessage');
 const failed=await run(true);assert.equal(failed.execution.status,'error');assert.equal(failed.calls.length,2);
});

test('unrelated and different Sheets or Slack tasks still use the general design path',()=>{
 assert.equal(inventoryWorkflowFromRequest('Google Sheets 매출을 Slack으로 요약해줘.',accounts),null);
 assert.equal(inventoryWorkflowFromRequest('Google Sheets 재고 부족을 찾아 Slack으로 보내줘.',accounts),null);
 assert.equal(inventoryWorkflowFromRequest(request+' 단, Slack으로 보내지 마.',accounts),null);
 const ambiguous=inventoryWorkflowFromRequest(request+' 다른 Slack 채널 ID C0CH769U6BY에도 보내줘.',accounts);
 assert.ok(ambiguous&&!ambiguous.workflow);
 assert.match(JSON.stringify(ambiguous),/채널 ID/);
});
