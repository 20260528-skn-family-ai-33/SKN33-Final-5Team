import test from 'node:test';
import assert from 'node:assert/strict';
import {visualModelConnection} from '../src/visual-model';
import {modelRequestTimeoutMs,SERVER_VISUAL_MODEL_ID,supportedCatalogForModel} from '../src/assistant';

test('external workflow design uses the configured A4B model without a member API key',async()=>{
 const original=globalThis.fetch;
 try{
  globalThis.fetch=(async(input,init)=>{
   assert.equal(String(input),'https://model.example.test/v1/chat/completions');
   assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer server-only-key');
   const body=JSON.parse(String(init?.body));
   assert.equal(body.messages[0].content,'설계 지침');
   assert.equal(body.messages[1].content,'사용자 요청');
   assert.equal(body.response_format.type,'json_schema');
   assert.equal(body.think,false);
   return Response.json({choices:[{message:{content:'{"workflow":null}'}}]});
  }) as typeof fetch;
  const connection=visualModelConnection({A4B_MAIN_PROVIDER:'ollama',A4B_MAIN_BASE_URL:'https://model.example.test/v1',A4B_MAIN_MODEL:'local-model',A4B_MAIN_API_KEY:'server-only-key'});
  const response=await connection.fetch('https://api.openai.com/v1/responses',{method:'POST',body:JSON.stringify({instructions:'설계 지침',input:'사용자 요청',text:{format:{type:'json_schema',name:'workflow',schema:{type:'object'}}}})});
  assert.equal(connection.model,'local-model');
  assert.equal((await response.json()).output[0].content[0].text,'{"workflow":null}');
  assert.equal(connection.credential.provider,'openai');
 }finally{globalThis.fetch=original;}
});
test('unconfigured A4B model does not fall back to a member key',()=>{
 assert.throws(()=>visualModelConnection({A4B_MAIN_PROVIDER:'openai',A4B_MAIN_MODEL:'gpt-4o'}),/설계 모델 연결/);
});
test('server visual design uses configured model timeout without changing member model calls',()=>{
 assert.equal(modelRequestTimeoutMs({id:SERVER_VISUAL_MODEL_ID},{A4B_MAIN_TIMEOUT_S:'180'}),180_000);
 assert.equal(modelRequestTimeoutMs({id:SERVER_VISUAL_MODEL_ID},{}),180_000);
 assert.equal(modelRequestTimeoutMs({id:'member-model'},{A4B_MAIN_TIMEOUT_S:'180'}),60_000);
});
test('server visual design keeps supported tools while compacting unrelated field details',()=>{
 const request='매일 오전 9시에 구글 시트 재고를 읽고 부족한 항목을 Slack으로 알리고 시트에 기록해줘';
 const complete=supportedCatalogForModel(request);
 const compact=supportedCatalogForModel(request,true);
 assert.deepEqual(compact.map(tool=>tool.type),complete.map(tool=>tool.type));
 assert.ok(JSON.stringify(compact).length<JSON.stringify(complete).length*0.7);
 assert.ok(compact.find(tool=>tool.type==='easyflow.googleSheets')?.fields);
 assert.equal(compact.find(tool=>tool.type==='n8n-nodes-base.webhook')?.fields,undefined);
 assert.ok(supportedCatalogForModel('Webhook 요청을 받아 응답해줘',true).find(tool=>tool.type==='n8n-nodes-base.webhook')?.fields);
});
