import {z} from 'zod';

const uuid=z.string().uuid();
export const toolGapChoiceRequestSchema=z.object({
 id:uuid,revision:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),requestId:uuid,
 gapId:z.string().regex(/^gap_[a-f0-9]{24}$/),stateFingerprint:z.string().regex(/^[a-f0-9]{64}$/),
 choice:z.enum(['create_tool','redesign_workflow','defer']),
}).strict();
export type ToolGapChoiceRequest=z.infer<typeof toolGapChoiceRequestSchema>;
export function parseToolGapChoiceRequest(value:unknown):ToolGapChoiceRequest{
 const parsed=toolGapChoiceRequestSchema.safeParse(value);
 if(!parsed.success)throw new Error('현재 대화의 선택 항목과 버전을 확인하세요.');
 return parsed.data;
}
export type ToolGapChoiceView={
 protocol:'a4b.tool-gap-choice.v1';gap_id:string;revision:number;state_fingerprint:string;
 capability_status:'unverified';status:'awaiting_choice'|'creation_requested'|'redesign_requested'|'deferred';
 prompt:string;missing_actions:string[];options:{id:ToolGapChoiceRequest['choice'];label:string}[];
 next_action:'wait_for_user'|'review_spec_then_generate'|'propose_existing_tool_alternative';
 selection:{id:string;request_id:string;choice:ToolGapChoiceRequest['choice'];input_revision:number;result_revision:number}|null;
 requirement_changes_approved:false;workflow_execution_allowed:false;automatic_registration:false;
};
export function choiceRequestFromView(sessionId:string,view:ToolGapChoiceView,choice:ToolGapChoiceRequest['choice'],requestId:string){
 return parseToolGapChoiceRequest({id:sessionId,revision:view.revision,requestId,gapId:view.gap_id,stateFingerprint:view.state_fingerprint,choice});
}
export const redesignDecisionRequestSchema=z.object({id:uuid,revision:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
 requestId:uuid,proposalId:uuid,decision:z.enum(['accept','reject','defer'])}).strict();
export function parseRedesignDecisionRequest(value:unknown){
 const result=redesignDecisionRequestSchema.safeParse(value);
 if(!result.success)throw new Error('현재 재설계 대안과 확인·거절·보류를 선택하세요.');
 return result.data;
}
