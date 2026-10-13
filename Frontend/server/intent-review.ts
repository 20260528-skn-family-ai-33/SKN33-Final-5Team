import type {Flow} from '../lib/workflow';
export type RequirementReview={id:string;description:string;evidence:string;status:'mapped'|'needs_input'|'unsupported';nodeIds:string[]};
export function groundedQuote(value:unknown,userTexts:string[]):string {
 if(typeof value!=='string')return '';
 if(userTexts.some(t=>t.includes(value)))return value;
 const pairs:Record<string,string>={'"':'"',"'":"'",'“':'”','‘':'’'};const text=value.trim();
 if(pairs[text[0]]===text.at(-1)){const inner=text.slice(1,-1);if(inner.trim()&&userTexts.some(t=>t.includes(inner)))return inner;}
 return value;
}
export function reviewRequirements(raw:unknown,userTexts:string[],flow?:Flow):RequirementReview[]{
 if(!Array.isArray(raw)||!raw.length||raw.length>60)throw new Error('요구사항 분석이 누락됐어요. 다시 질문해주세요.');
 const ids=new Set<string>();
 return raw.map(r=>{
  if(r&&typeof r==='object')r={...r,evidence:groundedQuote(r.evidence,userTexts)};
  if(!r||typeof r.id!=='string'||!r.id||ids.has(r.id)||typeof r.description!=='string'||!r.description.trim()||r.description.length>1000||typeof r.evidence!=='string'||!r.evidence.trim()||!userTexts.some(t=>t.includes(r.evidence))||!['mapped','needs_input','unsupported'].includes(r.status)||!Array.isArray(r.nodeIds)||r.nodeIds.some((id:unknown)=>typeof id!=='string'))throw new Error('요구사항의 사용자 원문·반영 상태를 확인하지 못했어요.');
  ids.add(r.id);
  if(r.status==='mapped'&&(!flow||!r.nodeIds.length||r.nodeIds.some((id:string)=>!flow.nodes.some(n=>n.id===id))))throw new Error('요구사항에 연결한 실제 단계가 없어요.');
  if(r.status!=='mapped'&&r.nodeIds.length)throw new Error('미확인 요구를 단계에 반영했다고 표시할 수 없어요.');
  return {id:r.id,description:r.description,evidence:r.evidence,status:r.status,nodeIds:[...new Set<string>(r.nodeIds)]};
 });
}
