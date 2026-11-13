import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontend=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(frontend,'app/gmail-summary-result.tsx')],absWorkingDir:frontend,
 bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const module={exports:{} as any};
new Function('require','module','exports',compiled)(require,module,module.exports);
const {GmailSummaryResult,parseGmailSummary,isGmailSlackSummaryWorkflow}=module.exports;

const workflow={entry:'mail',nodes:[
 {id:'mail',tool:'email.list',label:'안 읽은 메일 읽기',bindings:[],settings:{operation:'read_messages',arguments_json:JSON.stringify({max_results:3,query:'is:unread -in:sent -in:drafts'})}},
 {id:'summary',tool:'compose_answer',label:'메일 요약',bindings:[{input:'documents',node_id:'mail',output:'documents'}],settings:{}},
 {id:'post',tool:'slack.post_message',label:'Slack 전송',bindings:[{input:'text',node_id:'summary',output:'text'}],settings:{operation:'execute',arguments_json:'{"channel_id":"C0123456789"}'}},
],edges:[{source:'mail',target:'summary',branch:'next'},{source:'summary',target:'post',branch:'next'}]};
const receipt='{"status":"accepted_by_provider","channel_id":"C0123456789","ts":"123.000"}';
const longBody='Slack Pro 무료 평가판이 시작되었습니다. '.repeat(20)+'https://example.com/detail';
const freeform=`조회한 안 읽은 메일 요약:\n\n제목: Slack Pro 무료 평가판을 시작했습니다.\n보낸 사람: Slack <no-reply@slack.com>\n${longBody}\n\n조회 범위: 최근 안 읽은 메일 최대 3건\n\n${receipt}`;

const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','navigator','HTMLElement','Element','Node','Event','MutationObserver']){
 Object.defineProperty(globalThis,key,{value:(dom.window as any)[key],configurable:true});
}
Object.defineProperty(globalThis,'IS_REACT_ACT_ENVIRONMENT',{value:true,configurable:true});
const {createElement,act}=require('react');
const {createRoot}=require('react-dom/client');
const host=document.getElementById('root')!;
const root=createRoot(host);
async function render(answer:string,flow:any=workflow){await act(async()=>{root.render(createElement(GmailSummaryResult,{answer,workflow:flow}));});}

test('Gmail→Slack cards show only verified mail fields and keep the full answer collapsible',async()=>{
 assert.equal(isGmailSlackSummaryWorkflow(workflow),true);
 await render(freeform);
 const card=host.querySelector('.gmr-card')!;
 assert.ok(card);
 assert.equal(card.querySelector('h3')?.textContent,'Slack Pro 무료 평가판을 시작했습니다.');
 assert.equal(card.querySelector('.gmr-card-heading p')?.textContent,'Slack <no-reply@slack.com>');
 assert.match(host.querySelector('.gmr-count')!.textContent!,/표시된 메일 1건 · 최대 3건 조회/);
 assert.match(card.querySelector('.gmr-card-content > p')!.textContent!,/Slack Pro 무료 평가판/);
 assert.ok(card.querySelector('.gmr-card-content > p')!.textContent!.length<300);
 assert.doesNotMatch(card.querySelector('.gmr-card-content')!.textContent!,/channel_id|accepted_by_provider|조회 범위/);
 assert.match(host.querySelector('.gmr-notices')!.textContent!,/조회 범위/);
 assert.equal(host.querySelector('.gmr-full')?.hasAttribute('open'),false);
 assert.equal(host.querySelector('.gmr-full p')?.textContent,freeform.slice(0,-('\n\n'+receipt).length));
 assert.doesNotMatch(host.textContent!,/accepted_by_provider|channel_id/);
 assert.doesNotMatch(host.querySelector('.gmr-card-heading')!.textContent!,/전송 성공|채널|날짜/);
});

test('multiple explicit title/sender sections become separate cards with explicit key points',async()=>{
 const answer='제목: 첫 메일\n보낸 사람: a@example.com\n핵심: 회의 시간 확정\n- 참석자 확인\n\n제목: 둘째 메일\n보낸 사람: b@example.com\n핵심: 결제 예정';
 await render(answer);
 const cards=host.querySelectorAll('.gmr-card');
 assert.equal(cards.length,2);
 assert.equal(cards[0].querySelector('h3')?.textContent,'첫 메일');
 assert.match(cards[0].querySelector('ul')!.textContent!,/회의 시간 확정.*참석자 확인/);
 assert.equal(cards[1].querySelector('h3')?.textContent,'둘째 메일');
 assert.match(cards[1].querySelector('ul')!.textContent!,/결제 예정/);
});

test('missing sender and empty result fall back without inventing email cards',async()=>{
 for(const answer of ['제목: 발신자 없는 메일\n본문 내용','조회한 안 읽은 메일이 없습니다.']){
  assert.equal(parseGmailSummary(answer,workflow),null);
  await render(answer);
  assert.equal(host.querySelector('.gmr-card'),null);
  assert.ok(host.querySelector('.as-run-answer'));
  assert.match(host.textContent!,new RegExp(answer.split('\n')[0]));
 }
});

test('unrelated workflow stays outside the Gmail card parser',async()=>{
 const other={...workflow,nodes:[{...workflow.nodes[0],tool:'read_document'},...workflow.nodes.slice(1)]};
 assert.equal(isGmailSlackSummaryWorkflow(other),false);
 await render(freeform,other);
 assert.equal(host.querySelector('.gmr-card'),null);
 assert.ok(host.querySelector('.as-run-answer'));
 assert.match(host.textContent!,/Slack Pro 무료 평가판을 시작했습니다/);
 assert.match(host.textContent!,/accepted_by_provider|channel_id/);
});

test('Gmail card fallback omits only Slack delivery receipts',async()=>{
 const answer='조회한 안 읽은 메일이 없습니다.\n\n'+receipt;
 await render(answer);
 assert.ok(host.querySelector('.as-run-answer'));
 assert.match(host.textContent!,/조회한 안 읽은 메일이 없습니다/);
 assert.doesNotMatch(host.textContent!,/accepted_by_provider|channel_id/);
});

test('batched summary headings stay outside mail cards and collection notes remain visible',async()=>{
 const answer='조회된 메일 1건을 2개 묶음으로 나누어 요약했습니다.\n\n### 메일 요약 1/2\n제목: 긴 메일\n보낸 사람: a@example.com\n앞부분의 핵심입니다.\n\n### 메일 요약 2/2\n제목: 긴 메일\n보낸 사람: a@example.com\n뒷부분의 핵심입니다.\n\n검색 결과가 더 있어 이번 요약에는 최대 3건만 포함했습니다.';
 await render(answer);
 assert.equal(host.querySelectorAll('.gmr-card').length,2);
 assert.match(host.querySelector('.gmr-count')!.textContent!,/요약 카드 2개/);
 assert.doesNotMatch(host.querySelector('.gmr-count')!.textContent!,/표시된 메일 2건/);
 assert.match(host.querySelector('.gmr-notices')!.textContent!,/조회된 메일 1건|검색 결과가 더 있어/);
 assert.doesNotMatch(host.querySelector('.gmr-card-content')!.textContent!,/### 메일 요약/);
});

test.after(async()=>{await act(async()=>{root.unmount();});dom.window.close();});
