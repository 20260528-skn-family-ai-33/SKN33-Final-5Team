import test from 'node:test';
import assert from 'node:assert/strict';
import {readPagination,resolvePagination,patchPaginationBody,paginationComplete,paginationNextURL,PaginationGuard,type PaginationContext,type PaginationRequest} from './http-pagination';
import {executeFlow} from './engine';
import {getBinary,decodeBinaryFile} from './binary';
import type {Flow,FlowNode,Json} from '../lib/workflow';
import type {RunOptions} from './contracts';
const stage=(id:string,type:string,parameters:Json={}):FlowNode=>({id,name:id,type:`n8n-nodes-base.${type}`,typeVersion:1,position:[0,0],parameters});
const flow=(parameters:Json):Flow=>({id:'pagination-test',name:'페이지 검증',nodes:[stage('start','manualTrigger'),stage('http','httpRequest',{url:'https://api.example.test/items',...parameters})],connections:{start:{main:[[{node:'http',type:'main',index:0}]]}},settings:{}});
const mock=(callback:(url:string,init:RequestInit)=>Response|Promise<Response>)=>((url:any,init:any)=>callback(String(url),init)) as typeof fetch;
const json=(body:any,status=200,headers:Record<string,string>={})=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json',...headers}});
const options=(fetch:typeof globalThis.fetch,extra:Partial<RunOptions>={}):RunOptions=>({mode:'learning',credential:()=>{throw new Error('no credential');},fetch,...extra});
const pagination=(settings:Json)=>({pagination:{pagination:settings}});
const numbered=(settings:Json={})=>({paginationMode:'updateAParameterInEachRequest',parameters:{parameters:[{type:'qs',name:'page',value:'={{ $pageCount + 1 }}'}]},...settings});
const context:PaginationContext={$json:{name:'민지'},$pageCount:2,$response:{body:{items:[{id:1},{id:2}],next:'/page/3',has_more:false},headers:{'x-next':'https://api.example.test/page/3'},statusCode:200},$request:{url:'https://api.example.test/items?page=2',method:'GET',headers:{},qs:{page:'2'}}};

test('safe pagination expressions read response/request paths, compute counters and terminate without accessing host functions',()=>{
 assert.equal(resolvePagination('={{ $pageCount + 1 }}',context),3);assert.equal(resolvePagination('={{ $pageCount * 100 }}',context),200);
 assert.equal(resolvePagination('={{ $response.body.items[$response.body.items.length - 1].id }}',context),2);
 assert.equal(resolvePagination("={{ $response.headers['x-next'] }}",context),'https://api.example.test/page/3');
 assert.equal(resolvePagination('={{ !$response.body.has_more && $response.body.items.length > 0 }}',context),true);
 assert.equal(resolvePagination('={{ $response.body.missing ?? $request.qs.page }}',context),'2');assert.equal(resolvePagination('고객 {{ $json.name }} 페이지 {{ $pageCount }}',context),'고객 민지 페이지 2');
 for(const source of ['={{ process.env }}','={{ $response.body.constructor }}',"={{ $response.body['__proto__'] }}",'={{ $response.body.items.map(x => x) }}','={{ (() => 1)() }}','={{ import("node:fs") }}'])assert.throws(()=>resolvePagination(source,context),/페이지 표현식/);
});
test('official nested schema validates modes, update names, completion and bounds before requests',()=>{
 assert.equal(readPagination({options:pagination({paginationMode:'off'})}),undefined);
 const config=readPagination({options:pagination(numbered({limitPagesFetched:true,maxRequests:3,requestInterval:25}))})!;assert.equal(config.maxRequests,3);assert.equal(config.requestInterval,25);assert.equal(config.parameters[0].type,'qs');
 for(const invalid of [numbered({maxRequests:0,limitPagesFetched:true}),numbered({maxRequests:1001,limitPagesFetched:true}),numbered({requestInterval:-1}),numbered({requestInterval:60001}),numbered({limitPagesFetched:'true'}),numbered({parameters:{parameters:[{type:'qs',name:'__proto__',value:1}]}}),numbered({parameters:{parameters:[]}}),numbered({paginationCompleteWhen:'other',completeExpression:'={{ process.env }}'}),numbered({paginationCompleteWhen:'receiveSpecificStatusCodes',statusCodesWhenComplete:'599,nope'})])assert.throws(()=>readPagination({options:pagination(invalid)}));
});
test('completion checks bare empty lists, explicit statuses and boolean expressions; body patches preserve unrelated nested fields',()=>{
 const empty=readPagination({options:pagination(numbered())})!;assert.equal(paginationComplete(empty,{...context,$response:{...context.$response!,body:[]}}),true);assert.equal(paginationComplete(empty,{...context,$response:{...context.$response!,body:{items:[]}}}),false);
 const code=readPagination({options:pagination(numbered({paginationCompleteWhen:'receiveSpecificStatusCodes',statusCodesWhenComplete:'204,404'}))})!;assert.equal(paginationComplete(code,{...context,$response:{...context.$response!,statusCode:404}}),true);
 const other=readPagination({options:pagination(numbered({paginationCompleteWhen:'other',completeExpression:'={{ !$response.body.has_more }}'}))})!;assert.equal(paginationComplete(other,context),true);
 const source={request:{cursor:'old',filter:'keep'},keep:true};assert.deepEqual(patchPaginationBody(source,{'request.cursor':'next','request.page':3}),{request:{cursor:'next',page:3,filter:'keep'},keep:true});assert.equal(source.request.cursor,'old');assert.throws(()=>patchPaginationBody({}, {'__proto__.polluted':true}));assert.equal(({} as any).polluted,undefined);
});
test('guard detects canonical repeated pages before fetching them and rejects authenticated origin changes',()=>{
 const config=readPagination({options:pagination(numbered())})!,request:PaginationRequest={url:'https://api.example.test/items?a=1&b=2',method:'GET',headers:{Authorization:'old'},qs:{}};
 const guard=new PaginationGuard(config);guard.claim(request,true);assert.throws(()=>guard.claim({...request,url:'https://api.example.test/items?b=2&a=1#ignore',headers:{Authorization:'new'}},true),/이미 요청/);assert.throws(()=>guard.claim({...request,url:'https://other.example.test/items'},true),/같은 사이트/);
 assert.equal(paginationNextURL('?page=3',new URL(request.url)).toString(),'https://api.example.test/items?page=3');for(const bad of ['file:///etc/passwd','https://user:password@example.test/','javascript:alert(1)'])assert.throws(()=>paginationNextURL(bad,new URL(request.url)));
});
test('HTTP follows response body next URLs and returns one item for each actual envelope page',async()=>{
 const urls:string[]=[],pages=[{items:[{id:1}],next:'/items?page=2'},{items:[{id:2}],next:null}];
 const result=await executeFlow(flow({options:pagination({paginationMode:'responseContainsNextURL',nextURL:'={{ $response.body.next }}',paginationCompleteWhen:'other',completeExpression:'={{ !$response.body.next }}'})}),[{}],options(mock(url=>{urls.push(url);return json(pages[urls.length-1]);})));
 assert.equal(result.status,'success',result.error);assert.deepEqual(urls,['https://api.example.test/items','https://api.example.test/items?page=2']);assert.deepEqual(result.output,pages);
});
test('HTTP reads next URL from response headers, and text full responses honor Put Output in Field',async()=>{
 let calls=0;const result=await executeFlow(flow({options:{...pagination({paginationMode:'responseContainsNextURL',nextURL:"={{ $response.headers['x-next'] }}",paginationCompleteWhen:'other',completeExpression:"={{ !$response.headers['x-next'] }}"}),response:{response:{responseFormat:'text',fullResponse:true,outputPropertyName:'content'}}}}),[{}],options(mock(url=>{calls++;if(calls===2)assert.equal(url,'https://api.example.test/second');return new Response(`page${calls}`,{status:201,headers:calls===1?{'x-next':'/second','x-page':'1'}:{'x-page':'2'}});})));assert.equal(result.status,'success',result.error);assert.equal(calls,2);assert.deepEqual(result.output.map((item:any)=>[item.content,item.statusCode,item.headers['x-page']]),[['page1',201,'1'],['page2',201,'2']]);assert.ok(result.output.every((item:any)=>!Object.hasOwn(item,'body')));
});
test('HTTP updates query page counters starting at zero and concatenates bare arrays until an empty page',async()=>{
 const queried:string[]=[],result=await executeFlow(flow({sendQuery:true,queryParameters:{parameters:[{name:'limit',value:2}]},options:pagination(numbered())}),[{customer:'A'}],options(mock(url=>{queried.push(url);const current=Number(new URL(url).searchParams.get('page'));assert.equal(new URL(url).searchParams.get('limit'),'2');return json(current<3?[{id:current*2-1},{id:current*2}]:[]);})));assert.equal(result.status,'success',result.error);assert.deepEqual(queried.map(url=>new URL(url).searchParams.get('page')),['1','2','3']);assert.deepEqual(result.output,[{id:1},{id:2},{id:3},{id:4}]);
});
test('HTTP body/header cursor updates use prior response data without losing base body or headers',async()=>{
 const seen:any[]=[],settings=numbered({parameters:{parameters:[{type:'body',name:'request.cursor',value:'={{ $response.body.next ?? "first" }}'},{type:'headers',name:'X-Page',value:'={{ $pageCount + 1 }}'}]},paginationCompleteWhen:'other',completeExpression:'={{ !$response.body.next }}'});
 const result=await executeFlow(flow({method:'POST',sendBody:true,specifyBody:'json',jsonBody:'{"request":{"filter":"keep"},"customer":"{{ $json.name }}"}',sendHeaders:true,headerParameters:{parameters:[{name:'X-Keep',value:'unchanged'}]},options:pagination(settings)}),[{name:'민지'}],options(mock((_url,init)=>{const body=JSON.parse(String(init.body));seen.push(body);assert.equal(new Headers(init.headers).get('X-Keep'),'unchanged');assert.equal(new Headers(init.headers).get('X-Page'),String(seen.length));return json({next:seen.length===1?'cursor2':null});}),{mode:'live'}));assert.equal(result.status,'success',result.error);assert.deepEqual(seen,[{request:{filter:'keep',cursor:'first'},customer:'민지'},{request:{filter:'keep',cursor:'cursor2'},customer:'민지'}]);
});
test('HTTP form-urlencoded pagination updates actual form bodies on every request',async()=>{
 const seen:string[]=[],result=await executeFlow(flow({method:'POST',sendBody:true,contentType:'form-urlencoded',specifyBody:'string',body:'filter=keep',options:pagination(numbered({parameters:{parameters:[{type:'body',name:'page',value:'={{ $pageCount + 1 }}'}]},limitPagesFetched:true,maxRequests:2}))}),[{}],options(mock((_url,init)=>{assert.ok(init.body instanceof URLSearchParams);assert.equal(init.body.get('filter'),'keep');seen.push(init.body.get('page')!);return json({ok:true});}),{mode:'live'}));assert.equal(result.status,'success',result.error);assert.deepEqual(seen,['1','2']);
});
test('explicit maxRequests completes at the requested limit while the default hard cap fails honestly',async()=>{
 let calls=0;const request=mock(url=>{calls++;return json([{page:new URL(url).searchParams.get('page')}]);});
 const limited=await executeFlow(flow({options:pagination(numbered({limitPagesFetched:true,maxRequests:2}))}),[{}],options(request));assert.equal(limited.status,'success');assert.equal(calls,2);assert.deepEqual(limited.output,[{page:'1'},{page:'2'}]);assert.deepEqual(limited.runs[1].pagination,{pagesFetched:2,itemsFetched:2,complete:false,limited:true,maxRequests:2});assert.match(limited.runs[1].warning||'',/2페이지/);calls=0;
 const unbounded=await executeFlow(flow({options:pagination(numbered())}),[{}],options(request));assert.equal(unbounded.status,'error');assert.equal(calls,100);assert.match(unbounded.error||'',/100페이지/);
});
test('repeated next URLs or constant update values are rejected before a duplicate external request',async()=>{
 for(const setting of [{paginationMode:'responseContainsNextURL',nextURL:'={{ $response.body.next }}'},numbered({parameters:{parameters:[{type:'qs',name:'page',value:1}]}})]){
  let calls=0;const result=await executeFlow(flow({options:pagination(setting)}),[{}],options(mock(url=>{calls++;return json({next:url});})));assert.equal(result.status,'error');assert.match(result.error||'',/이미 요청/);assert.equal(calls,1);
 }
});
test('authenticated cross-origin pagination rejects the next request and awaits refreshed credentials for every allowed page',async()=>{
 let fetches=0,credentials=0;const config={paginationMode:'responseContainsNextURL',nextURL:'={{ $response.body.next }}',paginationCompleteWhen:'other',completeExpression:'={{ !$response.body.next }}'};
 const authenticated={authentication:'genericCredentialType',credentialId:'account',options:pagination(config)};
 const runOptions=options(mock((_url,init)=>{fetches++;assert.equal(new Headers(init.headers).get('Authorization'),`Bearer protected-token-${credentials}`);return json({next:fetches===1?'/items?page=2':'https://other.example.test/items'});}),{credential:async()=>{credentials++;await Promise.resolve();return {id:'account',name:'test',provider:'bearer',data:{token:`protected-token-${credentials}`},createdAt:'',updatedAt:''};}});
 const result=await executeFlow(flow(authenticated),[{}],runOptions);assert.equal(result.status,'error');assert.match(result.error||'',/같은 사이트/);assert.equal(fetches,2);assert.equal(credentials,2);assert.ok(!JSON.stringify(result).includes('protected-token-'));
 let manualCalls=0;const manual=await executeFlow(flow({sendHeaders:true,headerParameters:{parameters:[{name:'X-API-Key',value:'private-header-token'}]},options:pagination(config)}),[{}],options(mock(()=>{manualCalls++;return json({next:'https://other.example.test/items'});})));assert.equal(manual.status,'error');assert.equal(manualCalls,1);
 let anonymousCalls=0;const anonymous=await executeFlow(flow({options:pagination(config)}),[{}],options(mock(url=>{anonymousCalls++;return json({next:anonymousCalls===1?'https://other.example.test/items':null,url});})));assert.equal(anonymous.status,'success',anonymous.error);assert.equal(anonymousCalls,2);
});
test('pagination retains learning read-only guarantees and rejects malformed expressions before fetch',async()=>{
 let calls=0;const request=mock(()=>{calls++;return json({});});
 for(const parameters of [{method:'POST',options:pagination(numbered())},{options:pagination(numbered({parameters:{parameters:[{type:'qs',name:'page',value:'={{ process.exit() }}'}]}}))},{options:pagination(numbered({parameters:{parameters:[{type:'body',name:'page',value:1}]}}))}]){const result=await executeFlow(flow(parameters),[{}],options(request));assert.equal(result.status,'error');assert.equal(calls,0);}
});
test('request intervals pause between pages and cancellation stops the delay before another fetch',async()=>{
 const times:number[]=[],settings=numbered({limitPagesFetched:true,maxRequests:2,requestInterval:20}),result=await executeFlow(flow({options:pagination(settings)}),[{}],options(mock(()=>{times.push(Date.now());return json({ok:true});})));assert.equal(result.status,'success');assert.equal(times.length,2);assert.ok(times[1]-times[0]>=15);
 const controller=new AbortController();let calls=0;const canceled=await executeFlow(flow({options:pagination(numbered({requestInterval:1000}))}),[{}],options(mock(()=>{calls++;setTimeout(()=>controller.abort(),20);return json({ok:true});}),{signal:controller.signal}));assert.equal(canceled.status,'canceled');assert.equal(calls,1);
});
test('pagination enforces cumulative item/JSON/file size bounds before requesting another page',async()=>{
 let calls=0;const tooMany=await executeFlow(flow({options:{...pagination(numbered()),response:{response:{fullResponse:true}}}}),[{}],options(mock(()=>{calls++;return json(Array.from({length:3000},(_,id)=>({id})));})));assert.equal(tooMany.status,'error');assert.match(tooMany.error||'',/5,000/);assert.equal(calls,2);
 calls=0;const tooLarge=await executeFlow(flow({options:pagination(numbered())}),[{}],options(mock(()=>{calls++;return json({content:'x'.repeat(2100000)});})));assert.equal(tooLarge.status,'error');assert.match(tooLarge.error||'',/4MB/);assert.equal(calls,2);
 calls=0;const files=await executeFlow(flow({options:{...pagination(numbered()),response:{response:{responseFormat:'file'}}}}),[{}],options(mock(()=>{calls++;return new Response(Buffer.alloc(4500000),{headers:{'Content-Type':'application/octet-stream'}});})));assert.equal(files.status,'error');assert.match(files.error||'',/8MB/);assert.equal(calls,2);
});
test('neverError preserves HTTP failure bodies with full JSON or text status while parsing and network errors remain real errors',async()=>{
 const output=await executeFlow(flow({options:{response:{response:{fullResponse:true,neverError:true,responseFormat:'json'}}}}),[{}],options(mock(()=>json({error:'not found'},404,{'x-reason':'gone'}))));assert.equal(output.status,'success');assert.deepEqual(output.output,[{body:{error:'not found'},headers:{'content-type':'application/json','x-reason':'gone'},statusCode:404,statusMessage:''}]);
 const text=await executeFlow(flow({includeResponseHeadersAndStatus:true,neverError:true,options:{response:{response:{responseFormat:'text',outputPropertyName:'content'}}}}),[{}],options(mock(()=>new Response('bad request',{status:400,statusText:'Bad Request'}))));assert.equal(text.status,'success');assert.equal(text.output[0].content,'bad request');assert.equal(text.output[0].statusCode,400);assert.equal(text.output[0].statusMessage,'Bad Request');assert.equal(text.output[0].body,undefined);
 const malformed=await executeFlow(flow({neverError:true,responseFormat:'json'}),[{}],options(mock(()=>new Response('not JSON',{status:500}))));assert.equal(malformed.status,'error');assert.match(malformed.error||'',/JSON/);
 const network=await executeFlow(flow({neverError:true}),[{}],options(mock(()=>{throw new Error('private transport data');})));assert.equal(network.status,'error');assert.ok(!JSON.stringify(network).includes('private transport data'));
});
test('neverError allows status-based pagination completion and error file downloads keep real status metadata',async()=>{
 let calls=0;const result=await executeFlow(flow({options:{...pagination(numbered({paginationCompleteWhen:'receiveSpecificStatusCodes',statusCodesWhenComplete:'404'})),response:{response:{neverError:true,fullResponse:true}}}}),[{}],options(mock(()=>{calls++;return json(calls===1?[{id:1}]:{error:'end'},calls===1?200:404);})));assert.equal(result.status,'success');assert.equal(calls,2);assert.deepEqual(result.output.map((page:any)=>page.statusCode),[200,404]);
 const file=await executeFlow(flow({options:{response:{response:{neverError:true,fullResponse:true,responseFormat:'file',outputPropertyName:'errorFile'}}}}),[{input:'not in full response'}],options(mock(()=>new Response('error bytes',{status:503,statusText:'Unavailable',headers:{'Content-Type':'text/plain'}}))));assert.equal(file.status,'success',file.error);assert.equal(file.output[0].statusCode,503);assert.equal(file.output[0].input,undefined);assert.equal(decodeBinaryFile(getBinary(file.output[0],'errorFile')).toString(),'error bytes');
 const defaultError=await executeFlow(flow({}),[{}],options(mock(()=>json({error:'bad'},503))));assert.equal(defaultError.status,'error');assert.match(defaultError.error||'',/HTTP 503/);
});
test('real completion at an explicit cap is distinct from truncation, and unrelated nodes never inherit pagination metadata',async()=>{
 let calls=0;const value=flow({options:pagination(numbered({limitPagesFetched:true,maxRequests:2}))});value.nodes.push(stage('end','noOp'));value.connections.http={main:[[{node:'end',type:'main',index:0}]]};
 const result=await executeFlow(value,[{}],options(mock(()=>{calls++;return json(calls===1?[{id:1}]:[]);})));assert.equal(result.status,'success');assert.deepEqual(result.runs[1].pagination,{pagesFetched:2,itemsFetched:1,complete:true,limited:false,maxRequests:2});assert.equal(result.runs[1].warning,undefined);assert.equal(result.runs[2].pagination,undefined);
});
test('invalid full-text output names and unsafe pagination bracket paths fail before effects',async()=>{
 let calls=0;const request=mock(()=>{calls++;return json({});});
 for(const parameters of [{options:{response:{response:{responseFormat:'text',fullResponse:true,outputPropertyName:'headers'}}}},{options:pagination(numbered({parameters:{parameters:[{type:'qs',name:'page',value:"={{ $response.body['constructor'] }}"}]}}))}]){const result=await executeFlow(flow(parameters),[{}],options(request));assert.equal(result.status,'error');assert.equal(calls,0);}
});
test('canceling a pending JSON response body cancels its reader before a later page can be requested',async()=>{
 const controller=new AbortController();let calls=0,canceled=false;
 const result=await executeFlow(flow({options:pagination(numbered())}),[{}],options(mock(()=>{calls++;const stream=new ReadableStream<Uint8Array>({start(c){c.enqueue(new TextEncoder().encode('[{"id":'));setTimeout(()=>controller.abort(),20);},cancel(){canceled=true;}});return new Response(stream,{headers:{'Content-Type':'application/json'}});}),{signal:controller.signal}));assert.equal(result.status,'canceled');assert.equal(calls,1);assert.equal(canceled,true);
});
