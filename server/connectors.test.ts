import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {connectorTypes,executeConnector,type ConnectorContext} from './connectors';
import type {Flow,FlowNode,Json} from '../lib/workflow';
import type {CredentialSecret} from './contracts';
import {getItemPairs} from '../lib/item-provenance';
import {executeFlow} from './engine';

const node=(service:string,parameters:Json):FlowNode=>({id:'service',name:service,type:`easyflow.${service}`,typeVersion:1,position:[0,0],credentialId:'test-credential',parameters});
const json=(data:any,status=200,headers:Record<string,string>={})=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json',...headers}});
type Call={url:URL;init:RequestInit;timeout?:number};
function harness(responses:(Response|((call:Call)=>Response))[],extra:Partial<ConnectorContext>={},data:Json={token:'private-test-service-token'}) {
 const calls:Call[]=[],registered:any[]=[],providers:(string|undefined)[]=[];
 const credential:CredentialSecret={id:'test-credential',name:'test',provider:'bearer',createdAt:'',updatedAt:'',data};
 const context:ConnectorContext={mode:'live',credential:(_node,provider)=>{providers.push(provider);return {...credential,provider:provider==='header'?'header':'bearer'};},registerSecret:value=>registered.push(value),resolveValue:(value,item)=>{const match=typeof value==='string'?/^=\{\{ \$json\.(\w+) \}\}$/.exec(value):null;return match?item[match[1]]:value;},request:async(url,init,timeout)=>{const call={url,init,timeout};calls.push(call);const next=responses.shift();assert.ok(next,'Unexpected request');return typeof next==='function'?next(call):next;},...extra};
 return {calls,registered,providers,context};
}
const body=(call:Call)=>JSON.parse(String(call.init.body));
const github={owner:'octocat',repository:'Hello-World'};
const notionPage='494c87d0-72c4-4cf6-960f-55f8427f7692';

test('GitHub lists paginated issues with fixed trusted host and current version',async()=>{
 const h=harness([json([{number:1,title:'first'}],200,{link:'<https://malicious.test/token>; rel="next"'}),json([{number:2,pull_request:{url:'reference'}}])]);
 const out=await executeConnector(node('github',{...github,operation:'listIssues',state:'all',limit:3}),[{}],h.context);
 assert.deepEqual(out[0].map(v=>v.number),[1,2]);assert.equal(h.calls.length,2);
 assert.equal(h.calls[1].url.host,'api.github.com');assert.equal(h.calls[1].url.searchParams.get('page'),'2');assert.equal(h.calls[0].url.searchParams.get('state'),'all');assert.equal(h.calls[0].timeout,30000);
 const headers=new Headers(h.calls[0].init.headers);assert.equal(headers.get('X-GitHub-Api-Version'),'2026-03-10');assert.equal(headers.get('Authorization'),'Bearer private-test-service-token');assert.deepEqual(h.providers,['bearer']);assert.ok(h.registered.length>=2);
});
test('GitHub retrieves and creates issues using per-item data expressions',async()=>{
 const get=harness([json({number:42})]);await executeConnector(node('github',{...github,operation:'getIssue',issueNumber:42}),[{}],get.context);assert.equal(get.calls[0].url.pathname,'/repos/octocat/Hello-World/issues/42');
 const create=harness([json({number:43},201)]);const out=await executeConnector(node('github',{...github,operation:'createIssue',title:'={{ $json.title }}',body:'본문'}),[{title:'실제 제목'}],create.context);
 assert.equal(create.calls[0].init.method,'POST');assert.deepEqual(body(create.calls[0]),{title:'실제 제목',body:'본문'});assert.equal(out[0][0].number,43);
});
test('Notion search remains a read operation despite POST and follows opaque cursors',async()=>{
 const h=harness([json({results:[{object:'page',id:'one'}],has_more:true,next_cursor:'opaque/cursor?2026'}),json({results:[{object:'page',id:'two'}],has_more:false,next_cursor:null})],{mode:'learning'});
 const out=await executeConnector(node('notion',{operation:'searchPages',query:'회의',limit:2}),[{}],h.context);
 assert.equal(out[0].length,2);assert.equal(h.calls[0].url.href,'https://api.notion.com/v1/search');assert.equal(new Headers(h.calls[0].init.headers).get('Notion-Version'),'2026-03-11');assert.deepEqual(body(h.calls[0]).filter,{value:'page',property:'object'});assert.equal(body(h.calls[1]).start_cursor,'opaque/cursor?2026');
});
test('Notion retrieves page properties and creates a page with split text blocks',async()=>{
 const get=harness([json({object:'page',id:notionPage})]);await executeConnector(node('notion',{operation:'getPage',pageId:notionPage}),[{}],get.context);assert.equal(get.calls[0].url.pathname,`/v1/pages/${notionPage}`);
 const create=harness([json({object:'page',id:'created'})]);await executeConnector(node('notion',{operation:'createPage',parentPageId:notionPage,title:'자동화 결과',body:'가'.repeat(4001)}),[{}],create.context);
 const payload=body(create.calls[0]);assert.equal(create.calls[0].init.method,'POST');assert.deepEqual(payload.parent,{page_id:notionPage});assert.equal(payload.properties.title.title[0].text.content,'자동화 결과');assert.deepEqual(payload.children.map((v:any)=>v.paragraph.rich_text[0].text.content.length),[2000,2000,1]);
});
test('Notion uses modern data-source query and validates filter and sort JSON',async()=>{
 const h=harness([json({results:[{id:'entry'}],has_more:false})],{mode:'learning'});
 await executeConnector(node('notion',{operation:'queryDataSource',dataSourceId:notionPage,filter:'{"property":"Status","select":{"equals":"Done"}}',sorts:[{property:'Created',direction:'descending'}]}),[{}],h.context);
 assert.equal(h.calls[0].url.pathname,`/v1/data_sources/${notionPage}/query`);assert.equal(body(h.calls[0]).filter.select.equals,'Done');assert.equal(body(h.calls[0]).sorts[0].direction,'descending');
 const invalid=harness([]);await assert.rejects(executeConnector(node('notion',{operation:'queryDataSource',dataSourceId:notionPage,sorts:{invalid:true}}),[{}],invalid.context),/JSON 배열/);assert.equal(invalid.calls.length,0);
});
test('Telegram sends directly with token in path and validates semantic failures',async()=>{
 const h=harness([json({ok:true,result:{message_id:7}})],{}, {token:'123456:private_test_bot_token'});
 const out=await executeConnector(node('telegram',{operation:'sendMessage',chatId:'@example_channel',text:'알림',parseMode:'HTML'}),[{}],h.context);
 assert.equal(h.calls[0].url.host,'api.telegram.org');assert.equal(h.calls[0].url.pathname,'/bot123456:private_test_bot_token/sendMessage');assert.equal(new Headers(h.calls[0].init.headers).has('Authorization'),false);assert.deepEqual(body(h.calls[0]),{chat_id:'@example_channel',text:'알림',parse_mode:'HTML'});assert.equal(out[0][0].message_id,7);
 const reject=harness([json({ok:false,error_code:403,description:'private_test_bot_token echoed'})],{}, {token:'123456:private_test_bot_token'});await assert.rejects(executeConnector(node('telegram',{operation:'sendMessage',chatId:'12',text:'알림'}),[{}],reject.context),err=>/403/.test(String(err))&&!String(err).includes('private_test_bot_token'));
});
test('Telegram polls without long wait and forbids practice offsets that acknowledge updates',async()=>{
 const h=harness([json({ok:true,result:[{update_id:10},{update_id:11}]})],{mode:'learning'},{token:'123456:private_test_bot_token'});
 const out=await executeConnector(node('telegram',{operation:'getUpdates',limit:2}),[{}],h.context);assert.equal(out[0].length,2);assert.deepEqual(body(h.calls[0]),{limit:2,timeout:0});
 const ack=harness([json({ok:true,result:[]})],{}, {token:'123456:private_test_bot_token'});await executeConnector(node('telegram',{operation:'getUpdates',offset:12}),[{}],ack.context);assert.equal(body(ack.calls[0]).offset,12);
 const blocked=harness([],{mode:'learning'},{token:'123456:private_test_bot_token'});await assert.rejects(executeConnector(node('telegram',{operation:'getUpdates',offset:12}),[{}],blocked.context),/offset/);assert.equal(blocked.calls.length,0);
});
test('Discord sends a confirmed webhook message, suppresses mentions and supports forum threads',async()=>{
 const webhook='https://discord.com/api/webhooks/123456/private_discord_webhook_token?ignored=1';
 const h=harness([json({id:'message'})],{}, {url:webhook});const out=await executeConnector(node('discord',{operation:'sendMessage',content:'@everyone 완료',username:'Easyflow',threadId:'123',threadName:'업무'}),[{}],h.context);
 assert.equal(h.providers[0],'header');assert.equal(h.calls[0].url.searchParams.get('wait'),'true');assert.equal(h.calls[0].url.searchParams.get('ignored'),null);assert.equal(h.calls[0].url.searchParams.get('thread_id'),'123');assert.deepEqual(body(h.calls[0]),{content:'@everyone 완료',allowed_mentions:{parse:[]},username:'Easyflow',thread_name:'업무'});assert.equal(out[0][0].id,'message');assert.ok(h.registered.includes('private_discord_webhook_token'));
});
test('Discord refuses non-service, credential-bearing and malformed webhook destinations',async()=>{
 for(const url of ['http://discord.com/api/webhooks/123/private_token','https://discord.com.evil.test/api/webhooks/123/private_token','https://account:password@discord.com/api/webhooks/123/private_token','https://discord.com/api/webhooks/123/private_token/extra']){
  const h=harness([],{}, {url});await assert.rejects(executeConnector(node('discord',{operation:'sendMessage',content:'test'}),[{}],h.context),/웹훅 주소/);assert.equal(h.calls.length,0);
 }
});
test('Airtable lists records with encoded table names, formulas and pagination',async()=>{
 const h=harness([json({records:[{id:'rec1',fields:{Name:'민지'}}],offset:'opaque/offset'}),json({records:[{id:'rec2',fields:{Name:'지훈'}}]})]);
 const out=await executeConnector(node('airtable',{operation:'listRecords',baseId:'app123',tableId:'고객 목록',filterByFormula:'{Score} >= 80',view:'완료',limit:2}),[{}],h.context);
 assert.equal(out[0].length,2);assert.equal(decodeURIComponent(h.calls[0].url.pathname),'/v0/app123/고객 목록');assert.equal(h.calls[0].url.searchParams.get('filterByFormula'),'{Score} >= 80');assert.equal(h.calls[1].url.searchParams.get('offset'),'opaque/offset');
});
test('Airtable creates and updates actual record fields with conservative typecasting',async()=>{
 for(const operation of ['createRecord','updateRecord']){
  const h=harness([json({records:[{id:'rec1',fields:{Name:'민지'}}]})]);const out=await executeConnector(node('airtable',{operation,baseId:'app123',tableId:'tbl123',recordId:'rec1',fields:{Name:'={{ $json.name }}',Score:92}}),[{name:'민지'}],h.context);
  assert.equal(h.calls[0].init.method,operation==='createRecord'?'POST':'PATCH');assert.deepEqual(body(h.calls[0]),{records:[{fields:{Name:'민지',Score:92},...(operation==='updateRecord'?{id:'rec1'}:{})}],typecast:false});assert.equal(out[0][0].id,'rec1');
 }
});
test('Google Sheets reads one output item per row preserving arrays and requested range',async()=>{
 const h=harness([json({range:"'고객'!A1:B2",majorDimension:'ROWS',values:[['민지',92],['지훈',68]]})],{mode:'learning'}, {accessToken:'sheets-access-token'});
 const out=await executeConnector(node('googleSheets',{operation:'read',spreadsheetId:'sheet123',range:"'고객'!A1:B2"}),[{}],h.context);
 assert.equal(h.calls[0].url.host,'sheets.googleapis.com');assert.equal(decodeURIComponent(h.calls[0].url.pathname),"/v4/spreadsheets/sheet123/values/'고객'!A1:B2");assert.equal(new Headers(h.calls[0].init.headers).get('Authorization'),'Bearer sheets-access-token');assert.deepEqual(out[0][1],{range:"'고객'!A1:B2",rowIndex:1,values:['지훈',68]});
 const empty=harness([json({range:'A1'})]);assert.deepEqual(await executeConnector(node('googleSheets',{operation:'read',spreadsheetId:'sheet123',range:'A1'}),[{}],empty.context),[[]]);
});
test('Google Sheets append and update send typed row matrices and explicit input options',async()=>{
 for(const operation of ['append','update']){
  const h=harness([json({updatedCells:2})]);await executeConnector(node('googleSheets',{operation,spreadsheetId:'sheet123',range:'Sheet1!A:B',values:'[["민지",92]]',valueInputOption:'USER_ENTERED'}),[{}],h.context);
  assert.equal(h.calls[0].init.method,operation==='append'?'POST':'PUT');assert.equal(h.calls[0].url.pathname.endsWith(':append'),operation==='append');assert.equal(h.calls[0].url.searchParams.get('valueInputOption'),'USER_ENTERED');assert.deepEqual(body(h.calls[0]),{range:'Sheet1!A:B',majorDimension:'ROWS',values:[['민지',92]]});
 }
 const invalid=harness([]);await assert.rejects(executeConnector(node('googleSheets',{operation:'append',spreadsheetId:'sheet123',range:'A1',values:[[{nested:true}]]}),[{}],invalid.context),/행 배열/);assert.equal(invalid.calls.length,0);
});
test('every external write is refused in learning mode before credential access or HTTP',async()=>{
 const writes:Record<string,string[]>={github:['createIssue'],notion:['createPage'],telegram:['sendMessage'],discord:['sendMessage'],airtable:['createRecord','updateRecord'],googleSheets:['append','update']};
 for(const [service,list] of Object.entries(writes))for(const operation of list){const h=harness([],{mode:'learning',credential:()=>{throw new Error('must not access credential');}});await assert.rejects(executeConnector(node(service,{operation}),[{}],h.context),/연습 모드/);assert.equal(h.calls.length,0);}
});
test('service-scoped credentials cannot be sent to another connector',async()=>{
 const h=harness([],{credential:()=>({id:'other',name:'Notion 연결',provider:'bearer',service:'notion',createdAt:'',updatedAt:'',data:{token:'other-service-token'}})});
 await assert.rejects(executeConnector(node('github',github),[{}],h.context),/다른 서비스/);assert.equal(h.calls.length,0);
});
test('invalid operations, missing inputs and path traversal fail without a request',async()=>{
 for(const [service,p] of [['github',{...github,operation:'deleteIssue'}],['github',{...github,owner:'..'}],['github',{...github,operation:'getIssue',issueNumber:0}],['notion',{operation:'getPage',pageId:'invalid'}],['airtable',{operation:'createRecord',baseId:'app123',tableId:'tbl123',fields:'{bad JSON}'}]] as [string,Json][]){const h=harness([]);await assert.rejects(executeConnector(node(service,p),[{}],h.context));assert.equal(h.calls.length,0);}
 const h=harness([]);assert.deepEqual(await executeConnector(node('github',github),[],h.context),[[]]);assert.equal(h.providers.length,0);
});
test('HTTP and network failures never expose provider error bodies or secret URLs',async()=>{
 const h=harness([json({error:'private-test-service-token echoed'},401)]);await assert.rejects(executeConnector(node('github',github),[{}],h.context),err=>/HTTP 401/.test(String(err))&&!String(err).includes('private-test-service-token'));
 const failure=harness([],{request:async()=>{throw new Error('https://api.telegram.org/bot123456:private_test_bot_token/getUpdates');}},{token:'123456:private_test_bot_token'});await assert.rejects(executeConnector(node('telegram',{operation:'getUpdates'}),[{}],failure.context),err=>/연결하지 못/.test(String(err))&&!String(err).includes('private_test_bot_token'));
 const invalid=harness([new Response('not JSON')]);await assert.rejects(executeConnector(node('github',github),[{}],invalid.context),/JSON 응답/);
});
test('response bytes, total output items and repeating cursors are bounded',async()=>{
 const huge=harness([new Response('x'.repeat(4*1024*1024+1))]);await assert.rejects(executeConnector(node('github',github),[{}],huge.context),/4MB/);
 const many=harness([json({values:Array.from({length:5001},()=>['x'])})]);await assert.rejects(executeConnector(node('googleSheets',{operation:'read',spreadsheetId:'sheet123',range:'A1:A6000'}),[{}],many.context),/5,000/);
 const repeat=harness([json({results:[{id:'one'}],has_more:true,next_cursor:'same'}),json({results:[{id:'two'}],has_more:true,next_cursor:'same'})]);await assert.rejects(executeConnector(node('notion',{operation:'searchPages',limit:3}),[{}],repeat.context),/다음 페이지/);assert.equal(repeat.calls.length,2);
});
test('connector catalog contains only implemented operations and official sources',()=>{
 const catalog=JSON.parse(readFileSync(new URL('./connector-catalog.json',import.meta.url),'utf8')).catalog;
 assert.deepEqual(catalog.map((c:any)=>c.n8nType),connectorTypes);
 const operations:Record<string,string[]>={github:['listIssues','getIssue','createIssue'],notion:['searchPages','getPage','createPage','queryDataSource'],telegram:['getUpdates','sendMessage'],discord:['sendMessage'],airtable:['listRecords','createRecord','updateRecord'],googleSheets:['read','append','update']};
 for(const entry of catalog){assert.equal(entry.typeVersion,1);assert.ok(entry.setupGuidance.length>=2);assert.ok(entry.docsUrl.startsWith('https://'));assert.deepEqual(entry.fields.find((f:any)=>f.key==='operation').options.map((o:any)=>o.value),operations[entry.id]);assert.deepEqual(entry.credentialProviders,[entry.id==='discord'?'header':'bearer']);}
});
test('GitHub pagination links every expanded row to its own request after empty inputs',async()=>{
 const input=[{repository:'empty'},{repository:'many'},{repository:'last'}],h=harness([
  json([]),json([{number:10},{number:11}],200,{link:'<https://api.github.com/ignored>; rel="next"'}),json([{number:12}]),json([{number:20}]),
 ]),output=await executeConnector(node('github',{...github,repository:'={{ $json.repository }}',limit:3}),input,h.context);
 assert.deepEqual(output,[[{number:10},{number:11},{number:12},{number:20}]]);assert.deepEqual(getItemPairs(output[0]),[[{item:1}],[{item:1}],[{item:1}],[{item:2}]]);assert.equal(h.calls.length,4);assert.deepEqual(h.calls.map(call=>call.url.pathname),['/repos/octocat/empty/issues','/repos/octocat/many/issues','/repos/octocat/many/issues','/repos/octocat/last/issues']);assert.equal(h.calls[2].url.searchParams.get('page'),'2');assert.equal(JSON.stringify(output),'[[{"number":10},{"number":11},{"number":12},{"number":20}]]');assert.deepEqual(Object.keys(output[0]),['0','1','2','3']);
});
test('Notion search and data-source pagination preserve per-input parents in learning mode',async()=>{
 for(const operation of ['searchPages','queryDataSource']){
  const h=harness([json({results:[],has_more:false}),json({results:[{id:'one'},{id:'two'}],has_more:true,next_cursor:'next-page'}),json({results:[{id:'three'}],has_more:false})],{mode:'learning'}),input=[{query:'empty'},{query:'results'}];
  const output=await executeConnector(node('notion',{operation,query:'={{ $json.query }}',dataSourceId:notionPage,limit:3}),input,h.context);assert.deepEqual(getItemPairs(output[0]),[[{item:1}],[{item:1}],[{item:1}]],operation);assert.equal(h.calls.length,3);assert.equal(body(h.calls[2]).start_cursor,'next-page');if(operation==='searchPages')assert.deepEqual(h.calls.map(call=>body(call).query),['empty','results','results']);
 }
});
test('Airtable records, Telegram updates and Google Sheets rows link expansion to original input indexes',async()=>{
 const airtable=harness([json({records:[]}),json({records:[{id:'a'},{id:'b'}],offset:'next'}),json({records:[{id:'c'}]})],{mode:'learning'}),airOutput=await executeConnector(node('airtable',{operation:'listRecords',baseId:'app123',tableId:'={{ $json.table }}',limit:3}),[{table:'empty'},{table:'customers'}],airtable.context);assert.deepEqual(getItemPairs(airOutput[0]),[[{item:1}],[{item:1}],[{item:1}]]);assert.equal(airtable.calls.length,3);assert.equal(airtable.calls[2].url.searchParams.get('offset'),'next');
 const telegram=harness([json({ok:true,result:[]}),json({ok:true,result:[{update_id:1},{update_id:2}]})],{mode:'learning'},{token:'123456:private_test_bot_token'}),telegramOutput=await executeConnector(node('telegram',{operation:'getUpdates'}),[{batch:'empty'},{batch:'next'}],telegram.context);assert.deepEqual(getItemPairs(telegramOutput[0]),[[{item:1}],[{item:1}]]);assert.equal(telegram.calls.length,2);assert.deepEqual(telegram.calls.map(body),[{limit:100,timeout:0},{limit:100,timeout:0}]);
 const sheets=harness([json({values:[]}),json({values:[['A',1],['B',2]]}),json({values:[['C',3]]})],{mode:'learning'}),sheetsOutput=await executeConnector(node('googleSheets',{operation:'read',spreadsheetId:'sheet123',range:'={{ $json.range }}'}),[{range:'Empty!A:B'},{range:'Many!A:B'},{range:'Last!A:B'}],sheets.context);assert.deepEqual(getItemPairs(sheetsOutput[0]),[[{item:1}],[{item:1}],[{item:2}]]);assert.deepEqual(sheetsOutput[0].map(item=>({range:item.range,rowIndex:item.rowIndex,values:item.values})),[{range:'Many!A:B',rowIndex:0,values:['A',1]},{range:'Many!A:B',rowIndex:1,values:['B',2]},{range:'Last!A:B',rowIndex:0,values:['C',3]}]);assert.equal(sheets.calls.length,3);
});
test('Every per-item read and write maps exact parents while preserving response fields and request counts',async()=>{
 const input=[{value:'A',values:[['A',1]],issue:10},{value:'B',values:[['B',2]],issue:20}];
 const cases:{service:string;operation:string;parameters:Json;response:Json;credential?:Json}[]=[
  {service:'github',operation:'getIssue',parameters:{...github,issueNumber:'={{ $json.issue }}'},response:{number:1,pairedItem:'ordinary response field',json:{payload:true}}},
  {service:'github',operation:'createIssue',parameters:{...github,title:'={{ $json.value }}'},response:{number:1}},
  {service:'notion',operation:'getPage',parameters:{pageId:notionPage},response:{object:'page',id:'page'}},
  {service:'notion',operation:'createPage',parameters:{parentPageId:notionPage,title:'={{ $json.value }}'},response:{object:'page',id:'page'}},
  {service:'telegram',operation:'sendMessage',parameters:{chatId:'123',text:'={{ $json.value }}'},response:{ok:true,result:{message_id:1}},credential:{token:'123456:private_test_bot_token'}},
  {service:'discord',operation:'sendMessage',parameters:{content:'={{ $json.value }}'},response:{id:'message'},credential:{url:'https://discord.com/api/webhooks/123456/private_discord_webhook_token'}},
  {service:'airtable',operation:'createRecord',parameters:{baseId:'app123',tableId:'tbl123',fields:{Name:'={{ $json.value }}'}},response:{records:[{id:'record',fields:{Name:'sample'}}]}},
  {service:'airtable',operation:'updateRecord',parameters:{baseId:'app123',tableId:'tbl123',recordId:'rec123',fields:{Name:'={{ $json.value }}'}},response:{records:[{id:'record',fields:{Name:'sample'}}]}},
  {service:'googleSheets',operation:'append',parameters:{spreadsheetId:'sheet123',range:'A1:B2',values:'={{ $json.values }}'},response:{updatedCells:2}},
  {service:'googleSheets',operation:'update',parameters:{spreadsheetId:'sheet123',range:'A1:B2',values:'={{ $json.values }}'},response:{updatedCells:2}},
 ];
 for(const entry of cases){
  const h=harness([json(entry.response),json(entry.response)],{},entry.credential),output=await executeConnector(node(entry.service,{operation:entry.operation,...entry.parameters}),input,h.context);assert.deepEqual(getItemPairs(output[0]),[[{item:0}],[{item:1}]],`${entry.service}/${entry.operation}`);assert.equal(h.calls.length,2);assert.deepEqual(Object.keys(output[0]),['0','1']);assert.equal(JSON.stringify(output),JSON.stringify([output[0].map(item=>({...item}))]));
  if(entry.operation==='getIssue'){assert.equal(h.calls[0].url.pathname.endsWith('/10'),true);assert.equal(h.calls[1].url.pathname.endsWith('/20'),true);assert.equal(output[0][0].pairedItem,'ordinary response field');assert.deepEqual(output[0][0].json,{payload:true});}
  if(entry.operation==='createIssue')assert.deepEqual(h.calls.map(call=>body(call).title),['A','B']);
  if(entry.operation==='createPage')assert.deepEqual(h.calls.map(call=>body(call).properties.title.title[0].text.content),['A','B']);
  if(entry.service==='telegram'||entry.service==='discord')assert.deepEqual(h.calls.map(call=>body(call)[entry.service==='telegram'?'text':'content']),['A','B']);
  if(entry.service==='airtable')assert.deepEqual(h.calls.map(call=>body(call).records[0].fields.Name),['A','B']);
  if(entry.service==='googleSheets')assert.deepEqual(h.calls.map(call=>body(call).values),[[['A',1]],[['B',2]]]);
 }
});
test('Connector empty responses and empty input attach explicit empty pairing without extra effects',async()=>{
 const h=harness([json([]),json([])],{mode:'learning'}),output=await executeConnector(node('github',github),[{batch:1},{batch:2}],h.context);assert.deepEqual(output,[[]]);assert.deepEqual(getItemPairs(output[0]),[]);assert.equal(h.calls.length,2);
 const empty=harness([],{credential:()=>{throw new Error('must not access credential');}}),noInput=await executeConnector(node('github',github),[],empty.context);assert.deepEqual(getItemPairs(noInput[0]),[]);assert.equal(empty.calls.length,0);
});
test('Modern item references follow paginated connector results to the correct source after expansion',async()=>{
 const source:FlowNode={id:'source',name:'원본 고객',type:'n8n-nodes-base.noOp',typeVersion:1,position:[0,0],parameters:{}},start:FlowNode={...source,id:'start',name:'시작',type:'n8n-nodes-base.manualTrigger'},api=node('github',{...github,repository:'={{ $json.repository }}',operation:'listIssues',limit:3,credentialId:'test-credential'}),result:FlowNode={...source,id:'result',name:'원본 연결',type:'n8n-nodes-base.set',parameters:{includeOtherFields:true,assignments:{assignments:[{name:'customer',type:'string',value:'={{ $("원본 고객").item.json.customer }}'}]}}};
 const flow:Flow={id:'connector-pairing',name:'커넥터 연결 검증',nodes:[start,source,api,result],connections:{시작:{main:[[{node:'원본 고객',type:'main',index:0}]]},'원본 고객':{main:[[{node:'github',type:'main',index:0}]]},github:{main:[[{node:'원본 연결',type:'main',index:0}]]}},settings:{timezone:'Asia/Seoul'}};
 const h=harness([json([]),json([{number:10},{number:11}],200,{link:'<https://api.github.com/ignored>; rel="next"'}),json([{number:12}]),json([{number:20}])]),credential:CredentialSecret={id:'test-credential',name:'GitHub 테스트',provider:'bearer',service:'github',createdAt:'',updatedAt:'',data:{token:'private-test-service-token'}};
 const execution=await executeFlow(flow,[{customer:'A',repository:'empty'},{customer:'B',repository:'many'},{customer:'C',repository:'last'}],{mode:'learning',credential:()=>credential,fetch:((url:any,init:any)=>h.context.request(new URL(String(url)),init)) as typeof fetch});assert.equal(execution.status,'success',execution.error);assert.deepEqual(execution.output,[{number:10,customer:'B'},{number:11,customer:'B'},{number:12,customer:'B'},{number:20,customer:'C'}]);assert.equal(h.calls.length,4);assert.deepEqual(execution.runs.find(run=>run.nodeId===api.id)?.outputPairedItems,[[[{item:1}],[{item:1}],[{item:1}],[{item:2}]]]);assert.equal(JSON.stringify(execution.output).includes('private-test-service-token'),false);assert.equal(JSON.stringify(execution.output).includes('pairedItem'),false);
});
