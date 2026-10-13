import type {FlowNode,Json} from '../lib/workflow';
import type {CredentialSecret} from './contracts';
import {attachItemPairs,type ItemPair} from '../lib/item-provenance';

export const connectorTypes=['easyflow.github','easyflow.notion','easyflow.telegram','easyflow.discord','easyflow.airtable','easyflow.googleSheets'];
export type ConnectorContext={
 mode:'learning'|'live';
 credential:(node:FlowNode,provider?:string)=>CredentialSecret|Promise<CredentialSecret>;
 request:(url:URL,init:RequestInit,timeout?:number)=>Promise<Response>;
 resolveValue:(value:any,item:Json)=>any;
 registerSecret:(value:any)=>void;
};
const MAX_ITEMS=5000,MAX_BYTES=4*1024*1024,TIMEOUT=30000;
const operations:Record<string,{default:string;read:string[];write:string[]}>= {
 'easyflow.github':{default:'listIssues',read:['listIssues','getIssue'],write:['createIssue']},
 'easyflow.notion':{default:'searchPages',read:['searchPages','getPage','queryDataSource'],write:['createPage']},
 'easyflow.telegram':{default:'getUpdates',read:['getUpdates'],write:['sendMessage']},
 'easyflow.discord':{default:'sendMessage',read:[],write:['sendMessage']},
 'easyflow.airtable':{default:'listRecords',read:['listRecords'],write:['createRecord','updateRecord']},
 'easyflow.googleSheets':{default:'read',read:['read'],write:['append','update']},
};
const names:Record<string,string>={'easyflow.github':'GitHub','easyflow.notion':'Notion','easyflow.telegram':'Telegram','easyflow.discord':'Discord','easyflow.airtable':'Airtable','easyflow.googleSheets':'Google Sheets'};
function text(value:any,label:string,max=10000,optional=false):string {
 if(value===undefined||value===null||value===''){if(optional)return '';throw new Error(`${label}을 입력해주세요.`);}
 if(typeof value!=='string'&&typeof value!=='number')throw new Error(`${label}은 문자로 입력해주세요.`);
 const result=String(value);if(!optional&&!result.trim())throw new Error(`${label}을 입력해주세요.`);
 if(result.length>max)throw new Error(`${label}은 ${max}자까지 입력할 수 있어요.`);return result;
}
function integer(value:any,label:string,min:number,max:number,fallback?:number):number {
 if((value===undefined||value===null||value==='')&&fallback!==undefined)return fallback;
 const result=typeof value==='number'?value:typeof value==='string'&&value.trim()?Number(value):NaN;
 if(!Number.isSafeInteger(result)||result<min||result>max)throw new Error(`${label}은 ${min}~${max} 사이의 정수로 입력해주세요.`);return result;
}
function oneOf(value:any,choices:string[],label:string,fallback:string):string {
 const result=value===undefined||value===''?fallback:value;
 if(typeof result!=='string'||!choices.includes(result))throw new Error(`${label} 설정을 확인해주세요.`);return result;
}
function parsed(value:any,label:string):any {
 if(typeof value==='string'){try{return JSON.parse(value);}catch{throw new Error(`${label}에 올바른 JSON을 입력해주세요.`);}}return value;
}
function object(value:any,label:string):Json {
 const result=parsed(value,label);if(!result||typeof result!=='object'||Array.isArray(result))throw new Error(`${label}은 JSON 객체로 입력해주세요.`);return result;
}
function records(value:any,service:string):Json[] {
 if(!Array.isArray(value)||value.some(v=>!v||typeof v!=='object'||Array.isArray(v)))throw new Error(`${service} 응답 형식을 확인할 수 없어요.`);return value;
}
function identifier(value:any,label:string):string {
 const result=text(value,label,300).trim();if(result==='.'||result==='..'||/[\/#?\x00-\x1f]/.test(result))throw new Error(`${label}을 확인해주세요.`);return encodeURIComponent(result);
}
function notionId(value:any,label:string):string {
 const result=text(value,label,100).trim();if(!/^(?:[a-f\d]{32}|[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12})$/i.test(result))throw new Error(`${label}에 Notion의 32자리 ID를 입력해주세요.`);return result;
}
function resolve(value:any,item:Json,context:ConnectorContext):any {
 const resolved=context.resolveValue(value,item);
 if(Array.isArray(resolved))return resolved.map(v=>resolve(v,item,context));
 if(resolved&&typeof resolved==='object')return Object.fromEntries(Object.entries(resolved).map(([k,v])=>[k,resolve(v,item,context)]));
 return resolved;
}
async function responseJson(response:Response,service:string):Promise<any> {
 if(!response.ok){void response.body?.cancel().catch(()=>{});throw new Error(`${service} 요청에 실패했어요 (HTTP ${response.status}). 연결 권한과 입력값을 확인해주세요.${response.status===429?' 요청이 너무 많아요. 잠시 후 다시 실행해주세요.':''}`);}
 const size=Number(response.headers.get('content-length'));
 if(Number.isFinite(size)&&size>MAX_BYTES){void response.body?.cancel().catch(()=>{});throw new Error(`${service} 응답이 4MB를 넘었어요. 조회 범위를 줄여주세요.`);}
 let raw='';
 if(response.body){
  const reader=response.body.getReader(),decoder=new TextDecoder();let bytes=0;
  try{while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;if(bytes>MAX_BYTES){await reader.cancel();throw new Error(`${service} 응답이 4MB를 넘었어요. 조회 범위를 줄여주세요.`);}raw+=decoder.decode(part.value,{stream:true});}raw+=decoder.decode();}finally{reader.releaseLock();}
 }
 try{return JSON.parse(raw);}catch{throw new Error(`${service}에서 JSON 응답을 받지 못했어요.`);}
}

/** Direct service APIs. No n8n package, runtime, or proxy is used. */
export async function executeConnector(node:FlowNode,input:Json[],context:ConnectorContext):Promise<Json[][]> {
 const spec=operations[node.type],service=names[node.type];if(!spec)throw new Error('지원하지 않는 서비스 단계예요.');
 const operation=node.parameters.operation??spec.default;
 if(![...spec.read,...spec.write].includes(operation))throw new Error(`${service}에서 지원하지 않는 작업이에요.`);
 if(context.mode==='learning'&&spec.write.includes(operation))throw new Error(`연습 모드에서는 ${service}에 데이터를 생성하거나 메시지를 보내지 않아요. 실제 실행으로 전환해주세요.`);
 if(input.length>MAX_ITEMS)throw new Error('서비스 단계는 한 번에 5,000개 데이터까지 처리할 수 있어요.');
 if(!input.length)return [attachItemPairs([],[])];
 const credential=await context.credential(node,node.type==='easyflow.discord'?'header':'bearer');context.registerSecret(credential.data);
 const credentialService=node.type.slice('easyflow.'.length);
 if(credential.service&&credential.service!==credentialService)throw new Error(`${service}용 연결을 선택해주세요. 다른 서비스의 토큰을 사용할 수 없어요.`);
 const headers:Record<string,string>={'Content-Type':'application/json'};
 let token='';
 if(node.type!=='easyflow.discord'){
  token=text(credential.data.token??credential.data.accessToken??credential.data.apiKey,'연결 토큰',10000).trim();
  if(/[\r\n]/.test(token))throw new Error('연결 토큰 형식을 확인해주세요.');context.registerSecret(token);headers.Authorization=`Bearer ${token}`;
 }
 if(node.type==='easyflow.github'){headers.Accept='application/vnd.github+json';headers['X-GitHub-Api-Version']='2026-03-10';}
 if(node.type==='easyflow.notion')headers['Notion-Version']='2026-03-11';
 let requests=0;const output:Json[]=[],pairs:ItemPair[][]=[];
 const add=(items:Json[],sourceIndex:number)=>{if(output.length+items.length>MAX_ITEMS)throw new Error('서비스 응답이 5,000개를 넘었어요. 조회 개수를 줄여주세요.');output.push(...items);for(const _ of items)pairs.push([{item:sourceIndex}]);};
 const request=async(url:URL,method='GET',body?:any):Promise<{data:any;response:Response}>=>{
  if(++requests>MAX_ITEMS)throw new Error('한 단계의 서비스 요청은 5,000회까지 실행할 수 있어요.');
  let response:Response;
  try{response=await context.request(url,{method,headers,redirect:'error',...(body===undefined?{}:{body:JSON.stringify(body)})},TIMEOUT);}catch{throw new Error(`${service}에 연결하지 못했어요. 네트워크 상태와 연결 정보를 확인해주세요.`);}
  return {data:await responseJson(response,service),response};
 };
 for(const [itemIndex,item] of input.entries()){
  const p=resolve(node.parameters,item,context),limit=()=>integer(p.limit,'조회 개수',1,MAX_ITEMS,100);
  if(node.type==='easyflow.github'){
   const base=`https://api.github.com/repos/${identifier(p.owner,'저장소 소유자')}/${identifier(p.repository,'저장소 이름')}/issues`;
   if(operation==='listIssues'){
    const count=limit();let fetched=0;
    for(let page=1;page<=50&&fetched<count;page++){
     const url=new URL(base);url.searchParams.set('state',oneOf(p.state,['open','closed','all'],'이슈 상태','open'));url.searchParams.set('per_page',String(Math.min(100,count-fetched)));url.searchParams.set('page',String(page));
     const {data,response}=await request(url),items=records(data,service).slice(0,count-fetched);add(items,itemIndex);fetched+=items.length;
     // Reconstruct next pages on our fixed host; never forward credentials to a Link URL.
     if(!/rel="next"/.test(response.headers.get('link')||'')||!items.length)break;
     if(page===50&&fetched<count)throw new Error('GitHub 조회가 50페이지를 넘었어요. 조회 범위를 줄여주세요.');
    }
   }else if(operation==='getIssue'){const {data}=await request(new URL(`${base}/${integer(p.issueNumber,'이슈 번호',1,Number.MAX_SAFE_INTEGER)}`));add([object(data,'GitHub 응답')],itemIndex);}
   else{const body={title:text(p.title,'이슈 제목',1024),body:text(p.body,'이슈 본문',65536,true)};const {data}=await request(new URL(base),'POST',body);add([object(data,'GitHub 응답')],itemIndex);}
  }else if(node.type==='easyflow.notion'){
   if(operation==='getPage'){const {data}=await request(new URL(`https://api.notion.com/v1/pages/${notionId(p.pageId,'페이지 ID')}`));add([object(data,'Notion 응답')],itemIndex);}
   else if(operation==='createPage'){
    const title=text(p.title,'페이지 제목',2000),body=text(p.body,'페이지 본문',100000,true);
    const children:Json[]=[];for(let pos=0;pos<body.length;pos+=2000)children.push({object:'block',type:'paragraph',paragraph:{rich_text:[{type:'text',text:{content:body.slice(pos,pos+2000)}}]}});
    const {data}=await request(new URL('https://api.notion.com/v1/pages'),'POST',{parent:{page_id:notionId(p.parentPageId,'부모 페이지 ID')},properties:{title:{type:'title',title:[{type:'text',text:{content:title}}]}},children});add([object(data,'Notion 응답')],itemIndex);
   }else{
    const base=operation==='searchPages'?'https://api.notion.com/v1/search':`https://api.notion.com/v1/data_sources/${notionId(p.dataSourceId,'데이터 소스 ID')}/query`;
    const count=limit();let fetched=0,cursor:string|undefined;const cursors=new Set<string>();
    for(let page=0;page<50&&fetched<count;page++){
     const body:Json={page_size:Math.min(100,count-fetched),...(cursor?{start_cursor:cursor}:{})};
     if(operation==='searchPages'){body.filter={value:'page',property:'object'};const query=text(p.query,'검색어',2000,true);if(query)body.query=query;}
     else{if(p.filter!==undefined&&p.filter!=='')body.filter=object(p.filter,'필터');if(p.sorts!==undefined&&p.sorts!==''){body.sorts=parsed(p.sorts,'정렬');if(!Array.isArray(body.sorts))throw new Error('정렬은 JSON 배열로 입력해주세요.');}}
     const {data}=await request(new URL(base),'POST',body),items=records(data.results,service).slice(0,count-fetched);add(items,itemIndex);fetched+=items.length;
     if(!data.has_more||fetched>=count)break;
     const nextCursor=data.next_cursor;if(typeof nextCursor!=='string'||!nextCursor||cursors.has(nextCursor))throw new Error('Notion의 다음 페이지 정보를 확인할 수 없어요.');cursor=nextCursor;cursors.add(nextCursor);
     if(page===49)throw new Error('Notion 조회가 50페이지를 넘었어요. 조회 범위를 줄여주세요.');
    }
   }
  }else if(node.type==='easyflow.telegram'){
   if(!/^\d+:[A-Za-z\d_-]+$/.test(token))throw new Error('Telegram BotFather에서 받은 봇 토큰을 연결해주세요.');
   // Telegram authenticates in the path, not the Authorization header.
   delete headers.Authorization;const url=new URL(`https://api.telegram.org/bot${token}/${operation}`);context.registerSecret(url.href);
   let body:Json;
   if(operation==='sendMessage'){
    body={chat_id:text(p.chatId,'채팅 ID',200),text:text(p.text,'메시지',4096)};
    const parseMode=oneOf(p.parseMode,['','HTML','MarkdownV2'],'메시지 서식','');if(parseMode)body.parse_mode=parseMode;
   }else{
    body={limit:integer(p.limit,'조회 개수',1,100,100),timeout:0};
    if(p.offset!==undefined&&p.offset!==''){
     // Offsets acknowledge updates and therefore are excluded from a read-only practice run.
     if(context.mode==='learning')throw new Error('연습 모드에서는 Telegram 업데이트를 확인 처리하는 offset을 사용할 수 없어요.');
     body.offset=integer(p.offset,'업데이트 시작 번호',-Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER);
    }
   }
   const {data}=await request(url,'POST',body);if(data.ok!==true)throw new Error(`Telegram 요청이 거절됐어요${Number.isInteger(data.error_code)?` (${data.error_code})`:''}. 봇 권한과 채팅 ID를 확인해주세요.`);
   add(operation==='getUpdates'?records(data.result,service):[object(data.result,'Telegram 응답')],itemIndex);
  }else if(node.type==='easyflow.discord'){
   const raw=text(credential.data.url,'Discord 웹훅 주소',2000);let url:URL;try{url=new URL(raw);}catch{throw new Error('Discord에서 복사한 웹훅 주소를 연결해주세요.');}
   if(url.protocol!=='https:'||url.hostname!=='discord.com'||url.port||url.username||url.password||!/^\/api\/(?:v\d+\/)?webhooks\/\d+\/[A-Za-z\d._-]+\/?$/.test(url.pathname))throw new Error('discord.com의 HTTPS 웹훅 주소를 연결해주세요.');
   context.registerSecret(raw);context.registerSecret(url.pathname.split('/').filter(Boolean).at(-1));url.search='';url.hash='';url.searchParams.set('wait','true');
   const body:Json={content:text(p.content,'메시지',2000),allowed_mentions:{parse:[]}};
   const username=text(p.username,'표시 이름',80,true);if(username)body.username=username;
   const threadId=text(p.threadId,'스레드 ID',30,true);if(threadId){if(!/^\d+$/.test(threadId))throw new Error('스레드 ID는 숫자로 입력해주세요.');url.searchParams.set('thread_id',threadId);}
   const threadName=text(p.threadName,'새 스레드 이름',100,true);if(threadName)body.thread_name=threadName;
   const {data}=await request(url,'POST',body);add([object(data,'Discord 응답')],itemIndex);
  }else if(node.type==='easyflow.airtable'){
   const base=`https://api.airtable.com/v0/${identifier(p.baseId,'베이스 ID')}/${identifier(p.tableId,'테이블 ID 또는 이름')}`;
   if(operation==='listRecords'){
    const count=limit();let fetched=0,offset:string|undefined;const offsets=new Set<string>();
    for(let page=0;page<50&&fetched<count;page++){
     const url=new URL(base);url.searchParams.set('pageSize',String(Math.min(100,count-fetched)));url.searchParams.set('maxRecords',String(count));if(offset)url.searchParams.set('offset',offset);
     for(const key of ['filterByFormula','view']){const value=text(p[key],key==='view'?'뷰 이름':'필터 수식',8000,true);if(value)url.searchParams.set(key,value);}
     const {data}=await request(url),items=records(data.records,service).slice(0,count-fetched);add(items,itemIndex);fetched+=items.length;if(!data.offset||fetched>=count)break;
     const nextOffset=data.offset;if(typeof nextOffset!=='string'||offsets.has(nextOffset))throw new Error('Airtable의 다음 페이지 정보를 확인할 수 없어요.');offset=nextOffset;offsets.add(nextOffset);
     if(page===49)throw new Error('Airtable 조회가 50페이지를 넘었어요. 조회 범위를 줄여주세요.');
    }
   }else{
    const fields=object(p.fields,'레코드 필드'),record:Json={fields};if(operation==='updateRecord')record.id=text(p.recordId,'레코드 ID',200);
    const {data}=await request(new URL(base),operation==='createRecord'?'POST':'PATCH',{records:[record],typecast:false});add(records(data.records,service),itemIndex);
   }
  }else if(node.type==='easyflow.googleSheets'){
   const range=text(p.range,'셀 범위',1000),base=`https://sheets.googleapis.com/v4/spreadsheets/${identifier(p.spreadsheetId,'스프레드시트 ID')}/values/${encodeURIComponent(range)}`;
   if(operation==='read'){
    const {data}=await request(new URL(base));const rows=data.values??[];
    if(!Array.isArray(rows)||rows.some((row:any)=>!Array.isArray(row)))throw new Error('Google Sheets의 행 데이터를 확인할 수 없어요.');
    add(rows.map((values:any[],index:number)=>({range:data.range??range,rowIndex:index,values})),itemIndex);
   }else{
    const values=parsed(p.values,'셀 데이터');if(!Array.isArray(values)||!values.length||values.length>MAX_ITEMS||values.some(row=>!Array.isArray(row)||row.some((cell:any)=>cell!==null&&!['string','number','boolean'].includes(typeof cell))))throw new Error('셀 데이터는 문자·숫자·참거짓을 담은 행 배열로 입력해주세요. 예: [["이름", 92]]');
    const url=new URL(base+(operation==='append'?':append':''));url.searchParams.set('valueInputOption',oneOf(p.valueInputOption,['RAW','USER_ENTERED'],'셀 입력 방식','RAW'));if(operation==='append')url.searchParams.set('insertDataOption','INSERT_ROWS');
    const {data}=await request(url,operation==='append'?'POST':'PUT',{range,majorDimension:'ROWS',values});add([object(data,'Google Sheets 응답')],itemIndex);
   }
  }
 }
 return [attachItemPairs(output,pairs)];
}
