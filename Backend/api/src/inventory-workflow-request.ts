import {randomUUID} from 'node:crypto';
import {validateFlow,type FlowNode} from '../../../shared/workflow';
import type {AssistantResult,CredentialPublic} from '../../../shared/api-contracts';

/** A bounded, explicit shortcut for the inventory example when the shared AI server is unavailable. */
export function inventoryWorkflowFromRequest(prompt:unknown,credentials:CredentialPublic[]):AssistantResult|null {
 if(typeof prompt!=='string'||prompt.length>4000||
  !/(?:Google\s*Sheets|구글\s*시트|스프레드시트)/i.test(prompt)||
  !/(?:Slack|슬랙)/i.test(prompt)||!prompt.includes('재고')||
  !/(?:부족|기준.{0,15}(?:적|미만|이하)|(?:적|미만|이하).{0,15}기준)/.test(prompt)||
  !/(?:기록|추가)/.test(prompt)||
  /(?:Slack|슬랙).{0,24}(?:보내지|알리지|전송하지)|(?:기록|추가)하지/.test(prompt))return null;

 const spreadsheetIds=[...new Set([
  ...[...prompt.matchAll(/docs\.google\.com\/spreadsheets\/d\/([A-Za-z0-9_-]{25,100})/g)].map(match=>match[1]),
  ...[...prompt.matchAll(/스프레드시트\s*(?:ID\s*)?[:：]?\s*([A-Za-z0-9_-]{25,100})/gi)].map(match=>match[1]),
 ])];
 const spreadsheetId=spreadsheetIds.length===1?spreadsheetIds[0]:undefined;
 const foundRanges=[...prompt.matchAll(/(?:^|[\s'"“”])([\p{L}\p{N}_-]{1,60})!([A-Z]{1,3}\d*:[A-Z]{1,3}\d*)/gu)]
  .map(match=>`${match[1]}!${match[2]}`);
 const sourceRanges=[...new Set(foundRanges.filter(range=>/!A1:D\d+$/i.test(range)&&!/기록|결과/.test(range)))];
 const appendRanges=[...new Set(foundRanges.filter(range=>/!A:F$/i.test(range)))];
 const sourceRange=sourceRanges.length===1?sourceRanges[0]:undefined;
 const appendRange=appendRanges.length===1?appendRanges[0]:undefined;
 const channelIds=[...new Set([...prompt.matchAll(/(?:채널\s*(?:ID)?\s*[:：]?\s*)(C[A-Z0-9]{8,})\b/gi)].map(match=>match[1].toUpperCase()))];
 const channelId=channelIds.length===1?channelIds[0]:undefined;
 const timeMatches=[...prompt.matchAll(/매일\s*(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?/g)];
 const normalizedTimes=[...new Set(timeMatches.map(match=>{
  const raw=Number(match[2]),hour=match[1]==='오후'&&raw<12?raw+12:match[1]==='오전'&&raw===12?0:raw;
  return `${hour}:${Number(match[3]||0)}`;
 }))];
 const time=normalizedTimes.length===1?timeMatches[0]:undefined;
 const hourRaw=time?Number(time[2]):NaN,minute=time?Number(time[3]||0):NaN;
 const hour=time?.[1]==='오후'&&hourRaw<12?hourRaw+12:time?.[1]==='오전'&&hourRaw===12?0:hourRaw;
 const sheets=credentials.filter(item=>item.provider==='bearer'&&item.service==='googleSheets');
 const slack=credentials.filter(item=>item.provider==='slack'&&(item.service==='slack'||!item.service));
 const questions:string[]=[];
 if(!spreadsheetId)questions.push('읽고 기록할 Google Sheets 스프레드시트 ID 또는 주소를 알려주세요.');
 if(!sourceRange)questions.push('헤더가 첫 줄인 원본 범위를 `재고!A1:D1000` 형식으로 알려주세요.');
 if(!appendRange)questions.push('6개 값을 추가할 기록 범위를 `부족재고_기록!A:F` 형식으로 알려주세요.');
 if(!channelId)questions.push('메시지를 보낼 Slack 채널 ID(C로 시작)를 알려주세요.');
 if(!time||!Number.isInteger(hour)||hour<0||hour>23||!Number.isInteger(minute)||minute<0||minute>59)questions.push('매일 실행할 시간을 `오전 9시` 형식으로 하나만 알려주세요.');
 if(!/A열\s*(?:SKU|상품\s*코드)/i.test(prompt)||!/B열\s*상품명/.test(prompt)||!/C열\s*현재\s*수량/.test(prompt)||!/D열\s*기준\s*수량/.test(prompt))
  questions.push('원본 열 순서가 A=SKU, B=상품명, C=현재 수량, D=기준 수량인지 알려주세요.');
 if(/\b(?:Asia|America|Europe|Australia|Pacific|Etc)\/[A-Za-z_]+\b/.test(prompt)&&!/Asia\/Seoul/.test(prompt))questions.push('현재 초안은 Asia/Seoul 시간대만 지원해요. 서울 시간으로 실행할지 알려주세요.');
 if(sheets.length!==1)questions.push(sheets.length?'사용할 Google Sheets 연결 계정을 하나로 선택해주세요.':'서비스 연결에서 Google Sheets 계정을 연결해주세요.');
 if(slack.length!==1)questions.push(slack.length?'사용할 Slack 연결 계정을 하나로 선택해주세요.':'서비스 연결에서 Slack 계정을 연결해주세요.');
 if(questions.length)return {summary:'재고 부족 알림 초안을 만들려면 다음 정보를 확인해주세요.',questions:questions.slice(0,2).map(question=>({question,choices:[]})),checklist:questions.slice(2)};

 const dateCode="const date = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);";
 const code=`const shortages = $input.all()\n  .map(item => item.json)\n  .filter(row => row.rowIndex > 0 && Array.isArray(row.values))\n  .map(row => {\n    const [sku, name, quantityText, thresholdText] = row.values;\n    const quantity = Number(quantityText);\n    const threshold = Number(thresholdText);\n    return {sku: String(sku || ''), name: String(name || ''), quantity, threshold, deficit: threshold - quantity};\n  })\n  .filter(row => row.sku && row.name && Number.isFinite(row.quantity) && Number.isFinite(row.threshold) && row.deficit > 0);\nif (shortages.length === 0) return [];\n${dateCode}\nconst rows = shortages.map(row => [date, row.sku, row.name, row.quantity, row.threshold, row.deficit]);\nconst message = '*재고 부족 알림* (' + date + ')\\n' + shortages.map(row =>\n  '• ' + row.name + ' (' + row.sku + '): 현재 ' + row.quantity + ' / 기준 ' + row.threshold + ' · 부족 ' + row.deficit\n).join('\\n');\nreturn [{json: {rows, message, count: shortages.length}, pairedItem: {item: 0}}];`;
 const nodes:FlowNode[]=[
  {id:'start',name:'매일 정해진 시간',type:'n8n-nodes-base.scheduleTrigger',typeVersion:1.2,position:[100,300],parameters:{rule:{interval:[{field:'days',daysInterval:1,triggerAtHour:hour,triggerAtMinute:minute}]}}},
  {id:'read',name:'재고 시트 읽기',type:'easyflow.googleSheets',typeVersion:1,position:[350,300],parameters:{operation:'read',spreadsheetId,range:sourceRange},credentialId:sheets[0].id},
  {id:'filter',name:'부족 재고 정리',type:'n8n-nodes-base.code',typeVersion:2,position:[600,300],parameters:{mode:'runOnceForAllItems',language:'javaScript',jsCode:code}},
  {id:'append',name:'부족 재고 기록',type:'easyflow.googleSheets',typeVersion:1,position:[875,210],parameters:{operation:'append',spreadsheetId,range:appendRange,values:'={{ $json.rows }}',valueInputOption:'RAW'},credentialId:sheets[0].id},
  {id:'slack',name:'Slack 알림 보내기',type:'n8n-nodes-base.slack',typeVersion:2.3,position:[1125,210],parameters:{resource:'message',operation:'post',channelId:{__rl:true,mode:'id',value:channelId},messageType:'text','text':'={{ $("부족 재고 정리").first().json.message }}',otherOptions:{includeLinkToWorkflow:false}},credentialId:slack[0].id},
 ];
 const connections={
  '매일 정해진 시간':{main:[[{node:'재고 시트 읽기',type:'main',index:0}]]},
  '재고 시트 읽기':{main:[[{node:'부족 재고 정리',type:'main',index:0}]]},
  '부족 재고 정리':{main:[[{node:'부족 재고 기록',type:'main',index:0}]]},
  '부족 재고 기록':{main:[[{node:'Slack 알림 보내기',type:'main',index:0}]]},
 };
 const workflow=validateFlow({id:randomUUID(),name:`매일 ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')} 재고 부족 알림`,nodes,connections,settings:{executionOrder:'v1',timezone:'Asia/Seoul'},active:false});
 return {workflow,summary:'재고 시트를 읽고 기준보다 적은 상품을 기록한 뒤 Slack으로 알리는 초안을 만들었어요.',checklist:['저장된 단계와 범위를 확인하고 시험 실행한 뒤 자동 실행을 켜주세요.']};
}
