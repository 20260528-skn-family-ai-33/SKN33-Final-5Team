import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowRequestFeedback} from '../src/workflow-request-feedback';

test('missing input becomes an actionable follow-up without returning model internals',()=>{
 const feedback=workflowRequestFeedback({summary:'재고 흐름',checklist:[],questions:[],requirements:[{id:'sheet',description:'재고를 읽을 시트 지정',evidence:'secret',status:'needs_input',nodeIds:[]}]});
 assert.deepEqual(feedback.requirements,[{id:'sheet',description:'재고를 읽을 시트 지정',status:'needs_input'}]);
 assert.match(feedback.questions[0].question,/재고를 읽을 시트 지정/);
 assert.match(feedback.questions[0].question,/대상과 설정값/);
 assert.equal(JSON.stringify(feedback).includes('secret'),false);
});

test('unsupported requirements are surfaced and an unexplained empty result asks for detail',()=>{
 const unsupported=workflowRequestFeedback({summary:'확인 필요',checklist:[],requirements:[{id:'service',description:'지원하지 않는 외부 서비스',evidence:'요청',status:'unsupported',nodeIds:[]}]});
 assert.deepEqual(unsupported.questions,[]);
 assert.match(unsupported.checklist[0],/지원 여부 확인 필요/);
 const empty=workflowRequestFeedback({summary:'흐름',checklist:[]});
 assert.match(empty.questions[0].question,/사용할 데이터/);
});

test('feedback removes common credential strings from all visible fields',()=>{
 const token='xoxb-1234567890-secret';
 const feedback=workflowRequestFeedback({summary:`Bearer ${token}`,checklist:[`api_key=${token}`],questions:[{question:`토큰 ${token}?`,choices:[]}],requirements:[{id:'r',description:`Slack 연결 ${token}`,evidence:'요청',status:'needs_input',nodeIds:[]}]});
 assert.equal(JSON.stringify(feedback).includes(token),false);
});
