import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {makeFlow,clone,deleteNode,exportFlow,nodePorts,renameNode,validateFlow,type Flow,type FlowNode} from '../lib/workflow';
import {executeFlow} from './engine';

const stage=(id:string,name:string,type='set',parameters:Record<string,any>={}):FlowNode=>({id,name,type:`n8n-nodes-base.${type}`,typeVersion:1,position:[100,200],parameters});

// Several channels, branch slots and incoming sources expose edits that silently lose graph data.
function graph():Flow {
 return {
  id:'workflow-source',name:'가져온 자동화',active:false,n8nId:'remote-id',updatedAt:'2026-01-01T00:00:00.000Z',
  nodes:[stage('start','시작','manualTrigger'),stage('branch','분기','if'),{...stage('subject','정리'),notes:'설명을 보존해요',vendor:{version:7}},stage('other','다른 입력'),stage('end','완료')],
  connections:{
   시작:{main:[[{node:'분기',type:'main',index:0}]]},
   분기:{main:[[{node:'정리',type:'main',index:0,edgeLabel:'첫 경로'},{node:'완료',type:'main',index:0}],[],[{node:'정리',type:'main',index:1}]],ai_tool:[[{node:'정리',type:'ai_tool',index:2,vendor:{keep:true}}]]},
   정리:{main:[[{node:'완료',type:'main',index:0,edgeLabel:'내 연결'}]],ai_custom:[[],[{node:'다른 입력',type:'ai_custom',index:1}]]},
   '다른 입력':{main:[[{node:'정리',type:'main',index:3},{node:'완료',type:'main',index:0}]]},
  },
  settings:{executionOrder:'v1',timezone:'Asia/Seoul',vendor:{retain:['setting']}},
  pinData:{정리:[{json:{name:'고정 예시',nested:[1,2]}}],'다른 입력':[{json:{keep:true}}]},
  staticData:{global:{cursor:42}},description:'외부 프로그램에서 가져온 정보',tags:[{id:'tag-1',name:'가져옴'}],vendor:{schema:'custom',nested:{enabled:true}},
 };
}

test('JSON import/export preserves unknown workflow, node, connection and pinned metadata across a second import',()=>{
 const imported=validateFlow(JSON.parse(JSON.stringify(graph())));
 const portable=exportFlow(imported);
 assert.equal(Object.hasOwn(portable,'id'),false);
 assert.equal(Object.hasOwn(portable,'n8nId'),false);
 assert.equal(Object.hasOwn(portable,'updatedAt'),false);
 const restored=validateFlow(JSON.parse(JSON.stringify(portable)));
 assert.notEqual(restored.id,imported.id);
 assert.deepEqual(exportFlow(restored),portable);
 assert.deepEqual(restored.vendor,imported.vendor);
 assert.deepEqual(restored.nodes.find(node=>node.id==='subject')?.vendor,{version:7});
 assert.deepEqual(restored.connections['분기'].ai_tool[0][0].vendor,{keep:true});
 assert.deepEqual(restored.pinData,imported.pinData);
 assert.deepEqual(restored.staticData,imported.staticData);
});

test('saving an unchanged node name preserves every incoming and outgoing branch and its pinned data',()=>{
 const source=graph(),before=clone(source);
 const saved=renameNode(source,'subject','정리');
 assert.deepEqual(saved,before);
 assert.deepEqual(source,before);
 assert.notEqual(saved,source);
});

test('renaming a node moves its outgoing channels and pinned data, and rewrites all incoming references without losing edge metadata',()=>{
 const source=graph(),before=clone(source);
 const renamed=renameNode(source,'subject','새 이름');
 assert.equal(renamed.nodes.find(node=>node.id==='subject')?.name,'새 이름');
 assert.equal(Object.hasOwn(renamed.connections,'정리'),false);
 assert.deepEqual(renamed.connections['새 이름'],before.connections['정리']);
 assert.equal(Object.hasOwn(renamed.pinData,'정리'),false);
 assert.deepEqual(renamed.pinData['새 이름'],before.pinData['정리']);
 assert.deepEqual(renamed.pinData['다른 입력'],before.pinData['다른 입력']);
 assert.deepEqual(renamed.connections['분기'],{
  main:[[{node:'새 이름',type:'main',index:0,edgeLabel:'첫 경로'},{node:'완료',type:'main',index:0}],[],[{node:'새 이름',type:'main',index:1}]],
  ai_tool:[[{node:'새 이름',type:'ai_tool',index:2,vendor:{keep:true}}]],
 });
 assert.deepEqual(renamed.connections['다른 입력'],{main:[[{node:'새 이름',type:'main',index:3},{node:'완료',type:'main',index:0}]]});
 assert.deepEqual(renamed.connections['시작'],before.connections['시작']);
 assert.deepEqual(renamed.nodes.find(node=>node.id==='subject')?.vendor,{version:7});
 assert.deepEqual(source,before);
 // Renaming back must recover the original graph, including empty branch slots.
 assert.deepEqual(renameNode(renamed,'subject','정리'),before);
});

test('deleting a node removes all links and only its own pin data while retaining unrelated routes and empty branch slots',()=>{
 const source=graph(),before=clone(source);
 const deleted=deleteNode(source,'subject');
 assert.equal(deleted.nodes.some(node=>node.id==='subject'),false);
 assert.equal(Object.hasOwn(deleted.connections,'정리'),false);
 assert.equal(Object.hasOwn(deleted.pinData,'정리'),false);
 assert.deepEqual(deleted.pinData['다른 입력'],before.pinData['다른 입력']);
 assert.deepEqual(deleted.connections['분기'],{main:[[{node:'완료',type:'main',index:0}],[],[]],ai_tool:[[]]});
 assert.deepEqual(deleted.connections['다른 입력'],{main:[[{node:'완료',type:'main',index:0}]]});
 assert.deepEqual(deleted.connections['시작'],before.connections['시작']);
 assert.deepEqual(deleted.vendor,before.vendor);
 assert.deepEqual(source,before);
 assert.doesNotThrow(()=>validateFlow(deleted));
});

test('imports missing workflow/node IDs generate distinct identifiers that survive export and subsequent import',()=>{
 const data=graph() as Record<string,any>;delete data.id;
 for(const node of data.nodes)delete node.id;
 const imported=validateFlow(data);
 assert.match(imported.id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
 const ids=imported.nodes.map(node=>node.id);
 assert.equal(new Set(ids).size,imported.nodes.length);
 assert.ok(ids.every(id=>typeof id==='string'&&id.length>0));
 assert.deepEqual(validateFlow(JSON.parse(JSON.stringify(exportFlow(imported)))).nodes.map(node=>node.id),ids);
});

test('import rejects duplicate node names or IDs before ambiguous references can be saved',()=>{
 const duplicateIds=graph();duplicateIds.nodes[2].id=duplicateIds.nodes[1].id;
 assert.throws(()=>validateFlow(duplicateIds),/이름과 ID/);
 const duplicateNames=graph();duplicateNames.nodes[2].name=duplicateNames.nodes[1].name;
 assert.throws(()=>validateFlow(duplicateNames),/이름과 ID/);
});

test('malformed imported Switch rules remain renderable without crashing or producing non-text ports',()=>{
 const cases=[{rules:null},{rules:'invalid'},{rules:{values:null}},{rules:{values:'invalid'}},{rules:{values:[null,'invalid',{}, {renameOutput:true,outputKey:'승인'}]}}];
 for(const parameters of cases){
  const node=stage('switch','갈림길','switch',{mode:'rules',...parameters,options:{fallbackOutput:'extra'}});
  const before=clone(node);
  const ports=nodePorts(node);
  assert.ok(ports.outputs.length>0);
  assert.ok(ports.outputs.every(label=>typeof label==='string'&&label.length>0));
  assert.equal(ports.outputs.at(-1),'나머지');
  assert.deepEqual(node,before);
 }
 assert.equal(nodePorts(stage('switch','갈림길','switch',{rules:{values:[null,{renameOutput:true,outputKey:'승인'}]}})).outputs[1],'승인');
});

test('error handling adds exactly one final output after dynamic IF, Switch and terminal ports without mutating the catalog',()=>{
 const stages=[
  stage('set','정리'),
  stage('if','조건','if'),
  stage('switch','갈림길','switch',{mode:'rules',rules:{values:[{renameOutput:true,outputKey:'승인'},{}]},options:{fallbackOutput:'extra'}}),
  stage('expression','번호 갈림길','switch',{mode:'expression',numberOutputs:5}),
  stage('stop','중지','stopAndError'),
 ];
 assert.equal(nodePorts(stages[1]).outputs.length,2);
 assert.equal(nodePorts(stages[2]).outputs.length,3);
 assert.equal(nodePorts(stages[3]).outputs.length,5);
 assert.equal(nodePorts(stages[4]).outputs.length,0);
 for(const node of stages){
  const normal=nodePorts(node);
  const withError=nodePorts({...node,onError:'continueErrorOutput'});
  assert.deepEqual(withError.outputs,[...normal.outputs,'오류']);
  assert.equal(withError.inputs,normal.inputs);
  assert.deepEqual(nodePorts(node),normal);
  assert.deepEqual(nodePorts({...node,onError:'continueRegularOutput'}),normal);
 }
});

test('renaming to an existing node name fails without overwriting paths or pinned data',()=>{const original=makeFlow();original.pinData={[original.nodes[0].name]:[{example:'start'}],[original.nodes[1].name]:[{example:'fields'}]};const snapshot=clone(original);assert.throws(()=>renameNode(original,original.nodes[0].id,original.nodes[1].name),/이름/);assert.deepEqual(original,snapshot);});
test('renaming an earlier node keeps quoted expression references executable',async()=>{
 const original=makeFlow();original.nodes[3].parameters.assignments.assignments=[{id:'score',name:'score',type:'number',value:'={{ $("데이터 정리").first().json.score + 1 }}'}];
 const renamed=renameNode(original,original.nodes[1].id,'입력 "고객"');
 const result=await executeFlow(renamed,[{}],{mode:'learning',credential:()=>{throw new Error('계정 호출 없음');}});
 assert.equal(result.status,'success',result.error);assert.deepEqual(result.runs.at(-1)?.output[0],[{score:93}]);
 assert.equal(original.nodes[1].name,'데이터 정리');
});
test('Merge ports follow the configured input count and field/cartesian combinations stay at two ports',()=>{
 assert.equal(nodePorts(stage('merge','합치기','merge',{mode:'append',numberInputs:4})).inputs,4);
 assert.equal(nodePorts(stage('merge','합치기','merge',{mode:'combine',combineBy:'combineByPosition',numberInputs:3})).inputs,3);
 assert.equal(nodePorts(stage('merge','합치기','merge',{mode:'chooseBranch',numberInputs:5})).inputs,5);
 for(const combineBy of ['combineByFields','combineAll'])assert.equal(nodePorts(stage('merge','합치기','merge',{mode:'combine',combineBy,numberInputs:3})).inputs,2);
});

test('Respond to Webhook ports preserve the always-visible version 1.3 response branch and the optional version 1.4+ branch',()=>{
 for(const typeVersion of [1,1.1,1.2,1.3,1.4,1.5])for(const enabled of [undefined,false,true]){
  const node={...stage('respond','요청에 답하기','respondToWebhook',enabled===undefined?{}:{enableResponseOutput:enabled}),typeVersion};
  const before=clone(node),expected=typeVersion===1.3||typeVersion>=1.4&&enabled===true?['받은 정보','보낸 응답']:['받은 정보'];
  assert.deepEqual(nodePorts(node),{inputs:1,outputs:expected});
  assert.deepEqual(nodePorts({...node,onError:'continueErrorOutput'}),{inputs:1,outputs:[...expected,'오류']});
  assert.deepEqual(node,before);
 }
});

test('portable order-response example returns its actual JSON response and keeps original-input and response branches separate',async()=>{
 const flow=validateFlow(JSON.parse(readFileSync(new URL('../examples/요청에_결과_돌려주기.workflow.json',import.meta.url),'utf8')));
 let response:any,calls=0;
 const execution=await executeFlow(flow,[{body:{customer:'예시 고객'},params:{orderId:'order-123'},query:{},headers:{}}],{mode:'live',webhookResponseMode:'responseNode',onWebhookResponse:value=>{calls++;response=value;},credential:()=>{throw new Error('이 예제는 연결 계정이 필요하지 않습니다.');}});
 assert.equal(execution.status,'success',execution.error);assert.equal(calls,1);assert.equal(execution.runs.length,5);
 assert.deepEqual(response,{statusCode:201,headers:{'x-easyflow-example':'order-response'},body:{customer:'예시 고객',orderId:'order-123',message:'주문을 접수했어요.'}});
 assert.deepEqual(execution.runs.find(run=>run.name==='원래 주문 정보')!.output[0],[response.body]);
 assert.deepEqual(execution.runs.find(run=>run.name==='보낸 응답 정보')!.output[0],[{response}]);
 assert.equal(flow.active,false);assert.equal(flow.hookToken,undefined);
});
