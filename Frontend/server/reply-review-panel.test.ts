import assert from 'node:assert/strict';
import test from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ReplyReviewPanel, type ReplyReview} from '../app/reply-review-panel';

const review:ReplyReview = {
 id:'review:0',customer_message:'환불 문의',reply_draft:'완료했습니다.',
 report:{verdict:'revise',summary:'완료 근거 없음',requests:['환불'],findings:[],missing_info:[],revised_reply:'검수 단계의 이전 수정안'},
 tool_calls:[],sources:[]
};
function render(item:ReplyReview){
 return renderToStaticMarkup(createElement(ReplyReviewPanel,{reviews:[item],runId:'run-id',disabled:false,canDecide:true,onDecision:async()=>{}}));
}
test('approval textarea starts with the latest composed candidate, preserving the original report',()=>{
 const html=render({...review,candidate_reply:'후속 단계의 최종 수정문',candidate_revision:1});
 assert.match(html,/<textarea[^>]*>후속 단계의 최종 수정문<\/textarea>/);
 assert.doesNotMatch(html,/<textarea[^>]*>검수 단계의 이전 수정안<\/textarea>/);
 assert.equal(review.report.revised_reply,'검수 단계의 이전 수정안');
});
test('legacy review without candidate metadata still displays its original proposal',()=>{
 assert.match(render(review),/<textarea[^>]*>검수 단계의 이전 수정안<\/textarea>/);
});
test('an approved decision displays the operator edit even if an AI candidate exists',()=>{
 const html=render({...review,candidate_reply:'AI 수정문',candidate_revision:1,decision:{decision:'approve',edited_reply:'운영자가 승인한 문장',decided_at:'2026-10-13T00:00:00Z'}});
 assert.ok(html.includes('운영자가 승인한 문장'));
 assert.doesNotMatch(html,/<textarea/);
});
