import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,readdirSync,rmSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {executeFlow,resolveValue} from './engine';
import {runCode} from './sandbox';
import {Store} from './store';
import {makeFlow,clone,type Flow,type FlowNode,type Json,type Execution} from '../lib/workflow';
import type {RunOptions,CredentialSecret} from './contracts';
import {BINARY_KEY,createBinaryFile,getBinary,decodeBinaryFile,withBinary,jsonWithoutBinary} from './binary';
import {assertWebhookJsonSize} from './webhook-response';

const node=(id:string,type:string,parameters:Json={}):FlowNode=>({id,name:id,type,typeVersion:1,position:[0,0],parameters});
const trigger=()=>node('start','n8n-nodes-base.manualTrigger');
function graph(nodes:FlowNode[],edges:[string,string,number?,number?][]):Flow {
 const connections:Flow['connections']={};for(const [source,target,output=0,input=0] of edges){const channels=connections[source]??={main:[]};while(channels.main.length<=output)channels.main.push([]);channels.main[output].push({node:target,type:'main',index:input});}
 return {id:'test-flow',name:'실행 검증',nodes,connections,settings:{timezone:'Asia/Seoul'}};
}
const secret:CredentialSecret={id:'secret',name:'test',provider:'openai',data:{apiKey:'test-private-api-key'},createdAt:'',updatedAt:''};
const options=(extra:Partial<RunOptions>={}):RunOptions=>({mode:'learning',credential:()=>secret,...extra});
const json=(data:any,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const mock=(callback:(url:string,init:RequestInit)=>Response|Promise<Response>)=>((url:any,init:any)=>callback(String(url),init)) as typeof fetch;

test('Respond to Webhook sends first-item expression response and passes every original with exact paired provenance',async()=>{
 const start=node('start','n8n-nodes-base.webhook',{responseMode:'responseNode'}),respond=node('respond','n8n-nodes-base.respondToWebhook',{respondWith:'json',responseBody:'={{ {chosen:$("start").item.json.id, count:$input.all().length} }}',enableResponseOutput:true,options:{responseCode:201,responseHeaders:{entries:[{name:'X-Chosen',value:'={{ $json.id }}'}]}}});respond.typeVersion=1.5;
 const input=[{id:'A'},{id:'B'},{id:'C'}],received:any[]=[],flow=graph([start,respond],[['start','respond']]),result=await executeFlow(flow,input,options({onWebhookResponse:response=>{received.push(response);},webhookResponseMode:'responseNode'}));assert.equal(result.status,'success',result.error);assert.deepEqual(received,[{statusCode:201,headers:{'x-chosen':'A'},body:{chosen:'A',count:3}}]);assert.equal(result.webhookResponseSent,true);assert.deepEqual(result.runs.at(-1)?.output[0],input);assert.deepEqual(result.runs.at(-1)?.outputPairedItems,[[[{item:0}],[{item:1}],[{item:2}]],[[{item:0}]]]);assert.deepEqual(result.runs.at(-1)?.output[1],[{response:received[0]}]);
});
test('ordinary token, password and authorization data survive checkpoints and Wait without credential-based masking',async()=>{
 const wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),after=node('after','n8n-nodes-base.set',{includeOtherFields:true,assignments:{assignments:[{name:'tokenCopy',type:'string',value:'={{ $json.token }}'}]}}),flow=graph([trigger(),wait,after],[['start','wait'],['wait','after']]),input=[{token:'ordinary form token',apiKey:'ordinary app field',password:'ordinary customer field',authorization:'ordinary data',secret:'ordinary payload'}],checkpoints:Execution[]=[];
 const opts=options({onProgress:progress=>{checkpoints.push(JSON.parse(JSON.stringify(progress)));}}),pending=await executeFlow(flow,input,opts);assert.equal(pending.status,'waiting');assert.deepEqual(pending.runs[0].output[0],input);assert.deepEqual(checkpoints.at(-1)?.runs[0].output[0],input);const saved=JSON.parse(JSON.stringify(pending));saved.resumeAt=new Date(Date.now()-1).toISOString();const resumed=await executeFlow(flow,input,{...opts,resume:saved});assert.equal(resumed.status,'success',resumed.error);assert.deepEqual(resumed.output,[{...input[0],tokenCopy:input[0].token}]);
});
test('known credential values are masked under arbitrary keys and embedded text while public metadata and ordinary token fields remain',async()=>{
 const privateToken='fixture-credential-private-token',account={...secret,provider:'bearer' as const,data:{token:privateToken,algorithm:'HS256',service:'github',username:'public-account',name:'public-label',clientId:'public-client'}},http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test',authentication:'genericCredentialType',credentialId:account.id});
 const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({credential:()=>account,fetch:mock(()=>json({arbitrary:privateToken,message:'credential='+privateToken+' done',token:'ordinary token',algorithm:'HS256',service:'github',username:'public-account',name:'public-label',clientId:'public-client'}))}));assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{arbitrary:'[보호된 연결 정보]',message:'credential=[보호된 연결 정보] done',token:'ordinary token',algorithm:'HS256',service:'github',username:'public-account',name:'public-label',clientId:'public-client'}]);assert.equal(JSON.stringify(result).includes(privateToken),false);
});
test('Basic credentials protect original password, full encoded Authorization and its encoded token from execution records',async()=>{
 const account={...secret,provider:'basic' as const,data:{username:'fixture-user',password:'fixture-private-password'}},http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test',authentication:'genericCredentialType',credentialId:account.id}),encoded=Buffer.from(account.data.username+':'+account.data.password).toString('base64');
 const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({credential:()=>account,fetch:mock((_url,init)=>{assert.equal(new Headers(init.headers).get('authorization'),'Basic '+encoded);return json({arbitrary:'Basic '+encoded,encoded,message:'value='+account.data.password,username:'fixture-user'});})}));assert.equal(result.status,'success',result.error);assert.equal(JSON.stringify(result).includes(encoded),false);assert.equal(JSON.stringify(result).includes(account.data.password),false);assert.equal(result.output?.[0].username,'fixture-user');
});
test('short credentials mask exact values without corrupting execution IDs, statuses or provenance metadata',async()=>{
 for(const password of ['a','s']){
  const account={...secret,provider:'basic' as const,data:{username:'public-user',password}},http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test',authentication:'genericCredentialType',credentialId:account.id}),flow=graph([trigger(),http],[['start','http']]),executionId='a-status-id';
  const result=await executeFlow(flow,[{}],options({executionId,credential:()=>account,fetch:mock(()=>json({exact:password,message:'password='+password+'; done',token:'ordinary token'}))}));assert.equal(result.status,'success',result.error);assert.equal(result.id,executionId);assert.equal(result.workflowId,flow.id);assert.deepEqual(result.runs.map(run=>[run.nodeId,run.name,run.status]),flow.nodes.map(step=>[step.id,step.name,'success']));assert.deepEqual(result.output,[{exact:'[보호된 연결 정보]',message:'password='+password+'; done',token:'ordinary token'}]);assert.deepEqual(result.runs[1].inputRefs,[[{run:'start',output:0,item:0}]]);
 }
});
test('JWT response branch tokens remain usable while signing secrets and private credential fields stay protected',async()=>{
 const start=node('start','n8n-nodes-base.webhook'),respond=node('respond','n8n-nodes-base.respondToWebhook',{respondWith:'jwt',payload:{sub:'fixture',algorithm:'HS256'},enableResponseOutput:true,credentialId:'jwt-fixture'});respond.typeVersion=1.5;const after=node('after','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {token:$json.response.body.token,algorithm:$("start").first().json.algorithm} }}'}),account={...secret,id:'jwt-fixture',provider:'jwt' as const,data:{keyType:'passphrase',algorithm:'HS256',secret:'fixture-jwt-signing-secret'}};let token='';
 const result=await executeFlow(graph([start,respond,after],[['start','respond'],['respond','after',1]]),[{algorithm:'HS256'}],options({credential:()=>account,onWebhookResponse:response=>{token=(response.body as any).token;},webhookResponseMode:'responseNode'}));assert.equal(result.status,'success',result.error);assert.equal(result.output?.[0].token,token);assert.equal(result.output?.[0].algorithm,'HS256');assert.equal(token.split('.').length,3);assert.equal(JSON.stringify(result).includes(account.data.secret),false);
});
test('later, manual, wrong-mode and child Respond nodes ignore invalid settings without opening another response',async()=>{
 const start=node('start','n8n-nodes-base.webhook'),first=node('first','n8n-nodes-base.respondToWebhook',{respondWith:'text',responseBody:'accepted'}),later=node('later','n8n-nodes-base.respondToWebhook',{respondWith:'binary',inputFieldName:'missing',responseDataSource:'set',enableResponseOutput:true});later.typeVersion=1.5;const input=[{id:'A'},{id:'B'}],received:any[]=[],flow=graph([start,first,later],[['start','first'],['first','later']]);
 const result=await executeFlow(flow,input,options({onWebhookResponse:r=>{received.push(r);},webhookResponseMode:'responseNode'}));assert.equal(result.status,'success',result.error);assert.equal(received.length,1);assert.deepEqual(result.runs.at(-1)?.output,[input,[]]);
 const manual=await executeFlow(flow,input,options());assert.equal(manual.status,'success',manual.error);assert.equal(manual.webhookResponseSent,undefined);assert.deepEqual(manual.runs.at(-1)?.output,[input,[]]);
 const wrongMode=await executeFlow(flow,input,options({onWebhookResponse:()=>{throw new Error('unexpected');},webhookResponseMode:'lastNode'}));assert.equal(wrongMode.status,'success',wrongMode.error);
 const childStart=node('childStart','n8n-nodes-base.executeWorkflowTrigger'),child=graph([childStart,later],[['childStart','later']]);child.id='child-response';const call=node('call','n8n-nodes-base.executeWorkflow',{workflowId:child.id}),parent=graph([start,call],[['start','call']]);const nested=await executeFlow(parent,input,options({onWebhookResponse:()=>{throw new Error('child must not respond to parent');},webhookResponseMode:'responseNode',getFlow:()=>child}));assert.equal(nested.status,'success',nested.error);assert.deepEqual(nested.output,input);assert.equal(nested.webhookResponseSent,undefined);
});
test('Webhook response callback survives checkpoint Wait resumes and loop activations without retransmission',async()=>{
 const start=node('start','n8n-nodes-base.webhook'),respond=node('respond','n8n-nodes-base.respondToWebhook',{respondWith:'text',responseBody:'={{ $json.id }}'}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),later=node('later','n8n-nodes-base.respondToWebhook',{respondWith:'json',responseBody:'invalid JSON'}),flow=graph([start,respond,wait,later],[['start','respond'],['respond','wait'],['wait','later']]),received:any[]=[],opts=options({onWebhookResponse:r=>{received.push(r);},webhookResponseMode:'responseNode'}),input=[{id:'A'},{id:'B'}];
 const pending=await executeFlow(flow,input,opts);assert.equal(pending.status,'waiting');assert.equal(pending.webhookResponseSent,true);const checkpoint=JSON.parse(JSON.stringify(pending));checkpoint.resumeAt=new Date(Date.now()-1).toISOString();const resumed=await executeFlow(flow,input,{...opts,resume:checkpoint});assert.equal(resumed.status,'success',resumed.error);assert.equal(received.length,1);assert.deepEqual(resumed.output,input);
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;const loopFlow=graph([start,loop,respond,node('done','n8n-nodes-base.noOp')],[['start','loop'],['loop','respond',1],['respond','loop'],['loop','done',0]]);received.length=0;const looped=await executeFlow(loopFlow,input,opts);assert.equal(looped.status,'success',looped.error);assert.equal(received.length,1);assert.equal(received[0].body,'A');assert.deepEqual(looped.output,input);assert.equal(looped.runs.filter(r=>r.name==='respond').length,2);
});
test('Respond output and error branches preserve native version gating and pairings',async()=>{
 const start=node('start','n8n-nodes-base.webhook'),respond=node('respond','n8n-nodes-base.respondToWebhook',{respondWith:'json',responseBody:'{}',enableResponseOutput:true}),opts=options({onWebhookResponse:()=>{},webhookResponseMode:'responseNode'});for(const [version,enabled,count] of [[1,true,1],[1.2,true,1],[1.3,false,2],[1.4,false,1],[1.4,true,2],[1.5,true,2]] as const){respond.typeVersion=version;respond.parameters.enableResponseOutput=enabled;const result=await executeFlow(graph([start,respond],[['start','respond']]),[{}],opts);assert.equal(result.status,'success',result.error);assert.equal(result.runs.at(-1)?.output.length,count);}
 respond.typeVersion=1.5;respond.parameters.responseBody='{invalid';respond.onError='continueErrorOutput';const regular=node('regular','n8n-nodes-base.noOp'),response=node('response','n8n-nodes-base.noOp'),errors=node('errors','n8n-nodes-base.noOp'),failed=await executeFlow(graph([start,respond,regular,response,errors],[['start','respond'],['respond','regular',0],['respond','response',1],['respond','errors',2]]),[{id:'A'},{id:'B'}],opts);assert.equal(failed.status,'success',failed.error);assert.deepEqual(failed.runs.find(r=>r.name==='respond')?.output.map(rows=>rows.length),[0,0,2]);assert.equal(failed.webhookResponseSent,undefined);
});
test('eight megabyte binary Webhook response branches remain native Buffer JSON through later nodes and SQLite',async()=>{
 const size=8*1024*1024,file=createBinaryFile(Buffer.alloc(size,255),{mimeType:'application/octet-stream',fileName:'max.bin'}),input=[withBinary({id:'max'},'data',file)],start=node('start','n8n-nodes-base.webhook'),respond=node('respond','n8n-nodes-base.respondToWebhook',{respondWith:'binary',enableResponseOutput:true}),pass=node('pass','n8n-nodes-base.noOp');respond.typeVersion=1.5;let sent=0;const flow=graph([start,respond,pass],[['start','respond'],['respond','pass',1]]),result=await executeFlow(flow,input,options({onWebhookResponse:r=>{sent++;assert.equal(decodeBinaryFile(r.binary).length,size);},webhookResponseMode:'responseNode'}));assert.equal(result.status,'success',result.error);assert.equal(sent,1);const body=result.output?.[0].response.body;assert.equal(body.type,'Buffer');assert.equal(body.data.length,size);assert.equal(body.data[0],255);assert.equal(body.data.at(-1),255);
 const dir=mkdtempSync(join(tmpdir(),'easyflow-webhook-buffer-')),store=new Store(dir);try{store.reserveExecution(result,flow,input);const roundtrip=store.getExecution(result.id)!;assert.equal(roundtrip.output?.[0].response.body.data.length,size);assert.equal(roundtrip.runs.at(-1)?.output[0][0].response.body.data.at(-1),255);assert.deepEqual(roundtrip.runs.at(-1)?.outputPairedItems,[[[{item:0}]]]);}finally{store.close();rmSync(dir,{recursive:true,force:true});}
 assert.throws(()=>assertWebhookJsonSize([{response:{body:{type:'Buffer',data:[256]}}}]),/바이트/);assert.throws(()=>assertWebhookJsonSize([{response:{body:{type:'Buffer',data:[0],other:'x'.repeat(4_000_001)}}}]),/4MB/);assert.throws(()=>assertWebhookJsonSize([{type:'Buffer',data:new Array(size+1).fill(0)}]),/8MB/);
});

test('executeOnce makes one actual request and exposes only the applied first item to expressions and Code',async()=>{
 const sort=node('sort','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'id',order:'descending'}]}}),http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}?count={{ $input.all().length }}'}),resultNode=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {id:$("start").item.json.id} }}'});http.executeOnce=true;
 const calls:string[]=[],items=[{id:'A'},{id:'B'},{id:'C'}],flow=graph([trigger(),sort,http,resultNode],[['start','sort'],['sort','http'],['http','result']]);
 const result=await executeFlow(flow,items,options({fetch:mock(url=>{calls.push(url);return json({});})}));assert.equal(result.status,'success',result.error);assert.deepEqual(calls,['https://api.example.test/C?count=1']);assert.deepEqual(result.output,[{id:'C'}]);
 const request=result.runs.find(r=>r.name==='http')!;assert.deepEqual(request.input,[{id:'C'},{id:'B'},{id:'A'}]);assert.deepEqual(request.outputPairedItems,[[[{item:0}]]]);assert.equal(request.inputRefs?.length,3);
 const code=node('code','n8n-nodes-base.code',{mode:'runOnceForAllItems',jsCode:'return $input.all().map(item => ({json:{id:$("start").itemMatching(0).json.id,count:$input.all().length,index:$itemIndex},pairedItem:{item:0}}));'});code.executeOnce=true;
 const coded=await executeFlow(graph([trigger(),sort,code,resultNode],[['start','sort'],['sort','code'],['code','result']]),items,options());assert.equal(coded.status,'success',coded.error);assert.deepEqual(coded.output,[{id:'C'}]);assert.deepEqual(coded.runs.find(r=>r.name==='code')?.output,[[{id:'C',count:1,index:0}]]);
 code.parameters.mode='runOnceForEachItem';code.parameters.jsCode='return {json:{id:$("start").item.json.id,count:$input.all().length,index:$itemIndex}};';const each=await executeFlow(graph([trigger(),code],[['start','code']]),items,options());assert.equal(each.status,'success',each.error);assert.deepEqual(each.output,[{id:'A',count:1,index:0}]);
});
test('executeOnce preserves original local indexes for every Merge port including an empty first port',async()=>{
 const sort=node('sort','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'id',order:'descending'}]}}),merge=node('merge','n8n-nodes-base.merge',{mode:'append',numberInputs:2}),resultNode=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {id:$("start").item.json.id} }}'});merge.executeOnce=true;
 const items=[{id:'A'},{id:'B'},{id:'C'}],flow=graph([trigger(),sort,merge,resultNode],[['start','sort'],['sort','merge',0,0],['start','merge',0,1],['merge','result']]);
 const result=await executeFlow(flow,items,options());assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{id:'C'},{id:'A'}]);const run=result.runs.find(r=>r.name==='merge')!;assert.deepEqual(run.inputPortCounts,[3,3]);assert.equal(run.input.length,6);assert.deepEqual(run.outputPairedItems,[[[{item:0,input:0}],[{item:0,input:1}]]]);
 const branch=node('branch','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:1,rightValue:2,operator:{type:'number',operation:'equals'}}]}}),emptyFlow=graph([trigger(),branch,sort,merge,resultNode],[['start','branch'],['start','sort'],['branch','merge',0,0],['sort','merge',0,1],['merge','result']]);
 const empty=await executeFlow(emptyFlow,items,options());assert.equal(empty.status,'success',empty.error);assert.deepEqual(empty.output,[{id:'C'}]);assert.deepEqual(empty.runs.find(r=>r.name==='merge')?.inputPortCounts,[0,3]);assert.deepEqual(empty.runs.find(r=>r.name==='merge')?.outputPairedItems,[[[{item:0,input:1}]]]);
 const saved=JSON.parse(JSON.stringify(empty));saved.runs.find((r:any)=>r.name==='merge').inputPortCounts=[99,99];const replay=await executeFlow(emptyFlow,items,options({resume:saved}));assert.equal(replay.status,'success',replay.error);assert.deepEqual(replay.output,[{id:'C'}]);assert.deepEqual(replay.runs.find(r=>r.name==='merge')?.inputPortCounts,[0,3]);
});
test('alwaysOutputData activates an empty result and keeps all original input lineage even with executeOnce',async()=>{
 const filter=node('filter','n8n-nodes-base.filter',{conditions:{conditions:[{leftValue:1,rightValue:2,operator:{type:'number',operation:'equals'}}]}}),http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $input.all().length }}'});filter.alwaysOutputData=true;filter.executeOnce=true;
 const items=[{id:'A'},{id:'B'}],flow=graph([trigger(),filter,http],[['start','filter'],['filter','http']]);let calls=0;
 const result=await executeFlow(flow,items,options({fetch:mock(url=>{calls++;assert.equal(url,'https://api.example.test/1');return json({ok:true});})}));assert.equal(result.status,'success',result.error);assert.equal(calls,1);const run=result.runs.find(r=>r.name==='filter')!;assert.deepEqual(run.output,[[{}]]);assert.deepEqual(run.outputPairedItems,[[[{item:0,input:0},{item:1,input:0}]]]);assert.equal(run.input.length,2);
 http.parameters.url='https://api.example.test/{{ $("start").item.json.id }}';calls=0;const ambiguous=await executeFlow(flow,items,options({fetch:mock(()=>{calls++;return json({});})}));assert.equal(ambiguous.status,'error');assert.match(ambiguous.error||'',/여러 원본/);assert.equal(calls,0);
 const merge=node('merge','n8n-nodes-base.merge',{mode:'combine',combineBy:'combineByFields',fieldsToMatchString:'id',options:{joinMode:'keepMatches'}}),left=node('left','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {id:"left"} }}'}),right=node('right','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {id:"right"} }}'});merge.alwaysOutputData=true;merge.executeOnce=true;
 const multiple=await executeFlow(graph([trigger(),left,right,merge],[['start','left'],['start','right'],['left','merge',0,0],['right','merge',0,1]]),items,options());assert.equal(multiple.status,'success',multiple.error);assert.deepEqual(multiple.output,[{}]);assert.deepEqual(multiple.runs.at(-1)?.outputPairedItems,[[[{item:0,input:0},{item:1,input:0},{item:0,input:1},{item:1,input:1}]]]);
});
test('alwaysOutputData preserves other IF outputs and the last error port without reviving skipped or failed branches',async()=>{
 const branch=node('branch','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:1,rightValue:2,operator:{type:'number',operation:'equals'}}]}}),yes=node('yes','n8n-nodes-base.httpRequest',{url:'https://api.example.test/yes'}),no=node('no','n8n-nodes-base.httpRequest',{url:'https://api.example.test/no/{{ $("start").item.json.id }}'});branch.alwaysOutputData=true;branch.onError='continueErrorOutput';const calls:string[]=[],items=[{id:'A'},{id:'B'}];
 const result=await executeFlow(graph([trigger(),branch,yes,no],[['start','branch'],['branch','yes',0],['branch','no',1]]),items,options({fetch:mock(url=>{calls.push(url);return json({});})}));assert.equal(result.status,'success',result.error);assert.deepEqual(calls,['https://api.example.test/yes','https://api.example.test/no/A','https://api.example.test/no/B']);assert.deepEqual(result.runs.find(r=>r.name==='branch')?.output,[[{}],items,[]]);
 branch.alwaysOutputData=false;yes.alwaysOutputData=true;const skipped=await executeFlow(graph([trigger(),branch,yes],[['start','branch'],['branch','yes',0]]),items,options({fetch:mock(()=>{throw new Error('unreachable');})}));assert.equal(skipped.status,'success');assert.equal(skipped.runs.at(-1)?.status,'skipped');assert.deepEqual(skipped.runs.at(-1)?.output,[[]]);
 const failed=node('failed','n8n-nodes-base.httpRequest',{url:'https://api.example.test/fail'}),regular=node('regular','n8n-nodes-base.noOp'),errors=node('errors','n8n-nodes-base.noOp');failed.executeOnce=true;failed.alwaysOutputData=true;failed.onError='continueErrorOutput';let attempts=0;
 const continued=await executeFlow(graph([trigger(),failed,regular,errors],[['start','failed'],['failed','regular',0],['failed','errors',1]]),items,options({fetch:mock(()=>{attempts++;return json({error:'failure'},500);})}));assert.equal(continued.status,'success',continued.error);assert.equal(attempts,1);assert.equal(continued.runs.find(r=>r.name==='regular')?.status,'skipped');assert.equal(continued.runs.find(r=>r.name==='failed')?.output[0].length,0);assert.equal(continued.runs.find(r=>r.name==='failed')?.output[1].length,2);
 failed.onError='stopWorkflow';const stopped=await executeFlow(graph([trigger(),failed],[['start','failed']]),items,options({fetch:mock(()=>json({},500))}));assert.equal(stopped.status,'error');assert.deepEqual(stopped.runs.at(-1)?.output,[[]]);
});
test('disabled and pinned data bypass executeOnce while empty pinned results honor alwaysOutputData',async()=>{
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test'});http.disabled=true;http.executeOnce=true;const items=[{id:'A'},{id:'B'}],flow=graph([trigger(),http],[['start','http']]);
 const disabled=await executeFlow(flow,items,options({fetch:mock(()=>{throw new Error('unreachable');})}));assert.equal(disabled.status,'success');assert.deepEqual(disabled.output,items);assert.deepEqual(disabled.runs.at(-1)?.outputPairedItems,[[[{item:0}],[{item:1}]]]);
 http.disabled=false;flow.pinData={http:[{json:{id:'pinnedA'}},{json:{id:'pinnedB'}}]};const pinned=await executeFlow(flow,items,options({fetch:mock(()=>{throw new Error('unreachable');})}));assert.equal(pinned.status,'success');assert.deepEqual(pinned.output,[{id:'pinnedA'},{id:'pinnedB'}]);assert.deepEqual(pinned.runs.at(-1)?.outputPairedItems,[[[],[]]]);
 http.alwaysOutputData=true;flow.pinData.http=[];const empty=await executeFlow(flow,items,options());assert.equal(empty.status,'success');assert.deepEqual(empty.output,[{}]);assert.deepEqual(empty.runs.at(-1)?.outputPairedItems,[[[{item:0,input:0},{item:1,input:0}]]]);
 const merge=node('merge','n8n-nodes-base.merge',{mode:'append',numberInputs:2});merge.disabled=true;merge.executeOnce=true;const merged=await executeFlow(graph([trigger(),merge],[['start','merge',0,0],['start','merge',0,1]]),items,options());assert.equal(merged.status,'success',merged.error);assert.deepEqual(merged.output,items);assert.deepEqual(merged.runs.at(-1)?.inputPortCounts,[2,2]);assert.deepEqual(merged.runs.at(-1)?.outputPairedItems,[[[{item:0,input:0}],[{item:1,input:0}]]]);
});
test('executeOnce Wait and subflow resumes preserve actual applied input and never repeat prior effects',async()=>{
 const before=node('before','n8n-nodes-base.httpRequest',{url:'https://api.example.test/before/{{ $json.id }}'}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),after=node('after','n8n-nodes-base.httpRequest',{url:'https://api.example.test/after/{{ $("start").item.json.id }}'});wait.executeOnce=true;const items=[{id:'A'},{id:'B'},{id:'C'}],calls:string[]=[],opts=options({fetch:mock(url=>{calls.push(url);return json({id:new URL(url).pathname.split('/').at(-1)});})}),flow=graph([trigger(),before,wait,after],[['start','before'],['before','wait'],['wait','after']]);
 const pending=await executeFlow(flow,items,opts);assert.equal(pending.status,'waiting');assert.equal(calls.length,3);const saved=JSON.parse(JSON.stringify(pending));saved.resumeAt=new Date(Date.now()-1).toISOString();const result=await executeFlow(flow,items,{...opts,resume:saved});assert.equal(result.status,'success',result.error);assert.deepEqual(calls,['https://api.example.test/before/A','https://api.example.test/before/B','https://api.example.test/before/C','https://api.example.test/after/A']);assert.equal(result.runs.find(r=>r.name==='wait')?.input.length,3);assert.deepEqual(result.runs.find(r=>r.name==='wait')?.outputPairedItems,[[[{item:0}]]]);
 const childStart=node('childStart','n8n-nodes-base.executeWorkflowTrigger'),child=graph([childStart,before,wait],[['childStart','before'],['before','wait']]);child.id='child-once';const sub=node('sub','n8n-nodes-base.executeWorkflow',{workflowId:child.id,mode:'each'});sub.executeOnce=true;const parent=graph([trigger(),sub,after],[['start','sub'],['sub','after']]);calls.length=0;let execution=await executeFlow(parent,items,{...opts,getFlow:()=>child});assert.equal(execution.status,'waiting');assert.deepEqual(calls,['https://api.example.test/before/A']);assert.equal((Object.values(execution.subflows||{})[0] as any).jobs.length,1);
 const state=JSON.parse(JSON.stringify(execution));state.resumeAt=new Date(Date.now()-1).toISOString();for(const job of Object.values(state.subflows) as any[])job.jobs[0].execution.resumeAt=state.resumeAt;execution=await executeFlow(parent,items,{...opts,getFlow:()=>child,resume:state});assert.equal(execution.status,'success',execution.error);assert.deepEqual(calls,['https://api.example.test/before/A','https://api.example.test/after/A']);assert.equal(execution.runs.find(r=>r.name==='sub')?.input.length,3);assert.deepEqual(execution.runs.find(r=>r.name==='sub')?.outputPairedItems,[[[{item:0}]]]);
});
test('executeOnce applies per loop activation and retains exact originals after JSON checkpoint resumes',async()=>{
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:3});loop.typeVersion=3;loop.executeOnce=true;
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}'}),split=node('split','n8n-nodes-base.splitOut',{fieldToSplitOut:'copies',include:'allOtherFields',options:{destinationFieldName:'copy'}}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),done=node('done','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {id:$("start").item.json.id,copy:$json.copy} }}'});
 const flow=graph([trigger(),loop,http,split,wait,done],[['start','loop'],['loop','http',1],['http','split'],['split','wait'],['wait','loop'],['loop','done',0]]),items=[{id:'A'},{id:'B'},{id:'C'}],calls:string[]=[],opts=options({fetch:mock(url=>{calls.push(url);return json({copies:[1,2]});})});
 let execution=await executeFlow(flow,items,opts);assert.equal(execution.status,'waiting');assert.deepEqual(calls,['https://api.example.test/A']);const state=JSON.parse(JSON.stringify(execution));state.resumeAt=new Date(Date.now()-1).toISOString();execution=await executeFlow(flow,items,{...opts,resume:state});assert.equal(execution.status,'success',execution.error);assert.deepEqual(calls,['https://api.example.test/A']);assert.deepEqual(execution.output,[{id:'A',copy:1}]);assert.deepEqual(execution.runs.filter(r=>r.name==='loop').map(r=>r.input.length),[3,2]);
});
test('alwaysOutputData activates loop done on every batch with durable waits and cancellation',async()=>{
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;loop.alwaysOutputData=true;
 const body=node('body','n8n-nodes-base.httpRequest',{url:'https://api.example.test/body/{{ $json.id }}'}),doneHttp=node('doneHttp','n8n-nodes-base.httpRequest',{url:'https://api.example.test/done/{{ $json.id ?? "empty" }}'}),doneWait=node('doneWait','n8n-nodes-base.wait',{amount:1,unit:'seconds'});
 const flow=graph([trigger(),loop,body,doneHttp,doneWait],[['start','loop'],['loop','body',1],['body','loop'],['loop','doneHttp',0],['doneHttp','doneWait']]),items=[{id:'A'},{id:'B'}],calls:string[]=[],opts=options({fetch:mock(url=>{calls.push(new URL(url).pathname);return json({id:new URL(url).pathname.split('/').at(-1)});})});
 let execution=await executeFlow(flow,items,opts),resumes=0;
 while(execution.status==='waiting'&&resumes++<5){const state=JSON.parse(JSON.stringify(execution));state.resumeAt=new Date(Date.now()-1).toISOString();execution=await executeFlow(flow,items,{...opts,resume:state});}
 assert.equal(execution.status,'success',execution.error);assert.equal(resumes,3);assert.deepEqual(calls,['/done/empty','/body/A','/done/empty','/body/B','/done/A','/done/B']);assert.deepEqual(execution.output,[{id:'A'},{id:'B'}]);assert.equal(execution.runs.filter(r=>r.name==='doneHttp').length,3);assert.equal(new Set(execution.runs.filter(r=>r.name==='doneHttp').map(r=>r.executionKey)).size,3);
 calls.length=0;const only=await executeFlow(flow,items,{...opts,onlyNode:'doneHttp'});assert.equal(only.status,'success',only.error);assert.deepEqual(calls,['/done/empty']);assert.equal(only.runs.some(r=>r.name==='body'),false);
 calls.length=0;const pending=await executeFlow(flow,items,opts);assert.equal(pending.status,'waiting');const controller=new AbortController();controller.abort();const canceled=await executeFlow(flow,items,{...opts,resume:pending,signal:controller.signal});assert.equal(canceled.status,'canceled');assert.deepEqual(calls,['/done/empty']);
});
test('nested loop synthetic done feedback keeps exact originals across persisted body waits',async()=>{
 const outer=node('outer','n8n-nodes-base.splitInBatches',{batchSize:1}),inner=node('inner','n8n-nodes-base.splitInBatches',{batchSize:1,options:{reset:'={{ $json.reset ?? false }}'}});outer.typeVersion=inner.typeVersion=3;inner.alwaysOutputData=true;
 const body=node('body','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}'}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),done=node('done','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id,empty:$json.id === undefined} }}'});
 // The inner done output itself is the outer loop's sole feedback connection.
 const flow=graph([trigger(),outer,inner,body,wait,done],[['start','outer'],['outer','inner',1],['inner','body',1],['body','wait'],['wait','inner'],['inner','outer',0],['outer','done',0]]),items=[{id:'A',reset:true},{id:'B',reset:true}],calls:string[]=[],opts=options({fetch:mock(url=>{const id=new URL(url).pathname.slice(1);calls.push(id);return json({id,reset:false});})});
 let execution=await executeFlow(flow,items,opts),resumes=0;while(execution.status==='waiting'&&resumes++<5){const state=JSON.parse(JSON.stringify(execution));state.resumeAt=new Date(Date.now()-1).toISOString();execution=await executeFlow(flow,items,{...opts,resume:state});}
 assert.equal(execution.status,'success',execution.error);assert.deepEqual(calls,['A','B']);assert.equal(resumes,2);assert.deepEqual(execution.output,[{original:'A',empty:true},{original:'A',empty:false},{original:'B',empty:true},{original:'B',empty:false}]);
});
test('executeOnce retries the same first item and root trigger selection limits later effects',async()=>{
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $json.id }}'});http.executeOnce=true;http.retryOnFail=true;http.maxTries=2;http.waitBetweenTries=0;const calls:string[]=[],items=[{id:'A'},{id:'B'}];
 const retried=await executeFlow(graph([trigger(),http],[['start','http']]),items,options({fetch:mock(url=>{calls.push(url);return calls.length===1?json({},500):json({ok:true});})}));assert.equal(retried.status,'success',retried.error);assert.deepEqual(calls,['https://api.example.test/A','https://api.example.test/A']);assert.equal(retried.runs.at(-1)?.attempts,2);assert.equal(retried.runs.at(-1)?.input.length,2);
 const start=trigger();start.executeOnce=true;http.executeOnce=false;http.retryOnFail=false;calls.length=0;const rooted=await executeFlow(graph([start,http],[['start','http']]),items,options({fetch:mock(url=>{calls.push(url);return json({ok:true});})}));assert.equal(rooted.status,'success',rooted.error);assert.deepEqual(calls,['https://api.example.test/A']);assert.deepEqual(rooted.runs[0].input,items);assert.deepEqual(rooted.runs[0].output,[[items[0]]]);
});

test('expressions reference previous records with explicit items and actual current item indexes',async()=>{
 const source=node('source','n8n-nodes-base.set',{includeOtherFields:true,assignments:{assignments:[{name:'upper',type:'string',value:'={{ $json.name.trim().toUpperCase() }}'}]}});
 const resultNode=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {first: $("source").first().json.upper, last: $("source").last().json.upper, positional: $node["source"].json.upper, itemIndex: $itemIndex, run: $runIndex, current: $input.item.json.n, sum: $input.first().json.n + $input.last().json.n, id: $execution.id, mode: $execution.mode, count: $("source").all().length} }}'});
 const result=await executeFlow(graph([trigger(),source,resultNode],[['start','source'],['source','result']]),[{name:' a ',n:2},{name:' b ',n:5}],options({executionId:'expression-execution'}));
 assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{first:'A',last:'B',positional:'A',itemIndex:0,run:0,current:2,sum:7,id:'expression-execution',mode:'test',count:2},{first:'A',last:'B',positional:'B',itemIndex:1,run:0,current:5,sum:7,id:'expression-execution',mode:'test',count:2}]);
});
test('previous node default branch follows the connected IF output and skipped nodes stay unavailable',async()=>{
 const branch=node('if','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:'={{ $json.score }}',rightValue:80,operator:{type:'number',operation:'gte'}}]}});
 const yes=node('yes','n8n-nodes-base.noOp'),no=node('no','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {score: $("if").first().json.score, yesRan: $("yes").isExecuted, count: $("if").all(1).length} }}'});
 const result=await executeFlow(graph([trigger(),branch,yes,no],[['start','if'],['if','yes',0],['if','no',1]]),[{score:30}],options());
 assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{score:30,yesRan:false,count:1}]);
 assert.equal(result.runs.find(r=>r.name==='yes')?.status,'skipped');
});
test('modern linked item uses exact source and future node references are never available',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}'});
 const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{id:1}],options({fetch:mock(url=>{calls++;assert.equal(url,'https://api.example.test/1');return json({});})}));assert.equal(result.status,'success',result.error);assert.equal(calls,1);calls=0;
 const check=node('check','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {future: $("http").isExecuted ? $("http").first().json : "waiting"} }}'});
 http.parameters.url='https://api.example.test';const future=await executeFlow(graph([trigger(),check,http],[['start','check'],['check','http']]),[{}],options({onlyNode:'check',fetch:mock(()=>{calls++;return json({});})}));assert.equal(future.status,'success');assert.deepEqual(future.output,[{future:'waiting'}]);assert.equal(calls,0);
});
test('loop expressions survive durable Wait resume and never replay external effects',async()=>{
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("loop").first().json.n }}'}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'});
 const summary=node('summary','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {round: $("http").first().json.round, explicit: $("http").first(0,$runIndex).json.round, run: $runIndex, seed: $("start").first().json.n} }}'});
 const done=node('done','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {items: $input.all(), httpVisible: $("http").isExecuted} }}'});
 const flow=graph([trigger(),loop,http,wait,summary,done],[['start','loop'],['loop','http',1],['http','wait'],['wait','summary'],['summary','loop'],['loop','done',0]]),calls:number[]=[];
 const opts=options({fetch:mock(url=>{const n=Number(new URL(url).pathname.slice(1));calls.push(n);return json({round:n});})});
 let result=await executeFlow(flow,[{n:1},{n:2},{n:3}],opts),resumes=0;
 while(result.status==='waiting'&&resumes++<5){result=await executeFlow(flow,[{n:1},{n:2},{n:3}],{...opts,resume:{...result,resumeAt:new Date(Date.now()-1).toISOString()}});}
 assert.equal(result.status,'success',result.error);assert.deepEqual(calls,[1,2,3]);assert.equal(resumes,3);
 assert.deepEqual(result.output,Array.from({length:3},()=>({items:[{json:{round:1,explicit:1,run:0,seed:1}},{json:{round:2,explicit:2,run:1,seed:1}},{json:{round:3,explicit:3,run:2,seed:1}}],httpVisible:false})));
 assert.deepEqual(result.runs.filter(r=>r.name==='summary').map(r=>r.executionScope),['root/loop/batch0','root/loop/batch1','root/loop/batch2']);
});
test('previous loop batches cannot make a skipped current branch appear executed',async()=>{
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;
 const branch=node('branch','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:'={{ $json.n }}',rightValue:1,operator:{type:'number',operation:'equals'}}]}});
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test'}),merge=node('merge','n8n-nodes-base.merge',{mode:'append',numberInputs:2});
 const summary=node('summary','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {visible: $("http").isExecuted, value: $("http").isExecuted ? $("http").first().json.value : $json.n} }}'}),done=node('done','n8n-nodes-base.noOp');
 const flow=graph([trigger(),loop,branch,http,merge,summary,done],[['start','loop'],['loop','branch',1],['branch','http',0],['branch','merge',1,1],['http','merge',0,0],['merge','summary'],['summary','loop'],['loop','done',0]]);let calls=0;
 const result=await executeFlow(flow,[{n:1},{n:2}],options({fetch:mock(()=>{calls++;return json({value:11});})}));assert.equal(result.status,'success',result.error);assert.equal(calls,1);assert.deepEqual(result.output,[{visible:true,value:11},{visible:false,value:2}]);
});
test('QuickJS Code receives current execution scope, connected branch, and per-item legacy data',async()=>{
 const branch=node('branch','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:'={{ $json.n }}',rightValue:0,operator:{type:'number',operation:'lt'}}]}});
 const code=node('code','n8n-nodes-base.code',{mode:'runOnceForEachItem',jsCode:'return {json:{n: $("branch").first().json.n, positional:$node["start"].json.n, index:$itemIndex, run:$runIndex, id:$execution.id, mode:$execution.mode}};'});
 const result=await executeFlow(graph([trigger(),branch,code],[['start','branch'],['branch','code',1]]),[{n:1},{n:2}],options({executionId:'code-context'}));assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{n:1,positional:1,index:0,run:0,id:'code-context',mode:'test'},{n:1,positional:2,index:1,run:0,id:'code-context',mode:'test'}]);
});
test('subflow inputs resolve parent references but the child cannot read parent execution records',async()=>{
 const childTrigger=node('childStart','n8n-nodes-base.executeWorkflowTrigger'),childSet=node('childSet','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {value:$json.parentValue, id:$execution.id, mode:$execution.mode, run:$runIndex} }}'}),child=graph([childTrigger,childSet],[['childStart','childSet']]);child.id='child-expression';
 const parentSet=node('parentSet','n8n-nodes-base.set',{assignments:{assignments:[{name:'value',value:9,type:'number'}]}}),sub=node('sub','n8n-nodes-base.executeWorkflow',{workflowId:child.id,workflowInputs:{value:{parentValue:'={{ $("parentSet").first().json.value + $itemIndex }}'}}});
 const result=await executeFlow(graph([trigger(),parentSet,sub],[['start','parentSet'],['parentSet','sub']]),[{},{}],options({executionId:'parent-expression',getFlow:id=>id===child.id?child:undefined}));assert.equal(result.status,'success',result.error);assert.deepEqual(result.output?.map((x:Json)=>x.value),[9,10]);assert.ok(result.output?.every((x:Json)=>x.id!=='parent-expression'&&x.run===0&&x.mode==='test'));
 const scheduled=node('start','n8n-nodes-base.scheduleTrigger'),production=await executeFlow(graph([scheduled,parentSet,sub],[['start','parentSet'],['parentSet','sub']]),[{}],options({getFlow:()=>child}));assert.equal(production.status,'success',production.error);assert.equal(production.output?.[0].mode,'production');
 childSet.parameters.jsonOutput='={{ {leak:$("parentSet").isExecuted} }}';const denied=await executeFlow(graph([trigger(),sub],[['start','sub']]),[{}],options({getFlow:()=>child}));assert.equal(denied.status,'error');assert.match(denied.error||'',/이름을 찾을 수/);
});
test('engine sends independent Merge ports with more than 5000 combined inputs and handles empty choice',async()=>{
 const left=node('left','n8n-nodes-base.set',{includeOtherFields:true,assignments:{assignments:[{name:'left',value:true,type:'boolean'}]}}),right=node('right','n8n-nodes-base.set',{includeOtherFields:true,assignments:{assignments:[{name:'right',value:true,type:'boolean'}]}}),merge=node('merge','n8n-nodes-base.merge',{mode:'combine',combineBy:'combineByFields',fieldsToMatchString:'id',numberInputs:2});
 const result=await executeFlow(graph([trigger(),left,right,merge],[['start','left'],['start','right'],['left','merge',0,1],['right','merge',0,0]]),Array.from({length:3000},(_,id)=>({id})),options());assert.equal(result.status,'success',result.error);assert.equal(result.output?.length,3000);assert.deepEqual(result.output?.[1],{id:1,right:true,left:true});assert.equal(result.runs.at(-1)?.input.length,6000);assert.deepEqual(result.runs.at(-1)?.inputPortCounts,[3000,3000]);assert.equal(Object.hasOwn(result.runs[0],'inputPortCounts'),false);
 const branch=node('branch','n8n-nodes-base.if',{conditions:{conditions:[{leftValue:1,rightValue:2,operator:{type:'number',operation:'equals'}}]}}),choice=node('choice','n8n-nodes-base.merge',{mode:'chooseBranch',chooseBranchMode:'waitForAll',output:'empty',numberInputs:2});
 const emptyFlow=graph([trigger(),branch,choice],[['start','branch'],['branch','choice',0,0],['branch','choice',0,1]]),empty=await executeFlow(emptyFlow,[{}],options());assert.equal(empty.status,'success',empty.error);assert.deepEqual(empty.output,[{}]);assert.equal(empty.runs.at(-1)?.status,'success');assert.deepEqual(empty.runs.at(-1)?.inputPortCounts,[0,0]);
 const saved=clone(empty);saved.runs.at(-1)!.inputPortCounts=[99,99];saved.runs[0].inputPortCounts=[99];const replayed=await executeFlow(emptyFlow,[{}],options({resume:saved}));assert.equal(replayed.status,'success',replayed.error);assert.deepEqual(replayed.runs.at(-1)?.inputPortCounts,[0,0]);assert.equal(Object.hasOwn(replayed.runs[0],'inputPortCounts'),false);
});
test('file metadata expressions read current and previous binary while JSON stays independent',async()=>{
 const file=node('file','n8n-nodes-base.convertToFile',{operation:'csv',binaryPropertyName:'data',options:{fileName:'source.csv'}});
 const details=node('details','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {name:$binary.data.fileName, type:$input.item.binary.data.mimeType, previous:$("file").first().binary.data.fileName, legacy:$node["file"].binary.data.fileSize, reserved:$json.__easyflowBinary ?? null} }}'});
 const result=await executeFlow(graph([trigger(),file,details],[['start','file'],['file','details']]),[{name:'민지',score:92}],options());assert.equal(result.status,'success',result.error);
 const data=jsonWithoutBinary(result.output[0]);assert.equal(data.name,'source.csv');assert.equal(data.previous,'source.csv');assert.match(data.type,/csv/);assert.ok(data.legacy>0);assert.equal(data.reserved,null);assert.equal(getBinary(result.output[0]).fileName,'source.csv');
});
test('engine JSON limits count actual UTF-8 bytes before effects and after node output',async()=>{
 const text='가'.repeat(1_400_000),http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test'});let calls=0;
 const input=await executeFlow(graph([trigger(),http],[['start','http']]),[{text}],options({fetch:mock(()=>{calls++;return json({});})}));assert.equal(input.status,'error');assert.match(input.error||'',/4MB/);assert.equal(calls,0);
 const code=node('code','n8n-nodes-base.code',{jsCode:'return [];'}),output=await executeFlow(graph([trigger(),code,http],[['start','code'],['code','http']]),[{}],options({code:async()=>[{text}],fetch:mock(()=>{calls++;return json({});})}));assert.equal(output.status,'error');assert.match(output.error||'',/4MB/);assert.equal(calls,0);
});
test('causal item references survive filtering, sorting and split duplication without positional guesses',async()=>{
 const filter=node('filter','n8n-nodes-base.filter',{conditions:{conditions:[{leftValue:'={{ $json.score }}',rightValue:5,operator:{type:'number',operation:'gte'}}]}}),sort=node('sort','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'score',order:'ascending'}]}}),split=node('split','n8n-nodes-base.splitOut',{fieldToSplitOut:'children',include:'allOtherFields',options:{destinationFieldName:'child'}}),result=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id, child:$json.child, sourceScore:$("filter").item.json.score} }}'});
 const flow=graph([trigger(),filter,sort,split,result],[['start','filter'],['filter','sort'],['sort','split'],['split','result']]),execution=await executeFlow(flow,[{id:'A',score:2,children:['a']},{id:'B',score:9,children:['b']},{id:'C',score:5,children:['c0','c1']}],options());
 assert.equal(execution.status,'success',execution.error);assert.deepEqual(execution.output,[{original:'C',child:'c0',sourceScore:5},{original:'C',child:'c1',sourceScore:5},{original:'B',child:'b',sourceScore:9}]);
 assert.deepEqual(execution.runs.find(r=>r.name==='sort')?.outputPairedItems,[[[{item:1}],[{item:0}]]]);assert.deepEqual(execution.runs.find(r=>r.name==='split')?.outputPairedItems,[[[{item:0}],[{item:0}],[{item:1}]]]);
 assert.equal(JSON.stringify(execution.output).includes('inputRefs'),false);assert.equal(JSON.stringify(execution.output).includes('pairedItem'),false);
});
test('Merge matched branches deduplicate the same ancestor while mismatched originals remain ambiguous',async()=>{
 const left=node('left','n8n-nodes-base.noOp'),right=node('right','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'id',order:'descending'}]}}),merge=node('merge','n8n-nodes-base.merge',{mode:'combine',combineBy:'combineByFields',fieldsToMatchString:'id',numberInputs:2}),result=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id, left:$("left").item.json.id, right:$("right").item.json.id} }}'});
 const flow=graph([trigger(),left,right,merge,result],[['start','left'],['start','right'],['left','merge',0,0],['right','merge',0,1],['merge','result']]),items=[{id:'A'},{id:'B'}];
 const matched=await executeFlow(flow,items,options());assert.equal(matched.status,'success',matched.error);assert.deepEqual(matched.output,[{original:'A',left:'A',right:'A'},{original:'B',left:'B',right:'B'}]);
 merge.parameters.combineBy='combineByPosition';const ambiguous=await executeFlow(flow,items,options());assert.equal(ambiguous.status,'error');assert.match(ambiguous.error||'',/여러 원본/);
});
test('Aggregate reports multiple originals explicitly and modern ambiguity stops later HTTP effects',async()=>{
 const aggregate=node('aggregate','n8n-nodes-base.aggregate',{aggregate:'aggregateAllItemData',destinationFieldName:'data'}),http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}'});let calls=0;
 const execution=await executeFlow(graph([trigger(),aggregate,http],[['start','aggregate'],['aggregate','http']]),[{id:'A'},{id:'B'}],options({fetch:mock(()=>{calls++;return json({});})}));assert.equal(execution.status,'error');assert.match(execution.error||'',/여러 원본/);assert.equal(calls,0);assert.deepEqual(execution.runs[1].outputPairedItems,[[[{item:0},{item:1}]]]);
});
test('loop causal references keep every original through durable Wait and done can read historical body results',async()=>{
 const loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/{{ $("start").item.json.id }}'}),wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'}),body=node('body','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id, batch:$("loop").item.json.id, changed:$("http").item.json.changed} }}'}),done=node('done','n8n-nodes-base.code',{mode:'runOnceForEachItem',jsCode:'return {json:{original:$("start").item.json.id, body:$("body").item.json.original, changed:$("http").item.json.changed}};'});
 const flow=graph([trigger(),loop,http,wait,body,done],[['start','loop'],['loop','http',1],['http','wait'],['wait','body'],['body','loop'],['loop','done',0]]),items=[{id:'A'},{id:'B'},{id:'C'}],calls:string[]=[];
 const opts=options({fetch:mock(url=>{calls.push(new URL(url).pathname.slice(1));return json({changed:true});})});let execution=await executeFlow(flow,items,opts),resumes=0;
 while(execution.status==='waiting'&&resumes++<5){const durable=JSON.parse(JSON.stringify(execution));durable.resumeAt=new Date(Date.now()-1).toISOString();execution=await executeFlow(flow,items,{...opts,resume:durable});}
 assert.equal(execution.status,'success',execution.error);assert.deepEqual(calls,['A','B','C']);assert.equal(resumes,3);assert.deepEqual(execution.output,[{original:'A',body:'A',changed:true},{original:'B',body:'B',changed:true},{original:'C',body:'C',changed:true}]);
 assert.ok(execution.runs.filter(r=>r.name==='loop').every(r=>r.inputRefs&&r.outputSourceRefs));
});
test('QuickJS identity reorder, explicit fresh pairing and unknown fresh outputs retain causal truth',async()=>{
 const code=node('code','n8n-nodes-base.code',{mode:'runOnceForAllItems',jsCode:'return $input.all().reverse();'}),result=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id, current:$json.id} }}'}),flow=graph([trigger(),code,result],[['start','code'],['code','result']]);
 const items=[{id:'A'},{id:'B'},{id:'C'}],reversed=await executeFlow(flow,items,options());assert.equal(reversed.status,'success',reversed.error);assert.deepEqual(reversed.output,[{original:'C',current:'C'},{original:'B',current:'B'},{original:'A',current:'A'}]);
 code.parameters.jsCode='return [{json:{id:"new"},pairedItem:{item:2}}];';const explicit=await executeFlow(flow,items,options());assert.equal(explicit.status,'success',explicit.error);assert.deepEqual(explicit.output,[{original:'C',current:'new'}]);
 code.parameters.jsCode='return [{json:{id:"X"}},{json:{id:"Y"}},{json:{id:"Z"}}];';const automatic=await executeFlow(flow,items,options());assert.equal(automatic.status,'success',automatic.error);assert.deepEqual(automatic.output,[{original:'A',current:'X'},{original:'B',current:'Y'},{original:'C',current:'Z'}]);
 code.parameters.jsCode='return [{json:{id:"X"}},{json:{id:"Y"}},{json:{id:"Z"}},{json:{id:"W"}}];';const unknown=await executeFlow(flow,items,options());assert.equal(unknown.status,'error');assert.match(unknown.error||'',/출처/);assert.deepEqual(unknown.runs[1].outputPairedItems,[[[],[],[],[]]]);
});
test('subflow reorder and aggregation propagate exact parent origins rather than returned positions',async()=>{
 const childStart=node('childStart','n8n-nodes-base.executeWorkflowTrigger'),sort=node('sort','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'id',order:'descending'}]}}),child=graph([childStart,sort],[['childStart','sort']]);child.id='paired-child';
 const sub=node('sub','n8n-nodes-base.executeWorkflow',{workflowId:child.id}),result=node('result','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {original:$("start").item.json.id, current:$json.id} }}'}),flow=graph([trigger(),sub,result],[['start','sub'],['sub','result']]);
 const reordered=await executeFlow(flow,[{id:'A'},{id:'B'}],options({getFlow:()=>child}));assert.equal(reordered.status,'success',reordered.error);assert.deepEqual(reordered.output,[{original:'B',current:'B'},{original:'A',current:'A'}]);assert.deepEqual(reordered.runs[1].outputPairedItems,[[[{item:1}],[{item:0}]]]);
 child.nodes=[childStart,node('aggregate','n8n-nodes-base.aggregate',{aggregate:'aggregateAllItemData',destinationFieldName:'data'})];child.connections={childStart:{main:[[{node:'aggregate',type:'main',index:0}]]}};
 const combined=await executeFlow(flow,[{id:'A'},{id:'B'}],options({getFlow:()=>child}));assert.equal(combined.status,'error');assert.match(combined.error||'',/여러 원본/);
});
test('Code-context ancestry budget bounds duplicate cached edges before any following request',async()=>{
 const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test'}),flow=graph([trigger(),http],[['start','http']]),saved=await executeFlow(flow,[{id:1}],options({onlyNode:'start'}));let calls=0;
 saved.runs[0].outputSourceRefs=[[Array.from({length:200001},()=>({run:'start',side:'input',output:0,item:0}))]];
 const execution=await executeFlow(flow,[{id:1}],options({resume:saved,fetch:mock(()=>{calls++;return json({});})}));assert.equal(execution.status,'error');assert.match(execution.error||'',/추적 한도/);assert.equal(calls,0);
});

test('standalone engine runs the default template with actual fields and numeric IF',async()=>{
 const result=await executeFlow(makeFlow(),[{}],options());assert.equal(result.status,'success');assert.equal(result.runs.length,4);assert.equal(result.runs[3].output[0][0].score,92);
});
test('raw Set resolves each input item and preserves input fields when requested',async()=>{
 const flow=graph([trigger(),node('set','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ $json }}',includeOtherFields:true})],[['start','set']]);
 const result=await executeFlow(flow,[{name:'A'},{name:'B'}],options());assert.equal(result.status,'success');assert.deepEqual(result.runs[1].output,[[{name:'A'},{name:'B'}]]);
 assert.equal(resolveValue('={{ $json.records[0].name }}',{records:[{name:'A'}]}),'A');assert.equal(resolveValue('고객 {{ $json.name }}',{name:'민지'}),'고객 민지');
});
test('IF honors strict types and Merge appends in target input order including empty branches',async()=>{
 const branch=node('branch','n8n-nodes-base.if',{conditions:{options:{typeValidation:'strict'},conditions:[{leftValue:'={{ $json.score }}',rightValue:80,operator:{type:'number',operation:'gte'}}],combinator:'and'}});
 const merge=node('merge','n8n-nodes-base.merge',{mode:'append',numberInputs:2});
 const flow=graph([trigger(),branch,merge],[['start','branch'],['branch','merge',0,1],['branch','merge',1,0]]);
 const result=await executeFlow(flow,[{name:'A',score:92},{name:'B',score:68}],options());assert.equal(result.status,'success');assert.deepEqual(result.runs[2].output[0].map(v=>v.name),['B','A']);
 const empty=await executeFlow(flow,[{name:'A',score:92}],options());assert.equal(empty.status,'success');assert.deepEqual(empty.runs[2].output[0].map(v=>v.name),['A']);
 const wrong=await executeFlow(flow,[{score:'92'}],options());assert.equal(wrong.status,'error');assert.match(wrong.error||'',/종류/);
});
test('learning mode never calls AI, Slack or HTTP write methods',async()=>{
 let calls=0;const request=mock(()=>{calls++;return json({ok:true});});
 for(const blocked of [node('blocked','@n8n/n8n-nodes-langchain.openAi',{modelId:{value:'test-model'},credentialId:'secret'}),node('blocked','n8n-nodes-base.slack',{channelId:{value:'C123'},text:'hello',credentialId:'secret'}),node('blocked','n8n-nodes-base.httpRequest',{method:'POST',url:'https://api.example.test/data'})]){
  const result=await executeFlow(graph([trigger(),blocked],[['start','blocked']]),[{}],options({fetch:request}));assert.equal(result.status,'error');assert.match(result.error||'',/연습 모드/);
 }
 assert.equal(calls,0);
});
test('HTTP applies query/headers/body and selected bearer credential directly',async()=>{
 let captured:{url:string;init:RequestInit}|undefined;
 const http=node('http','n8n-nodes-base.httpRequest',{method:'POST',url:'https://api.example.test/data',authentication:'genericCredentialType',credentialId:'secret',sendQuery:true,queryParameters:{parameters:[{name:'search',value:'={{ $json.name }}'}]},sendHeaders:true,headerParameters:{parameters:[{name:'X-Context',value:'={{ $json.context }}'}]},sendBody:true,specifyBody:'json',jsonBody:'{"name":"{{ $json.name }}"}',options:{timeout:1000}});
 const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{name:'민지',context:'request'}],options({mode:'live',credential:()=>({...secret,provider:'bearer',data:{token:'test-private-token'}}),fetch:mock((url,init)=>{captured={url,init};return json({done:true,token:'test-private-token'});})}));
 assert.equal(result.status,'success');assert.equal(new URL(captured!.url).searchParams.get('search'),'민지');assert.equal(new Headers(captured!.init.headers).get('Authorization'),'Bearer test-private-token');assert.equal(new Headers(captured!.init.headers).get('X-Context'),'request');assert.deepEqual(JSON.parse(String(captured!.init.body)),{name:'민지'});assert.ok(!JSON.stringify(result).includes('test-private-token'));
});
test('HTTP ignores body when sendBody is false and supports Basic/Header auth and text responses',async()=>{
 for(const [provider,data,expectedHeader,expectedValue] of [['basic',{username:'account',password:'secret-password'},'Authorization',`Basic ${Buffer.from('account:secret-password').toString('base64')}`],['header',{name:'X-API-Key',value:'private-header-key'},'X-API-Key','private-header-key']] as const){
  const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data',method:'GET',authentication:'genericCredentialType',credentialId:'secret',sendBody:false,jsonBody:'{"accidental":true}',options:{response:{response:{responseFormat:'text'}}}});
  const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({credential:()=>({...secret,provider,data}),fetch:mock((_url,init)=>{assert.equal(init.body,undefined);assert.equal(new Headers(init.headers).get(expectedHeader),expectedValue);return new Response('actual text');})}));
  assert.equal(result.status,'success');assert.deepEqual(result.runs[1].output,[[{data:'actual text'}]]);
 }
});
test('JSON body expressions keep object types and safely interpolate quoted text',async()=>{
 for(const body of ['={{ $json.payload }}','{"text":"{{ $json.text }}"}']){
  const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data',method:'POST',sendBody:true,specifyBody:'json',jsonBody:body});
  const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{payload:{text:'"quoted"'},text:'"quoted"'}],options({mode:'live',fetch:mock((_url,init)=>{assert.deepEqual(JSON.parse(String(init.body)),{text:'"quoted"'});return json({done:true});})}));assert.equal(result.status,'success');
 }
});
test('Wait persists its deadline and resume replays completed external effects',async()=>{
 let calls=0,checkpoints:Execution[]=[];
 const flow=graph([trigger(),node('post','n8n-nodes-base.httpRequest',{url:'https://api.example.test/action',method:'POST'}),node('wait','n8n-nodes-base.wait',{amount:10,unit:'seconds',resume:'timeInterval'}),node('result','n8n-nodes-base.set',{assignments:{assignments:[{name:'done',type:'boolean',value:true}]}})],[['start','post'],['post','wait'],['wait','result']]);
 const opts=options({mode:'live',fetch:mock(()=>{calls++;return json({accepted:true});}),onProgress:e=>{checkpoints.push(clone(e));}});
 const waiting=await executeFlow(flow,[{}],opts);assert.equal(waiting.status,'waiting');assert.equal(waiting.pendingNodeId,'wait');assert.equal(waiting.runs.length,2);assert.equal(calls,1);assert.ok(checkpoints.some(e=>e.status==='waiting'));
 const early=await executeFlow(flow,[{}],{...opts,resume:waiting});assert.equal(early.status,'waiting');assert.equal(early.resumeAt,waiting.resumeAt);assert.equal(calls,1);
 const resumed=await executeFlow(flow,[{}],{...opts,resume:{...waiting,resumeAt:new Date(Date.now()-1).toISOString()}});assert.equal(resumed.status,'success');assert.equal(calls,1);assert.equal(resumed.runs.length,4);assert.deepEqual(resumed.runs[3].output,[[{done:true}]]);
});
test('OpenAI uses direct Responses API and extracts text after reasoning items',async()=>{
 let captured:Json={};const ai=node('ai','@n8n/n8n-nodes-langchain.openAi',{resource:'text',operation:'message',credentialId:'secret',modelId:{value:'user-selected-model'},messages:{values:[{role:'system',content:'세 줄 요약'},{role:'user',content:'={{ $json.content }}'}]}});
 const result=await executeFlow(graph([trigger(),ai],[['start','ai']]),[{content:'실제 입력'}],options({mode:'live',fetch:mock((url,init)=>{assert.equal(url,'https://api.openai.com/v1/responses');assert.equal(new Headers(init.headers).get('Authorization'),'Bearer test-private-api-key');captured=JSON.parse(String(init.body));return json({status:'completed',output:[{type:'reasoning',summary:[]},{type:'message',content:[{type:'output_text',text:'요약 결과'}]}],usage:{total_tokens:12}});})}));
 assert.equal(result.status,'success');assert.equal(captured.model,'user-selected-model');assert.equal(captured.store,false);assert.equal(captured.instructions,'세 줄 요약');assert.equal(result.runs[1].output[0][0].message.content,'요약 결과');assert.ok(!JSON.stringify(result).includes(secret.data.apiKey));
});
test('AI Agent performs only registered read-only URL tools and returns actual text',async()=>{
 let aiCalls=0,toolCalls=0;const agent=node('agent','@n8n/n8n-nodes-langchain.agent',{credentialId:'secret',model:'user-selected-model',text:'={{ $json.prompt }}',tools:[{name:'read_status',url:'https://api.example.test/status',method:'GET'}]});
 const result=await executeFlow(graph([trigger(),agent],[['start','agent']]),[{prompt:'상태 확인'}],options({mode:'live',fetch:mock((url,init)=>{
  if(url==='https://api.example.test/status'){toolCalls++;assert.equal(init.method,'GET');assert.equal(new Headers(init.headers).has('Authorization'),false);return json({status:'ready'});}
  aiCalls++;const body=JSON.parse(String(init.body));assert.equal(body.tools[0].type,'function');assert.equal(body.tools[0].name,'read_status');
  if(aiCalls===1)return json({output:[{type:'function_call',name:'read_status',call_id:'call1',arguments:'{}'}]});
  assert.ok(body.input.some((v:any)=>v.type==='function_call_output'&&v.call_id==='call1'));return json({output:[{type:'message',content:[{type:'output_text',text:'준비됨'}]}]});
 })}));assert.equal(result.status,'success');assert.equal(aiCalls,2);assert.equal(toolCalls,1);assert.equal(result.runs[1].output[0][0].toolCalls,1);
});
test('AI Agent refuses model-selected arbitrary URL arguments',async()=>{
 let calls=0;const agent=node('agent','@n8n/n8n-nodes-langchain.agent',{credentialId:'secret',model:'test-model',text:'hello',tools:[{name:'read_status',url:'https://api.example.test/status'}]});
 const result=await executeFlow(graph([trigger(),agent],[['start','agent']]),[{}],options({mode:'live',fetch:mock(()=>{calls++;return json({output:[{type:'function_call',name:'read_status',call_id:'call1',arguments:'{"url":"http://other.test"}'}]});})}));assert.equal(result.status,'error');assert.equal(calls,1);assert.match(result.error||'',/임의의 주소/);
});
test('Slack checks JSON ok even when HTTP status is 200',async()=>{
 const slack=node('slack','n8n-nodes-base.slack',{resource:'message',operation:'post',credentialId:'secret',channelId:{value:'C123'},text:'={{ $json.text }}'});
 const result=await executeFlow(graph([trigger(),slack],[['start','slack']]),[{text:'실제 입력'}],options({mode:'live',credential:()=>({...secret,provider:'slack',data:{token:'test-slack-token'}}),fetch:mock((url,init)=>{assert.equal(url,'https://slack.com/api/chat.postMessage');assert.deepEqual(JSON.parse(String(init.body)),{channel:'C123',text:'실제 입력'});return json({ok:false,error:'not_in_channel'});})}));assert.equal(result.status,'error');assert.match(result.error||'',/초대/);
});
test('cycles and unsupported nodes fail before external effects; cancellation is recorded',async()=>{
 let calls=0;const request=mock(()=>{calls++;return json({});});const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data'});
 const cycle=graph([trigger(),http,node('set','n8n-nodes-base.set',{})],[['start','http'],['http','set'],['set','http']]);assert.equal((await executeFlow(cycle,[{}],options({fetch:request}))).status,'error');
 const unsupported=graph([trigger(),http,node('unknown','other.custom-node')],[['start','http'],['http','unknown']]);assert.equal((await executeFlow(unsupported,[{}],options({fetch:request}))).status,'error');assert.equal(calls,0);
 const controller=new AbortController();controller.abort();const canceled=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({signal:controller.signal,fetch:request}));assert.equal(canceled.status,'canceled');assert.equal(calls,0);
});
test('retryOnFail retries actual HTTP failures up to maxTries with the configured delay',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data'});http.retryOnFail=true;http.maxTries=3;http.waitBetweenTries=10;
 const start=Date.now(),result=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({fetch:mock(()=>json(++calls<3?{error:'temporary'}:{done:true},calls<3?503:200))}));assert.equal(result.status,'success');assert.equal(calls,3);assert.equal((result.runs[1] as any).attempts,3);assert.ok(Date.now()-start>=15);assert.deepEqual(result.runs[1].output,[[{done:true}]]);
 calls=0;http.maxTries=2;const failure=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({fetch:mock(()=>{calls++;return json({},503);})}));assert.equal(failure.status,'error');assert.equal(calls,2);
});
test('cancellation interrupts retry delay and is never swallowed by continueOnFail',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data'});http.retryOnFail=true;http.maxTries=3;http.waitBetweenTries=1000;http.continueOnFail=true;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20);try{const result=await executeFlow(graph([trigger(),http],[['start','http']]),[{}],options({signal:controller.signal,fetch:mock(()=>{calls++;return json({},503);})}));assert.equal(result.status,'canceled');assert.equal(calls,1);}finally{clearTimeout(timer);}
});
test('continued failures are honest records and are not repeated when a later Wait resumes',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data'});http.onError='continueRegularOutput';const wait=node('wait','n8n-nodes-base.wait',{amount:1,unit:'seconds'});const flow=graph([trigger(),http,wait],[['start','http'],['http','wait']]),opts=options({fetch:mock(()=>{calls++;return json({},503);})});
 const waiting=await executeFlow(flow,[{id:1}],opts);assert.equal(waiting.status,'waiting');assert.equal(waiting.runs[1].status,'error');assert.equal((waiting.runs[1] as any).continued,true);assert.equal(waiting.runs[1].output[0][0].id,1);assert.match(waiting.runs[1].output[0][0].error,/HTTP 503/);
 const result=await executeFlow(flow,[{id:1}],{...opts,resume:{...waiting,resumeAt:new Date(Date.now()-1).toISOString()}});assert.equal(result.status,'success');assert.equal(calls,1);
});
test('continueErrorOutput uses the last port after every normal Switch output',async()=>{
 const rule=(value:number)=>({conditions:{options:{typeValidation:'strict'},conditions:[{leftValue:'={{ $json.score }}',rightValue:value,operator:{type:'number',operation:'gte'}}],combinator:'and'}});
 const split=node('split','n8n-nodes-base.switch',{mode:'rules',rules:{values:[rule(80),rule(60)]},options:{fallbackOutput:'extra'}});split.onError='continueErrorOutput';const a=node('A','n8n-nodes-base.noOp'),b=node('B','n8n-nodes-base.noOp'),fallback=node('fallback','n8n-nodes-base.noOp'),handler=node('handler','n8n-nodes-base.noOp');
 const flow=graph([trigger(),split,a,b,fallback,handler],[['start','split'],['split','A',0],['split','B',1],['split','fallback',2],['split','handler',3]]);
 const result=await executeFlow(flow,[{score:'wrong'}],options());assert.equal(result.status,'success');assert.equal(result.runs[1].status,'error');assert.equal(result.runs[1].output.length,4);assert.deepEqual(result.runs[1].output.slice(0,3),[[],[],[]]);assert.equal(result.runs.at(-1)?.name,'handler');assert.match(result.runs.at(-1)?.output[0][0].error,/종류/);assert.ok(result.runs.filter(r=>['A','B','fallback'].includes(r.name)).every(r=>r.status==='skipped'));
 const success=await executeFlow(flow,[{score:90}],options());assert.equal(success.status,'success');assert.deepEqual(success.runs[1].output[3],[]);assert.equal(success.runs.at(-1)?.status,'skipped');assert.deepEqual(success.output,[{score:90}]);
});
test('sub-workflows execute saved snapshots, map inputs, return real child outputs and cap recursion',async()=>{
 const child=graph([node('child-start','n8n-nodes-base.executeWorkflowTrigger'),node('child-result','n8n-nodes-base.code',{jsCode:'return $input.all().map(item=>({json:{value:item.json.value*2,label:item.json.label}}));'})],[['child-start','child-result']]);child.id='child';
 const call=node('call','n8n-nodes-base.executeWorkflow',{source:'database',workflowId:{value:'child'},mode:'each',workflowInputs:{value:{label:'{{ $json.name }}'}},options:{waitForSubWorkflow:true}}),parent=graph([trigger(),call],[['start','call']]);
 const result=await executeFlow(parent,[{value:2,name:'A'},{value:3,name:'B'}],options({getFlow:id=>id==='child'?child:undefined}));assert.equal(result.status,'success');assert.deepEqual(result.runs[1].output,[[{value:4,label:'A'},{value:6,label:'B'}]]);assert.equal(result.subflows.call.jobs.length,2);
 call.parameters.workflowId={value:parent.id};const recursive=await executeFlow(parent,[{}],options({getFlow:()=>parent}));assert.equal(recursive.status,'error');assert.match(recursive.error||'',/8단계/);
});
test('sub-workflow Wait checkpoints survive parent resume without repeating child HTTP effects',async()=>{
 let calls=0;const child=graph([node('child-start','n8n-nodes-base.executeWorkflowTrigger'),node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/action',method:'POST'}),node('child-wait','n8n-nodes-base.wait',{amount:20,unit:'seconds'}),node('child-result','n8n-nodes-base.noOp')],[['child-start','http'],['http','child-wait'],['child-wait','child-result']]);child.id='child';
 const call=node('call','n8n-nodes-base.executeWorkflow',{source:'database',workflowId:{value:'child'},mode:'once',options:{waitForSubWorkflow:true}}),parent=graph([trigger(),call,node('result','n8n-nodes-base.noOp')],[['start','call'],['call','result']]);const opts=options({mode:'live',getFlow:()=>child,fetch:mock(()=>{calls++;return json({accepted:true});})});
 const waiting=await executeFlow(parent,[{}],opts);assert.equal(waiting.status,'waiting');assert.equal(waiting.pendingNodeId,'call');assert.equal(waiting.subflows.call.jobs[0].execution.status,'waiting');assert.equal(calls,1);assert.equal(waiting.runs.length,1);
 const resume=clone(waiting);resume.resumeAt=new Date(Date.now()-1).toISOString();resume.subflows.call.jobs[0].execution.resumeAt=resume.resumeAt;
 const changedChild=clone(child);changedChild.nodes.at(-1)!.type='unknown.changed-node';const result=await executeFlow(parent,[{}],{...opts,resume,getFlow:()=>changedChild});assert.equal(result.status,'success');assert.equal(calls,1);assert.deepEqual(result.output,[{accepted:true}]);assert.equal(result.subflows.call.jobs[0].execution.runs.length,4);
});
test('pinned AI data is used only in learning mode and live mode always calls the real service',async()=>{
 let calls=0;const ai=node('ai','@n8n/n8n-nodes-langchain.openAi',{model:'test-model',credentialId:'secret',text:'hello'}),flow=graph([trigger(),ai],[['start','ai']]);flow.pinData={ai:[{json:{text:'고정 예시'}}]};const opts=options({fetch:mock(()=>{calls++;return json({output:[{type:'message',content:[{type:'output_text',text:'실제 응답'}]}]});})});
 const learning=await executeFlow(flow,[{}],opts);assert.equal(learning.status,'success');assert.equal(calls,0);assert.equal((learning.runs[1] as any).pinned,true);assert.deepEqual(learning.runs[1].output,[[{text:'고정 예시'}]]);
 const live=await executeFlow(flow,[{}],{...opts,mode:'live'});assert.equal(live.status,'success');assert.equal(calls,1);assert.equal((live.runs[1] as any).pinned,undefined);assert.equal(live.runs[1].output[0][0].text,'실제 응답');
});
test('errorTrigger passes the own runtime error-workflow payload through',async()=>{
 const flow=graph([node('error-start','n8n-nodes-base.errorTrigger'),node('result','n8n-nodes-base.noOp')],[['error-start','result']]),input=[{execution:{id:'failed-run',error:{message:'요청 실패'}},workflow:{id:'source',name:'원래 자동화'}}];const result=await executeFlow(flow,input,options());assert.equal(result.status,'success');assert.deepEqual(result.output,input);
});
test('sticky annotations render as metadata and never consume execution records or step limits',async()=>{
 const note=node('note','n8n-nodes-base.stickyNote',{content:'설명 메모',width:300,height:200}),flow=graph([trigger(),note,node('end','n8n-nodes-base.noOp')],[['start','end']]);
 const result=await executeFlow(flow,[{value:1}],options({maxSteps:2}));assert.equal(result.status,'success',result.error);assert.deepEqual(result.runs.map(run=>run.nodeId),['start','end']);assert.deepEqual(result.output,[{value:1}]);
 const annotationOnly=await executeFlow(flow,[{value:1}],options({onlyNode:'note'}));assert.equal(annotationOnly.status,'error');assert.equal(annotationOnly.runs.length,0);assert.match(annotationOnly.error||'',/메모/);
});
test('edges into or out of sticky annotations fail before any HTTP effect',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://example.test/write',method:'POST'}),note=node('note','n8n-nodes-base.stickyNote',{content:'메모'});
 for(const edge of [['http','note'],['note','http']] as [string,string][]){const result=await executeFlow(graph([trigger(),http,note],[['start','http'],edge]),[{}],options({mode:'live',fetch:mock(()=>{calls++;return json({});})}));assert.equal(result.status,'error');assert.match(result.error||'',/번호/);assert.equal(calls,0);}
});
test('invalid source and target port indexes fail before any HTTP effect',async()=>{
 let calls=0;const http=node('http','n8n-nodes-base.httpRequest',{url:'https://api.example.test/action',method:'POST'}),end=node('end','n8n-nodes-base.noOp'),opts=options({mode:'live',fetch:mock(()=>{calls++;return json({});})});
 for(const edge of [['http','end',2,0],['http','end',0,1]] as [string,string,number,number][]){const result=await executeFlow(graph([trigger(),http,end],[['start','http'],edge]),[{}],opts);assert.equal(result.status,'error');assert.match(result.error||'',/번호/);assert.equal(calls,0);}
});
test('new data-node chain executes through the engine and stops with configured error',async()=>{
 const split=node('split','n8n-nodes-base.splitOut',{fieldToSplitOut:'items'}),filter=node('filter','n8n-nodes-base.filter',{conditions:{options:{typeValidation:'strict'},conditions:[{leftValue:'={{ $json.score }}',rightValue:60,operator:{type:'number',operation:'gte'}}],combinator:'and'}}),dedupe=node('dedupe','n8n-nodes-base.removeDuplicates',{operation:'removeDuplicateInputItems',compare:'allFields'}),sort=node('sort','n8n-nodes-base.sort',{type:'simple',sortFieldsUi:{sortField:[{fieldName:'score',order:'descending'}]}}),limit=node('limit','n8n-nodes-base.limit',{maxItems:1,keep:'lastItems'}),aggregate=node('aggregate','n8n-nodes-base.aggregate',{aggregate:'aggregateAllItemData',destinationFieldName:'records',include:'allFields'});
 const flow=graph([trigger(),split,filter,dedupe,sort,limit,aggregate],[['start','split'],['split','filter'],['filter','dedupe'],['dedupe','sort'],['sort','limit'],['limit','aggregate']]);const result=await executeFlow(flow,[{items:[{name:'A',score:90},{name:'A',score:90},{name:'B',score:50},{name:'C',score:80}]}],options());assert.equal(result.status,'success');assert.ok(result.runs.every(r=>r.status==='success'));assert.deepEqual(result.output,[{records:[{name:'C',score:80}]}]);
 const stop=node('stop','n8n-nodes-base.stopAndError',{errorType:'errorMessage',errorMessage:'입력 확인이 필요해요.'});const stopped=await executeFlow(graph([trigger(),stop],[['start','stop']]),[{}],options());assert.equal(stopped.status,'error');assert.equal(stopped.error,'입력 확인이 필요해요.');
});
test('connector dispatch uses the own API request and preserves learning write restrictions',async()=>{
 let calls=0;const github=node('github','easyflow.github',{operation:'listIssues',owner:'example',repository:'repo',limit:1,credentialId:'secret'}),flow=graph([trigger(),github],[['start','github']]);const opts=options({credential:()=>({...secret,provider:'bearer',service:'github',data:{token:'private-github-token'}}),fetch:mock((url,init)=>{calls++;assert.ok(url.startsWith('https://api.github.com/repos/example/repo/issues'));assert.equal(new Headers(init.headers).get('Authorization'),'Bearer private-github-token');return json([{id:1,title:'예시 이슈'}]);})});const result=await executeFlow(flow,[{}],opts);assert.equal(result.status,'success');assert.equal(calls,1);assert.deepEqual(result.output,[{id:1,title:'예시 이슈'}]);assert.ok(!JSON.stringify(result).includes('private-github-token'));
 github.parameters.operation='createIssue';github.parameters.title='실제 전송 아님';const blocked=await executeFlow(flow,[{}],opts);assert.equal(blocked.status,'error');assert.equal(calls,1);assert.match(blocked.error||'',/연습 모드/);
});
test('asynchronous credential refresh is awaited for HTTP, OpenAI, Slack, connectors and subflow calls without leaking tokens',async()=>{
 const cases:[FlowNode,CredentialSecret['provider'],string,any][]=[
  [node('action','n8n-nodes-base.httpRequest',{url:'https://api.example.test/data',authentication:'genericCredentialType',credentialId:'secret'}),'bearer','Bearer refreshed-private-token',{ok:true}],
  [node('action','@n8n/n8n-nodes-langchain.openAi',{model:'test-model',text:'hello',credentialId:'secret'}),'openai','Bearer refreshed-private-token',{output:[{type:'message',content:[{type:'output_text',text:'완료'}]}]}],
  [node('action','n8n-nodes-base.slack',{channel:'C123',text:'hello',credentialId:'secret'}),'slack','Bearer refreshed-private-token',{ok:true,channel:'C123',ts:'1'}],
  [node('action','easyflow.github',{owner:'example',repository:'repo',operation:'listIssues',limit:1,credentialId:'secret'}),'bearer','Bearer refreshed-private-token',[{id:1}]],
 ];
 for(const [action,provider,header,payload] of cases){
  let refreshed=false,calls=0;const child=graph([node('child-start','n8n-nodes-base.executeWorkflowTrigger'),action],[['child-start','action']]);child.id='async-child';
  const flow=graph([trigger(),node('call','n8n-nodes-base.executeWorkflow',{workflowId:'async-child'})],[['start','call']]);
  const result=await executeFlow(flow,[{}],options({mode:'live',getFlow:()=>child,credential:async()=>{await Promise.resolve();refreshed=true;return {...secret,provider,data:{token:'refreshed-private-token'}};},fetch:mock((_url,init)=>{assert.equal(refreshed,true);assert.equal(new Headers(init.headers).get('Authorization'),header);calls++;return json(payload);})}));
  assert.equal(result.status,'success',result.error);assert.equal(calls,1);assert.ok(!JSON.stringify(result).includes('refreshed-private-token'));
 }
});
test('file conversion and extraction execute through the engine, and Set keeps separate files while replacing JSON fields',async()=>{
 const file=createBinaryFile(Buffer.from('name,score\r\n민지,90\r\n준호,85','utf8'),{fileName:'people.csv',mimeType:'text/csv'}),input=withBinary({discard:true},'upload',file);
 const set=node('set','n8n-nodes-base.set',{includeOtherFields:false,assignments:{assignments:[{name:'source',value:'upload',type:'string'}]}}),extract=node('extract','n8n-nodes-base.extractFromFile',{operation:'csv',binaryPropertyName:'upload',options:{keepSource:'none'}}),convert=node('convert','n8n-nodes-base.convertToFile',{operation:'toJson',binaryPropertyName:'result'});
 const result=await executeFlow(graph([trigger(),set,extract,convert],[['start','set'],['set','extract'],['extract','convert']]),[input],options());
 assert.equal(result.status,'success',result.error);assert.deepEqual(jsonWithoutBinary(result.runs[1].output[0][0]),{source:'upload'});assert.deepEqual(getBinary(result.runs[1].output[0][0],'upload'),file);
 assert.deepEqual(JSON.parse(decodeBinaryFile(getBinary(result.output[0],'result')).toString('utf8')),[{name:'민지',score:'90'},{name:'준호',score:'85'}]);
 assert.deepEqual(resolveValue('={{ $json }}',input),{discard:true});assert.equal(Object.hasOwn(result.runs[1].output[0][0],BINARY_KEY),true);
});
test('HTTP file responses retain binary data and metadata independently of the JSON size limit',async()=>{
 const bytes=Buffer.alloc(4_100_000,97),http=node('download','n8n-nodes-base.httpRequest',{url:'https://api.example.test/report.txt',options:{response:{response:{responseFormat:'file',outputPropertyName:'report'}}}});
 const result=await executeFlow(graph([trigger(),http,node('pass','n8n-nodes-base.noOp')],[['start','download'],['download','pass']]),[{id:3}],options({fetch:mock(()=>new Response(bytes,{headers:{'Content-Type':'text/plain'}}))}));
 assert.equal(result.status,'success',result.error);assert.equal(getBinary(result.output[0],'report').fileSize,bytes.length);assert.equal(getBinary(result.output[0],'report').fileName,'report.txt');assert.deepEqual(jsonWithoutBinary(result.output[0]),{id:3});assert.deepEqual(decodeBinaryFile(getBinary(result.output[0],'report')),bytes);
 const malformed={...result.output[0],[BINARY_KEY]:{report:{...getBinary(result.output[0],'report'),fileSize:1}}};let effects=0;
 const invalid=await executeFlow(graph([trigger(),http],[['start','download']]),[malformed],options({fetch:mock(()=>{effects++;return new Response(bytes);})}));assert.equal(invalid.status,'error');assert.equal(effects,0);assert.match(invalid.error||'',/크기/);
});
test('HTTP sends selected files as real multipart or raw binary bodies and learning mode blocks both write requests',async()=>{
 const file=createBinaryFile(Buffer.from('파일 내용','utf8'),{fileName:'upload.txt',mimeType:'text/plain'}),input=withBinary({id:7},'upload',file);
 for(const contentType of ['multipart-form-data','binaryData']){
  let calls=0;const action=node('upload','n8n-nodes-base.httpRequest',{url:'https://api.example.test/upload',method:'POST',sendBody:true,contentType,inputDataFieldName:'upload',sendHeaders:true,headerParameters:{parameters:[{name:'Content-Type',value:'application/json'}]},bodyParameters:{parameters:[{name:'attachment',parameterType:'formBinaryData',inputDataFieldName:'upload'},{name:'label',value:'={{ $json.id }}'}]}});
  const opts=options({mode:'live',fetch:mock(async(_url,init)=>{calls++;if(contentType==='binaryData'){assert.ok(init.body instanceof Blob);assert.equal(await init.body.text(),'파일 내용');assert.equal(new Headers(init.headers).get('Content-Type'),'application/json');}else{assert.ok(init.body instanceof FormData);const attachment=init.body.get('attachment');assert.ok(attachment instanceof Blob);assert.equal(await attachment.text(),'파일 내용');assert.equal((attachment as File).name,'upload.txt');assert.equal(init.body.get('label'),'7');assert.equal(new Headers(init.headers).has('Content-Type'),false);}return json({ok:true});})});
  assert.equal((await executeFlow(graph([trigger(),action],[['start','upload']]),[input],opts)).status,'success');assert.equal(calls,1);
  const blocked=await executeFlow(graph([trigger(),action],[['start','upload']]),[input],{...opts,mode:'learning'});assert.equal(blocked.status,'error');assert.match(blocked.error||'',/연습 모드/);assert.equal(calls,1);
 }
});
test('QuickJS transforms actual records and has no host process/network/file functions',async()=>{
 assert.deepEqual(await runCode('return $input.all().map(item=>({json:{...item.json,double:item.json.value*2}}));',[{value:3}]),[{value:3,double:6}]);
 assert.deepEqual(await runCode('return {json:{name:$json.name,index:$itemIndex}};',[{name:'A'},{name:'B'}],'runOnceForEachItem'),[{name:'A',index:0},{name:'B',index:1}]);
 const [scope]=await runCode('return [{json:{process:typeof process,require:typeof require,fetch:typeof fetch,Worker:typeof Worker,importScripts:typeof importScripts}}];',[{}]);assert.deepEqual(scope,{process:'undefined',require:'undefined',fetch:'undefined',Worker:'undefined',importScripts:'undefined'});
 await assert.rejects(runCode('return Array.from({length:5001},()=>({json:{value:1}}));',[{}]),/5,000/);
 await assert.rejects(runCode('return Promise.resolve([{json:{}}]);',[{}]),/비동기/);
});
test('QuickJS enforces CPU and memory bounds',async()=>{
 const start=Date.now(),loop=runCode('while(true){}',[{}]);let timerAt=0;await new Promise<void>(resolve=>setTimeout(()=>{timerAt=Date.now();resolve();},50));assert.ok(timerAt-start<1000,'code must not block the API event loop');await assert.rejects(loop,/2초/);assert.ok(Date.now()-start<3000);
 await assert.rejects(runCode('const a=[];while(true){a.push(new Array(200000).fill(123));}',[{}]));
});
test('SQLite stores workflows, private AES-GCM credentials, dedupe reservations and snapshots across restarts',()=>{
 const directory=mkdtempSync(join(tmpdir(),'easyflow-store-test-'));let store=new Store(directory);
 try{
  const flow=store.saveFlow(makeFlow());assert.equal(store.getFlow(flow.id)?.name,flow.name);assert.equal(store.listFlows().length,1);
  const publicCredential=store.saveCredential({name:'사용자 키',provider:'openai',data:{apiKey:'never-store-this-in-plaintext'}});assert.equal((publicCredential as any).data,undefined);assert.equal((store.listCredentials()[0] as any).data,undefined);assert.equal(store.getCredential(publicCredential.id).data.apiKey,'never-store-this-in-plaintext');
  // Windows has no POSIX permission bits; the key file is protected by the user profile ACL.
  if(process.platform!=='win32')assert.equal(statSync(join(directory,'credential.key')).mode&0o777,0o600);
  const e:Execution={id:'execution1',workflowId:flow.id,workflowName:flow.name,status:'queued',startedAt:new Date().toISOString(),duration:0,runs:[],mode:'learning'};
  assert.equal(store.reserveExecution(e,flow,[{value:1}],'schedule-slot-1'),true);assert.equal(store.reserveExecution({...e,id:'execution2'},flow,[{}],'schedule-slot-1'),false);store.saveExecution({...e,status:'waiting',resumeAt:'2030-01-01T00:00:00Z'});assert.equal(store.getExecution(e.id)?.status,'waiting');assert.deepEqual(store.getExecutionSnapshot(e.id)?.input,[{value:1}]);
  for(let i=0;i<501;i++)store.reserveExecution({...e,id:`done-${i}`,status:'success',startedAt:'2040-01-01T00:00:00Z'},flow,[{}]);assert.ok(!store.listExecutions().some(v=>v.id===e.id));assert.equal(store.listPendingExecutions()[0].id,e.id);
  store.close();for(const file of readdirSync(directory).filter(v=>v.startsWith('easyflow.sqlite')))assert.ok(!readFileSync(join(directory,file)).toString('utf8').includes('never-store-this-in-plaintext'));
  store=new Store(directory);assert.equal(store.getCredential(publicCredential.id).data.apiKey,'never-store-this-in-plaintext');assert.equal(store.getExecution(e.id)?.status,'waiting');assert.equal(store.deleteCredential(publicCredential.id),true);assert.throws(()=>store.getCredential(publicCredential.id),/다시 연결/);assert.equal(store.deleteFlow(flow.id),true);assert.equal(store.getExecutionSnapshot(e.id)?.flow.name,flow.name);
 }finally{store.close();rmSync(directory,{recursive:true,force:true});}
});
