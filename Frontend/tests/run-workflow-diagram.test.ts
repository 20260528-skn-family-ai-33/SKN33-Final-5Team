import assert from 'node:assert/strict';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {createElement, type ComponentProps} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {RunWorkflowDiagram, type RunWorkflow} from '../app/run-workflow-diagram';

const workflow:RunWorkflow = {
  entry:'check',
  nodes:[
    {id:'export',label:'파일 생성',tool:'create_spreadsheet'},
    {id:'ask',label:'담당자 확인',tool:'ask_user'},
    {id:'check',label:'금액 조건 확인',tool:'condition'},
    {id:'auto',label:'항목 추출',tool:'extract_information'},
  ],
  edges:[
    {source:'check',target:'auto',branch:'yes'}, {source:'check',target:'ask',branch:'no'},
    {source:'auto',target:'export',branch:'next'}, {source:'ask',target:'export',branch:'next'},
  ],
};
function render(props:ComponentProps<typeof RunWorkflowDiagram>) {
  return JSDOM.fragment(renderToStaticMarkup(createElement(RunWorkflowDiagram,props)));
}

test('branches and joins follow the saved edges and distinguish observed work from a waiting step', () => {
  const tree = render({workflow,steps:[{node:'check',label:'금액 조건 확인',status:'completed'}],
    runStatus:'needs_input',waitingNodeId:'ask'});
  const stages = [...tree.querySelectorAll<HTMLElement>('[data-workflow-node]')];
  assert.deepEqual(stages.map(el => el.dataset.workflowNode), ['check','auto','ask','export']);
  assert.equal(stages[0].querySelector('.rwd-status')?.textContent,'완료');
  assert.equal(stages[1].querySelector('.rwd-status')?.textContent,'기록 없음');
  assert.equal(stages[2].querySelector('.rwd-status')?.textContent,'답변 대기');
  assert.deepEqual([...stages[0].querySelectorAll('.rwd-branch')].map(el => el.textContent?.trim()),
    ['조건 충족 2단계 항목 추출','조건 미충족 3단계 담당자 확인']);
  assert.match(stages[1].querySelector('.rwd-branch')?.textContent ?? '',/4단계 파일 생성/);
  assert.match(stages[2].querySelector('.rwd-branch')?.textContent ?? '',/4단계 파일 생성/);
  assert.equal(stages[1].querySelector('.rwd-next'),null,'parallel alternatives must not be connected to each other');
});

test('historical stages without a graph keep their recorded order without claiming connections', () => {
  const tree = render({steps:[
    {node:'read',label:'파일 읽기',status:'completed'},{node:'read',label:'다시 읽기',status:'failed'},
  ],runStatus:'failed'});
  assert.equal(tree.querySelector('section')?.getAttribute('aria-label'),'실행 단계');
  assert.deepEqual([...tree.querySelectorAll('.rwd-copy strong')].map(el=>el.textContent),['파일 읽기','다시 읽기']);
  assert.deepEqual([...tree.querySelectorAll('.rwd-status')].map(el=>el.textContent),['완료','실패']);
  assert.equal(tree.querySelector('.rwd-next, .rwd-rail, .rwd-branch'),null);
});

test('a workflow preview contains no execution claims and an empty record has no fabricated stages', () => {
  assert.equal(render({workflow}).querySelector('.rwd-status'),null);
  assert.equal(render({workflow:null,steps:[]}).childElementCount,0);
});

test('long workflows expose every node inside a keyboard-accessible scroll region', () => {
  const nodes = Array.from({length:9}, (_,i)=>({id:String(i),label:`작업 ${i}`,tool:'read_document'}));
  const tree = render({workflow:{entry:'0',nodes,edges:nodes.slice(1).map((n,i)=>({source:String(i),target:n.id,branch:'next'}))}});
  const region = tree.querySelector('[role="region"][aria-label="전체 단계 보기"]');
  assert.equal(region?.getAttribute('tabindex'),'0');
  assert.equal(region?.querySelectorAll('[role="listitem"]').length,9);
  assert.equal(render({workflow}).querySelector('[role="region"]'),null);
});
