import {examplePath} from './paths';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {generateWorkflow} from './assistant';
import {makeFlow,clone,validateFlow,type Flow,type FlowNode,type Json} from '../lib/workflow';
import {loopNodeType,compileLoopPlan} from './loops';
import {executeFlow} from './engine';
import {createBinaryFile,withBinary} from './binary';
import type {CredentialPublic,Provider} from './contracts';
const credential={id:'ai',name:'테스트',provider:'openai' as const,createdAt:'',updatedAt:'',data:{apiKey:'test-secret'}};
const reply=(value:any)=>new Response(JSON.stringify({status:'completed',output:[{type:'reasoning'},{type:'message',content:[{type:'output_text',text:JSON.stringify(value)}]}]}));
test('AI builder requests JSON drafts directly and never executes or preserves generated activation/secrets',async()=>{
 const flow=makeFlow();Object.assign(flow,{active:true,pinData:{},hookToken:'invented',settings:{errorWorkflow:'invented'}});let calls=0;
 const r=await generateWorkflow('점수가 높은 고객을 분류해줘',{credential,credentials:[credential],fetch:async(url,init)=>{calls++;assert.equal(url,'https://api.openai.com/v1/responses');assert.equal((init!.headers as any).Authorization,'Bearer test-secret');const body=JSON.parse(String(init!.body));assert.equal(body.text.format.type,'json_object');assert.equal(body.store,false);assert.equal(JSON.stringify(body).includes('test-secret'),false);return reply({workflow:flow,summary:'점수를 확인해요.',checklist:['점수 입력']});}});
 assert.equal(calls,1);assert.notEqual(r.workflow.id,flow.id);assert.equal(r.workflow.active,false);assert.equal(r.workflow.hookToken,undefined);assert.equal(r.workflow.pinData,undefined);assert.equal(r.workflow.settings.errorWorkflow,undefined);assert.equal(r.summary,'점수를 확인해요.');
});
test('AI builder rejects unsupported nodes, cycles, refusal and malformed output',async()=>{
 const options=(value:any)=>({credential,credentials:[],fetch:async()=>reply(value)});
 const unknown=makeFlow();unknown.nodes[1].type='unknown';await assert.rejects(()=>generateWorkflow('자동화 초안을 만들어줘',options({workflow:unknown})),/지원하지/);
 const cycle=makeFlow();cycle.connections[cycle.nodes[3].name]={main:[[{node:cycle.nodes[1].name,type:'main',index:0}]]};await assert.rejects(()=>generateWorkflow('자동화 초안을 만들어줘',options({workflow:cycle})),/반복 연결/);
 await assert.rejects(()=>generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(JSON.stringify({status:'completed',output:[{type:'message',content:[{type:'refusal',refusal:'no'}]}]}))}),/만들 수 없/);
});
const generate=(flow:any,extra:any={},publicCredentials:any[]=[])=>generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:publicCredentials,fetch:async()=>reply({workflow:flow,summary:'설명',checklist:[],...extra})});
test('AI drafts can reuse previous-node data and calculations through the independent engine',async()=>{
 const flow=makeFlow();flow.nodes[3].parameters.assignments.assignments=[{id:'original',name:'originalName',value:'={{ $("데이터 정리").item.json.name }}',type:'string'},{id:'adjusted',name:'adjustedScore',value:'={{ $json.score + 3 }}',type:'number'}];
 const generated=await generateWorkflow('앞의 고객 정보를 다시 가져와서 점수에 3을 더해줘',{credential,credentials:[],fetch:async(_url,init)=>{const instructions=JSON.parse(String(init!.body)).instructions;assert.match(instructions,/이전 단계/);assert.match(instructions,/현재 회차/);assert.match(instructions,/Merge 합치기/);assert.match(instructions,/현재 항목의 원본 값/);assert.match(instructions,/itemMatching/);assert.equal(instructions.includes("현대 .item 항목 연결은 아직 지원하지"),false);return reply({workflow:flow,summary:'고객 정보를 유지하고 점수를 계산해요.',checklist:[]});}});
 const result=await executeFlow(generated.workflow,[{}],{mode:'learning',credential:()=>{throw new Error('계정 호출 없음');}});
 assert.equal(result.status,'success');assert.deepEqual(result.runs.at(-1)?.output[0],[{originalName:'예시 고객 김민지',adjustedScore:95}]);
});
test('draft root, node and edge metadata are rebuilt from allowed fields before validation',async()=>{
 const flow=makeFlow();Object.assign(flow,{credentials:{apiKey:credential.data.apiKey},staticData:{secret:credential.data.apiKey},meta:{admin:true},subflows:{injected:true},schedule:{nextRunAt:1},hookToken:'invented',active:true});
 Object.assign(flow.nodes[1],{credentials:{apiKey:credential.data.apiKey},disabled:true,retryOnFail:true,onError:'continueRegularOutput',continueOnFail:true,alwaysOutputData:true,executeOnce:true,scheduleAnchorAt:1});
 Object.assign(flow.connections[flow.nodes[0].name].main[0][0],{secret:credential.data.apiKey,admin:true});
 const r=await generate(flow);assert.deepEqual(Object.keys(r.workflow).sort(),['active','connections','id','name','nodes','settings']);assert.equal(r.workflow.active,false);
 assert.deepEqual(Object.keys(r.workflow.nodes[1]).sort(),['id','name','parameters','position','type','typeVersion']);assert.deepEqual(Object.keys(r.workflow.connections[flow.nodes[0].name].main[0][0]).sort(),['index','node','type']);assert.equal(JSON.stringify(r).includes(credential.data.apiKey),false);
});
test('nested inline secret material and account IDs are rejected, including JSON strings',async()=>{
 for(const key of ['key','apiKey','API_KEY','privateKey','private_key','passphrase','token','access_token','password','credentials','credentialId']){
  const flow=makeFlow();flow.nodes[1].parameters={nested:{[key]:'invented-private-value'}};await assert.rejects(generate(flow),/비밀값이나 연결 ID/);
 }
 const embedded=makeFlow();embedded.nodes[1].parameters={mode:'raw',jsonOutput:'{"child":{"credentials":{"apiKey":"invented"}}}'};await assert.rejects(generate(embedded),/비밀값이나 연결 ID/);
});
test('ordinary Set data-column names remain usable and known keys are masked everywhere returned',async()=>{
 const flow=makeFlow();flow.name='자동화 '+credential.data.apiKey;flow.nodes[1].parameters.assignments.assignments.push({id:'token-data',name:'token',value:'일반 열 데이터',type:'string'},{id:'secret-text',name:'content',value:'문장 '+credential.data.apiKey,type:'string'});
 const r=await generate(flow,{summary:'설명 '+credential.data.apiKey,checklist:[credential.data.apiKey,'x'.repeat(10000)]});
 assert.equal(r.workflow.nodes[1].parameters.assignments.assignments.find((v:any)=>v.name==='token').value,'일반 열 데이터');assert.equal(JSON.stringify(r).includes(credential.data.apiKey),false);assert.match(r.summary,/보호된 연결 정보/);assert.equal(r.checklist[1].length,500);
});
test('known selected API key is masked in user input and never sent as prompt material',async()=>{
 let calls=0;await generateWorkflow('본문에 '+credential.data.apiKey+'가 있지만 키는 보내지 마세요.',{credential,credentials:[],fetch:async(_url,init)=>{calls++;const body=String(init!.body);assert.equal(body.includes(credential.data.apiKey),false);assert.match(body,/보호된 연결 정보/);return reply({workflow:makeFlow(),summary:'초안',checklist:[]});}});assert.equal(calls,1);
});
test('HTTP inline auth pairs and URLs carrying auth are rejected',async()=>{
 for(const parameters of [{url:'https://example.test',sendHeaders:true,headerParameters:{parameters:[{name:'Authorization',value:'Bearer invented'}]}},{url:'https://example.test',sendBody:true,bodyParameters:{parameters:[{name:'password',value:'invented'}]}},{url:'https://example.test?token=invented'},{url:'https://account:invented@example.test'}]){
  const flow=makeFlow();flow.nodes[1].type='n8n-nodes-base.httpRequest';flow.nodes[1].parameters=parameters;await assert.rejects(generate(flow),/인증 정보/);
 }
});
test('connections reject invalid target slots and non-main edge types',async()=>{
 for(const change of [{index:1},{index:999},{type:'ai_languageModel'},{index:-1}]){
  const flow=makeFlow();Object.assign(flow.connections[flow.nodes[0].name].main[0][0],change);await assert.rejects(generate(flow),/경로|입력 번호/);
 }
});
test('removed execution metadata cannot create extra output ports',async()=>{
 const flow=makeFlow();flow.nodes[1].onError='continueErrorOutput';flow.connections[flow.nodes[1].name].main.push([{node:flow.nodes[3].name,type:'main',index:0}]);await assert.rejects(generate(flow),/출력 경로/);
});
test('all generated steps must be reachable from the sole start',async()=>{
 const flow=makeFlow();flow.nodes.push({...flow.nodes[3],id:'orphan',name:'연결되지 않은 단계'});await assert.rejects(generate(flow),/연결되지 않은/);
 const detached=makeFlow();delete detached.connections[detached.nodes[0].name];await assert.rejects(generate(detached),/연결되지 않은/);
});
test('account binding is restricted to descriptors and compatible provider/service',async()=>{
 const plain=makeFlow();plain.nodes[1].credentialId=credential.id;const r=await generate(plain,{},[credential]);assert.equal(r.workflow.nodes[1].credentialId,undefined);
 const github=makeFlow();github.nodes[1].type='easyflow.github';github.nodes[1].parameters={operation:'listIssues',owner:'octocat',repository:'Hello-World'};github.nodes[1].credentialId='other';
 const other={id:'other',name:'Notion',provider:'bearer',service:'notion',createdAt:'',updatedAt:''};const correct={id:'github',name:'GitHub',provider:'bearer',service:'github',createdAt:'',updatedAt:''};
 const mismatch=await generate(github,{},[other]);assert.equal(mismatch.workflow.nodes[1].credentialId,undefined);assert.ok(mismatch.checklist.some(v=>v.includes('연결 계정')));
 const bound=await generate(github,{},[other,correct]);assert.equal(bound.workflow.nodes[1].credentialId,'github');
 const http=makeFlow();http.nodes[1].type='n8n-nodes-base.httpRequest';http.nodes[1].parameters={method:'GET',url:'https://example.test',authentication:'genericCredentialType'};http.nodes[1].credentialId=correct.id;assert.equal((await generate(http,{},[correct])).workflow.nodes[1].credentialId,undefined);
 const forged=makeFlow();forged.nodes[1].parameters.credentialId='other';await assert.rejects(generate(forged,{},[other]),/연결 ID/);
});
test('response stream stops at 2MB in bytes and cancels before consuming the whole response',async()=>{
 let chunks=0,canceled=false;const stream=new ReadableStream<Uint8Array>({pull(controller){chunks++;controller.enqueue(new Uint8Array(300000).fill(32));if(chunks===20)controller.close();},cancel(){canceled=true;}});
 await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(stream)}),/2MB/);assert.equal(canceled,true);assert.ok(chunks<20);
 const korean='가'.repeat(700000);assert.ok(korean.length<2000000);await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(korean)}),/2MB/);
 let pulled=0,earlyCancel=false;const lengthStream=new ReadableStream<Uint8Array>({pull(controller){pulled++;controller.enqueue(new Uint8Array(1));},cancel(){earlyCancel=true;}},{highWaterMark:0});
 await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(lengthStream,{headers:{'content-length':'2000001'}})}),/2MB/);assert.equal(pulled,0);assert.equal(earlyCancel,true);
});
test('model strings are bounded and invalid models never call the provider',async()=>{
 for(const model of ['', '   ', 'x'.repeat(121),123]){let calls=0;await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],model:model as any,fetch:async()=>{calls++;return reply({workflow:makeFlow()});}}),/모델 이름/);assert.equal(calls,0);}
 let captured='';await generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],model:' user-selected-model ',fetch:async(_url,init)=>{captured=JSON.parse(String(init!.body)).model;return reply({workflow:makeFlow()});}});assert.equal(captured,'user-selected-model');
});
test('incomplete, failed, malformed envelope and mixed refusal outputs cannot become drafts',async()=>{
 const normal=JSON.stringify({workflow:makeFlow(),summary:'설명',checklist:[]});
 const envelopes=[{status:'incomplete',output:[]},{status:'failed',output:[{type:'message',content:[{type:'output_text',text:normal}]}]},{status:'completed',output:{}},{status:'completed',output:[{type:'message',content:[{type:'refusal',refusal:'no'},{type:'output_text',text:normal}]}]},null,{status:'completed',output:[{type:'message',content:[{type:'output_text',text:123}]}]}];
 for(const value of envelopes)await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(JSON.stringify(value))}));
});
test('network and response read exceptions never return raw provider secret text',async()=>{
 await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>{throw new Error('transport '+credential.data.apiKey);}}),error=>!String(error).includes(credential.data.apiKey));
 const stream=new ReadableStream({start(controller){controller.error(new Error('body '+credential.data.apiKey));}});await assert.rejects(generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response(stream)}),error=>!String(error).includes(credential.data.apiKey));
});
test('AI builder reports provider failures without leaking credential material',async()=>{
 await assert.rejects(()=>generateWorkflow('자동화 초안을 만들어줘',{credential,credentials:[],fetch:async()=>new Response('test-secret',{status:401})}),error=>{assert.equal((error as Error).message.includes('test-secret'),false);return true;});
 await assert.rejects(()=>generateWorkflow('짧음',{credential,credentials:[]}),/5자/);
});

const draftNode=(name:string,type='n8n-nodes-base.noOp',parameters:Json={}):FlowNode=>({id:name,name,type,typeVersion:type===loopNodeType?3:1,position:[0,0],parameters});
function draftGraph(nodes:FlowNode[],edges:[string,string,number?,number?][]):Flow {
 const connections:Flow['connections']={};for(const [source,target,output=0,input=0] of edges){const channels=connections[source]??={main:[]};while(channels.main.length<=output)channels.main.push([]);channels.main[output].push({node:target,type:'main',index:input});}
 return {id:'generated-loop',name:'CSV를 묶음별로 처리',nodes,connections,settings:{},active:true};
}
const loopDraft=()=>draftGraph([
 draftNode('start','n8n-nodes-base.manualTrigger'),
 draftNode('loop',loopNodeType,{batchSize:2,options:{reset:false}}),
 draftNode('body','n8n-nodes-base.httpRequest',{url:'https://example.test/write',method:'POST'}),
 draftNode('done'),
],[['start','loop'],['loop','body',1],['body','loop'],['loop','done',0]]);

test('AI drafts accept one structured loop with exact done/loop ports and still perform only the provider request',async()=>{
 const flow=loopDraft();flow.loopContexts={injected:true};flow.nodes[1].onError='continueErrorOutput';let calls=0;
 const result=await generateWorkflow('목록을 두 개씩 처리하고 완료된 결과를 모아줘',{credential,credentials:[],fetch:async(url,init)=>{
  calls++;assert.equal(url,'https://api.openai.com/v1/responses');const body=JSON.parse(String(init!.body));
  assert.match(body.instructions,/done 완료는 출력0, loop 반복 묶음은 출력1/);
  assert.match(body.instructions,/feedback 연결 하나/);assert.match(body.instructions,/options\.reset은 false/);assert.match(body.instructions,/무조건 reset:true/);
  assert.match(body.instructions,/8MB 이하/);assert.match(body.instructions,/UTF-8/);assert.match(body.instructions,/실제 파일 업로드/);
  assert.ok(body.instructions.includes('n8n-nodes-base.convertToFile'));assert.ok(body.instructions.includes('n8n-nodes-base.extractFromFile'));
  return reply({workflow:flow,summary:'묶음별로 처리하고 결과를 모아요.',checklist:[]});
 }});
 assert.equal(calls,1);assert.equal(result.workflow.active,false);assert.notEqual(result.workflow.id,flow.id);assert.equal(result.workflow.loopContexts,undefined);assert.equal(result.workflow.nodes[1].onError,undefined);assert.deepEqual(result.workflow.nodes[1].parameters,{batchSize:2,options:{reset:false}});
 const plan=compileLoopPlan(result.workflow);assert.equal(plan.loops.get('loop')?.feedback.source,'body');assert.deepEqual(plan.loops.get('loop')?.body.ordered.map(node=>node.name),['body']);assert.deepEqual(plan.root.ordered.map(node=>node.name),['start','loop','done']);
});

test('AI loop drafts reject missing/duplicate feedback, arbitrary body cycles, cross-entry and shared done/body paths',async()=>{
 const missing=loopDraft();delete missing.connections.body;
 const duplicate=loopDraft();duplicate.connections.body.main[0].push({node:'loop',type:'main',index:0});
 const cycle=loopDraft();cycle.nodes.push(draftNode('inner-body'));cycle.connections.body.main[0].push({node:'inner-body',type:'main',index:0});cycle.connections['inner-body']={main:[[{node:'body',type:'main',index:0}]]};
 const crossEntry=loopDraft();crossEntry.connections.start.main[0].push({node:'body',type:'main',index:0});
 const doneIntoBody=loopDraft();doneIntoBody.connections.loop.main[0]=[{node:'body',type:'main',index:0}];
 const escapedBody=loopDraft();escapedBody.connections.body.main[0].push({node:'done',type:'main',index:0});
 for(const flow of [missing,duplicate,cycle,crossEntry,doneIntoBody,escapedBody]){
  let calls=0;await assert.rejects(generateWorkflow('목록을 묶음별로 반복 처리해줘',{credential,credentials:[],fetch:async(url)=>{calls++;assert.equal(url,'https://api.openai.com/v1/responses');return reply({workflow:flow,checklist:[]});}}),/반복 연결/);assert.equal(calls,1);
 }
});

test('structured loop validation retains rejection of invalid ports and disconnected steps',async()=>{
 const badOutput=loopDraft();badOutput.connections.loop.main.push([{node:'done',type:'main',index:0}]);await assert.rejects(generate(badOutput),/출력 경로/);
 const badInput=loopDraft();badInput.connections.body.main[0][0].index=1;await assert.rejects(generate(badInput),/입력 경로/);
 const disconnected=loopDraft();disconnected.nodes.push(draftNode('orphan'));await assert.rejects(generate(disconnected),/연결되지 않은/);
});

test('file and loop stages can be combined in a draft, with actual-file upload and UTF-8 guidance added independently of model checklist',async()=>{
 const flow=loopDraft();flow.nodes.splice(1,0,draftNode('CSV 읽기','n8n-nodes-base.extractFromFile',{operation:'csv',binaryPropertyName:'uploaded',options:{encoding:'utf-8'}}));flow.nodes[flow.nodes.length-1]=draftNode('done','n8n-nodes-base.convertToFile',{operation:'csv',binaryPropertyName:'result',fileName:'처리 결과.csv',encoding:'utf8'});
 flow.connections.start.main[0]=[{node:'CSV 읽기',type:'main',index:0}];flow.connections['CSV 읽기']={main:[[{node:'loop',type:'main',index:0}]]};
 const result=await generate(flow);assert.equal(result.workflow.active,false);assert.equal(compileLoopPlan(result.workflow).loops.size,1);
 assert.ok(result.checklist.some(value=>value.includes('CSV 읽기')&&value.includes('실제 입력 파일')&&value.includes('업로드')&&value.includes('uploaded')&&value.includes('UTF-8')&&value.includes('8MB')));
 assert.ok(result.checklist.some(value=>value.includes('done')&&value.includes('실행 결과에서 다운로드')));
 assert.equal(result.workflow.nodes.find(node=>node.name==='CSV 읽기')!.parameters.binaryPropertyName,'uploaded');assert.equal(result.workflow.nodes.find(node=>node.name==='done')!.parameters.fileName,'처리 결과.csv');
 assert.equal(result.workflow.pinData,undefined);assert.equal(result.workflow.staticData,undefined);
});

test('AI file drafts reject unsupported parsers and encoding without executing file or workflow requests',async()=>{
 const invalid=[{operation:'docx'},{operation:'ods'},{operation:'csv',encoding:'shift_jis'},{operation:'text',options:{encoding:'euc-kr'}},{operation:'csv',options:[]},{operation:'csv',binaryPropertyName:'__easyflowBinary'}];
 for(const parameters of invalid){
  const flow=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('read','n8n-nodes-base.extractFromFile',parameters)],[['start','read']]);let calls=0;
  await assert.rejects(generateWorkflow('입력 파일을 읽는 자동화를 만들어줘',{credential,credentials:[],fetch:async(url)=>{calls++;assert.equal(url,'https://api.openai.com/v1/responses');return reply({workflow:flow});}}),/파일|UTF-8/);assert.equal(calls,1);
 }
 const convert=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('write','n8n-nodes-base.convertToFile',{operation:'pdf'})],[['start','write']]);await assert.rejects(generate(convert),/파일 형식/);
});

test('base64 extraction remains a binary operation, and advanced loop/file parameters retain secret cleaning',async()=>{
 const binary=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('read','n8n-nodes-base.extractFromFile',{operation:'binaryToProperty',binaryPropertyName:'data',destinationKey:'base64'})],[['start','read']]);
 const result=await generate(binary);assert.ok(result.checklist.some(value=>value.includes('실제 입력 파일')&&value.includes('8MB')&&!value.includes('텍스트는 UTF-8')));
 const inline=clone(binary);inline.nodes[1].parameters.options={token:'invented'};await assert.rejects(generate(inline),/비밀값이나 연결 ID/);
 const loop=loopDraft();loop.nodes[1].parameters.options={nested:{clientSecret:'invented'}};await assert.rejects(generate(loop),/비밀값이나 연결 ID/);
 const masked=loopDraft();masked.nodes[1].parameters.userNote='노트 '+credential.data.apiKey;assert.equal(JSON.stringify(await generate(masked)).includes(credential.data.apiKey),false);
});


test('XLSX and PDF drafts are available with honest file preparation guidance',async()=>{
 for(const operation of ['xlsx','pdf']){
  const flow=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('read','n8n-nodes-base.extractFromFile',{operation})],[['start','read']]);
  const result=await generate(flow);assert.equal(result.workflow.nodes[1].parameters.operation,operation);assert.ok(result.checklist.some(value=>value.includes('실제 입력 파일')&&!value.includes('텍스트는 UTF-8')));
 }
 for(const operation of ['xlsx','html','rtf']){const flow=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('write','n8n-nodes-base.convertToFile',{operation})],[['start','write']]);assert.equal((await generate(flow)).workflow.nodes[1].parameters.operation,operation);}
});

test('canvas notes are disconnected annotations and cannot carry execution edges',async()=>{
 const flow=makeFlow();flow.nodes.push(draftNode('설명','n8n-nodes-base.stickyNote',{content:'## 첫 자동화',color:1,width:240,height:160}));
 assert.equal((await generate(flow)).workflow.nodes.at(-1)!.type,'n8n-nodes-base.stickyNote');
 const loop=loopDraft();loop.nodes.push(draftNode('메모','n8n-nodes-base.stickyNote',{content:'반복 설명'}));assert.equal((await generate(loop)).workflow.nodes.at(-1)!.parameters.content,'반복 설명');
 const linked=clone(flow);linked.connections[linked.nodes[0].name].main[0].push({node:'설명',type:'main',index:0});await assert.rejects(generate(linked),/입력 경로/);
 const source=clone(flow);source.connections['설명']={main:[[{node:flow.nodes[1].name,type:'main',index:0}]]};await assert.rejects(generate(source),/출력 경로/);
 const invalid=clone(flow);invalid.nodes.at(-1)!.parameters.width=1;await assert.rejects(generate(invalid),/메모/);
});

test('AI pagination drafts preserve supported settings and reject unbounded malformed pagination',async()=>{
 const flow=draftGraph([draftNode('start','n8n-nodes-base.manualTrigger'),draftNode('pages','n8n-nodes-base.httpRequest',{url:'https://example.test/items',options:{pagination:{pagination:{paginationMode:'updateAParameterInEachRequest',parameters:{parameters:[{type:'qs',name:'page',value:'={{ $pageCount + 1 }}'}]},paginationCompleteWhen:'responseIsEmpty',limitPagesFetched:true,maxRequests:10}}}})],[['start','pages']]);
 const result=await generate(flow);assert.equal(result.workflow.nodes[1].parameters.options.pagination.pagination.maxRequests,10);
 const invalid=clone(flow);invalid.nodes[1].parameters.options.pagination.pagination.maxRequests=1001;await assert.rejects(generate(invalid),/페이지|요청/);
});

const publicAccount=(id:string,provider:Provider):CredentialPublic=>({id,name:id,provider,createdAt:'',updatedAt:''});
const apiDraft=(hook:Json={},response:Json={respondWith:'firstIncomingItem'})=>draftGraph([
 draftNode('요청 시작','n8n-nodes-base.webhook',{httpMethod:'GET',path:'customers/:id',responseMode:'responseNode',...hook}),
 draftNode('응답 만들기','n8n-nodes-base.respondToWebhook',response),
],[['요청 시작','응답 만들기']]);

test('AI API drafts bind only the account required by the selected Webhook authentication',async()=>{
 const options:[string,Provider][]=[['basicAuth','basic'],['headerAuth','header'],['bearerAuth','bearer'],['jwtAuth','jwt']];
 for(const [authentication,provider] of options){
  const expected=publicAccount('matched-'+authentication,provider),other=publicAccount('wrong-account',provider==='jwt'?'basic':'jwt');
  const result=await generate(apiDraft({authentication}),{},[other,expected]);
  assert.equal(result.workflow.nodes[0].credentialId,expected.id);assert.equal(result.workflow.nodes[1].credentialId,undefined);
  assert.ok(result.checklist.some(value=>value.includes('요청 시작')&&value.includes('자동 실행')&&value.includes('GET')));
  assert.equal(result.checklist.some(value=>value.includes('연결 계정을 선택')),false);
  assert.equal(result.workflow.active,false);assert.equal(result.workflow.hookToken,undefined);
 }
});

test('JWT draft accounts stay unbound when missing, incompatible or ambiguous, and an explicitly selected compatible public account survives',async()=>{
 const flow=apiDraft({authentication:'jwtAuth'},{respondWith:'jwt',payload:{sub:'customer-123'}});
 for(const accounts of [[],[publicAccount('wrong','bearer')],[publicAccount('a','jwt'),publicAccount('b','jwt')]]){
  const result=await generate(flow,{},accounts);assert.equal(result.workflow.nodes[0].credentialId,undefined);assert.equal(result.workflow.nodes[1].credentialId,undefined);
  for(const name of ['요청 시작','응답 만들기'])assert.ok(result.checklist.some(value=>value.includes(name)&&value.includes('JWT 연결 계정')));
 }
 const selected=clone(flow);selected.nodes[0].credentialId='b';selected.nodes[1].credentialId='not-a-jwt';
 const result=await generate(selected,{},[publicAccount('a','jwt'),publicAccount('b','jwt'),publicAccount('not-a-jwt','header')]);
 assert.equal(result.workflow.nodes[0].credentialId,'b');assert.equal(result.workflow.nodes[1].credentialId,undefined);
 assert.equal(result.checklist.some(value=>value.includes('요청 시작')&&value.includes('JWT 연결 계정')),false);
 assert.ok(result.checklist.some(value=>value.includes('응답 만들기')&&value.includes('JWT 연결 계정')));
});

test('default token and unauthenticated Webhooks and ordinary replies do not acquire unrelated accounts',async()=>{
 const accounts=[publicAccount('basic-private','basic'),publicAccount('header-private','header'),publicAccount('bearer-private','bearer'),publicAccount('jwt-private','jwt')];
 for(const authentication of [undefined,'easyflowToken','none'])for(const respondWith of ['firstIncomingItem','allIncomingItems','json','text','binary','noData','redirect']){
  const flow=apiDraft(authentication===undefined?{}:{authentication},{respondWith,...(respondWith==='binary'?{responseDataSource:'set',inputFieldName:'invoice'}:{})});flow.nodes[0].credentialId='jwt-private';flow.nodes[1].credentialId='jwt-private';
  const result=await generate(flow,{},accounts);assert.equal(result.workflow.nodes[0].credentialId,undefined);assert.equal(result.workflow.nodes[1].credentialId,undefined);
  assert.equal(result.checklist.some(value=>value.includes('연결 계정을 선택')),false);
  if(respondWith==='binary')assert.ok(result.checklist.some(value=>value.includes('실제 파일')&&value.includes('invoice')));
 }
});

test('AI receives accurate Webhook response and conditional-account schemas without saved account material',async()=>{
 let calls=0;const account=publicAccount('private-selected-jwt-id','jwt');
 const result=await generateWorkflow('API 요청을 받아 고객 결과를 JWT로 돌려줘',{credential,credentials:[account],fetch:async(url,init)=>{
  calls++;assert.equal(url,'https://api.openai.com/v1/responses');const body=JSON.parse(String(init!.body));
  for(const text of ['responseNode','options.responsePropertyName','responseBinaryPropertyName','responseKey','enableResponseOutput','payload JSON 객체','credentialConditions','bearerAuth','스트리밍 응답은 아직 지원하지'])assert.ok(body.instructions.includes(text),text);
  assert.equal(String(init!.body).includes(account.id),false);assert.equal(String(init!.body).includes(credential.data.apiKey),false);
  return reply({workflow:apiDraft({authentication:'none'},{respondWith:'jwt',payload:{sub:'customer-123'}}),checklist:[]});
 }});
 assert.equal(calls,1);assert.equal(result.workflow.nodes[0].credentialId,undefined);assert.equal(result.workflow.nodes[1].credentialId,account.id);
 assert.equal(result.checklist.some(value=>value.includes('응답 단계에서 정하기')),false);
});

test('AI API drafts reject unsupported authentication, response modes and formats before they become runnable drafts',async()=>{
 for(const hook of [{httpMethod:'OPTIONS'},{authentication:'unknownAuth'},{authentication:'={{ $json.mode }}'},{responseMode:'streaming'},{responseMode:'lastNode',responseData:'xml'},{options:[]},{responseCode:100},{options:{responseCode:{values:{responseCode:199}}}}])await assert.rejects(generate(apiDraft(hook)),/요청 방식|인증|응답|옵션/);
 for(const response of [{respondWith:'xml'},{respondWith:'={{ $json.mode }}'},{respondWith:'binary',responseDataSource:'other'},{respondWith:'json',options:[]},{respondWith:'json',enableResponseOutput:'true'},{respondWith:'json',options:{responseCode:100}},{respondWith:'json',options:{responseCode:'201'}}])await assert.rejects(generate(apiDraft({},response)),/응답|출력|파일/);
 const privateKey=apiDraft({authentication:'jwtAuth'},{respondWith:'jwt',payload:{sub:'customer'},options:{privateKey:'invented-key'}});await assert.rejects(generate(privateKey),/비밀값이나 연결 ID/);
 const wrapped=apiDraft({}, {respondWith:'json',responseBody:'{"private_key":"invented-key"}'});await assert.rejects(generate(wrapped),/비밀값이나 연결 ID/);
});

test('AI reply output branches follow the actual setting and mismatched modes add an actionable guide',async()=>{
 const flow=apiDraft({responseMode:'onReceived'},{respondWith:'json',responseBody:{message:'완료'},enableResponseOutput:true});
 flow.nodes.push(draftNode('원래 입력'),draftNode('응답 기록'));flow.connections['응답 만들기']={main:[[{node:'원래 입력',type:'main',index:0}],[{node:'응답 기록',type:'main',index:0}]]};
 const result=await generate(flow);assert.equal(result.workflow.nodes[1].parameters.enableResponseOutput,true);assert.equal(result.workflow.nodes[1].typeVersion,1.5);
 assert.ok(result.checklist.some(value=>value.includes('응답 만들기')&&value.includes('응답 단계에서 정하기')&&value.includes('실제 요청')));
 const disabled=clone(flow);disabled.nodes[1].parameters.enableResponseOutput=false;await assert.rejects(generate(disabled),/출력 경로/);
});

test('AI Webhook file drafts preserve native receive options and guide users to real request files',async()=>{
 const flow=draftGraph([
  draftNode('파일 요청','n8n-nodes-base.webhook',{httpMethod:'POST',path:'files',responseMode:'responseNode',options:{binaryData:true,rawBody:false,binaryPropertyName:'uploaded'}}),
  draftNode('CSV 읽기','n8n-nodes-base.extractFromFile',{operation:'csv',binaryPropertyName:'uploaded',encoding:'utf8'}),
  draftNode('행 돌려주기','n8n-nodes-base.respondToWebhook',{respondWith:'allIncomingItems'}),
 ],[['파일 요청','CSV 읽기'],['CSV 읽기','행 돌려주기']]);
 const result=await generate(flow);
 assert.deepEqual(result.workflow.nodes[0].parameters.options,{binaryData:true,rawBody:false,binaryPropertyName:'uploaded'});
 assert.ok(result.checklist.some(value=>value.includes('파일 요청')&&value.includes('예시 요청 보내기')&&value.includes('실제 파일')&&value.includes('uploaded')));
 assert.ok(result.checklist.some(value=>value.includes('CSV 읽기')&&value.includes('Webhook 주소')&&value.includes('UTF-8')));
 assert.equal(result.workflow.active,false);assert.equal(result.workflow.hookToken,undefined);
 const withRawBody=clone(flow);withRawBody.nodes[0].parameters.options={rawBody:true,binaryPropertyName:'unused-prefix'};
 const preserved=await generate(withRawBody);assert.equal(preserved.workflow.nodes[0].parameters.options.rawBody,true);
 assert.ok(preserved.checklist.some(value=>value.includes('원문 파일은 data')));
});

test('AI receives the real multipart, raw body, XML and request size schemas',async()=>{
 await generateWorkflow('요청으로 CSV 파일을 받아 읽어줘',{credential,credentials:[],fetch:async(_url,init)=>{
  const instructions=JSON.parse(String(init!.body)).instructions;
  for(const text of ['multipart/form-data','options.binaryData:true','options.rawBody:true','options.binaryPropertyName','data0,data1','XML(/xml,+xml)','gzip/deflate','4,000,000바이트','8MiB','예시 요청 보내기'])assert.ok(instructions.includes(text),text);
  assert.equal(instructions.includes('원본 파일 수신은 아직 지원하지'),false);
  return reply({workflow:apiDraft({httpMethod:'POST',options:{rawBody:true}})});
 }});
});

test('AI rejects malformed Webhook receive options and unsafe file names',async()=>{
 for(const options of [{binaryData:'true'},{rawBody:1},{binaryData:'={{ $json.enabled }}'},{binaryPropertyName:''},{binaryPropertyName:' '},{binaryPropertyName:'x'.repeat(121)},{binaryPropertyName:'__easyflowBinary'},{binaryPropertyName:'__proto__'},{binaryPropertyName:'constructor'},{binaryPropertyName:'bad\u0000name'},{binaryPropertyName:42}])await assert.rejects(generate(apiDraft({options})),/파일|비밀값/);
});

test('portable incoming CSV example returns the actual rows and separates original rows from the response record',async()=>{
 const flow=validateFlow(JSON.parse(readFileSync(examplePath('요청으로_파일_받기.workflow.json'),'utf8')));
 const input=withBinary({body:{customer:'예시 고객'},query:{},params:{},headers:{}},'data',createBinaryFile(Buffer.from('name,score\n김민지,92\n이준호,76\n','utf8'),{mimeType:'text/csv',fileName:'고객.csv'}));
 let response:any,calls=0;
 const result=await executeFlow(flow,[input],{mode:'live',webhookResponseMode:'responseNode',onWebhookResponse:value=>{response=value;calls++;},credential:()=>{throw new Error('이 예제는 계정을 호출하지 않습니다.');}});
 assert.equal(result.status,'success',result.error);assert.equal(calls,1);assert.equal(result.runs.length,5);
 assert.deepEqual(response,{statusCode:201,headers:{'x-easyflow-example':'csv-upload'},body:[{name:'김민지',score:'92'},{name:'이준호',score:'76'}]});
 assert.deepEqual(result.runs.find(run=>run.name==='읽은 행 확인')!.output[0],response.body);
 assert.deepEqual(result.runs.find(run=>run.name==='보낸 응답 확인')!.output[0],[{response}]);
 assert.equal(flow.active,false);assert.equal(flow.hookToken,undefined);
});
