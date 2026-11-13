import type {AssistantResult} from '../../../shared/api-contracts';

function safeText(value:string,limit:number){
 return value.slice(0,limit)
  .replace(/Bearer\s+[^\s,;"']+/gi,'Bearer [보호된 값]')
  .replace(/\b(?:xox[baprs]-[A-Za-z0-9-]+|ya29\.[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]{8,})\b/g,'[보호된 값]')
  .replace(/\b(api[_ -]?key|client[_ -]?secret|access[_ -]?token|refresh[_ -]?token)\s*[:=]\s*[^\s,;"']+/gi,'$1: [보호된 값]');
}

/** Keep an incomplete design useful without exposing model internals or credentials. */
export function workflowRequestFeedback(result:AssistantResult){
 const requirements=(result.requirements||[]).slice(0,60).map(item=>({
  id:safeText(item.id,120),
  description:safeText(item.description,1000),
  status:item.status,
 }));
 const questions=(result.questions||[]).slice(0,2).map(item=>({question:safeText(item.question,500)}));
 const checklist=(result.checklist||[]).slice(0,10).map(item=>safeText(item,1000));
 if(!questions.length){
  for(const item of requirements.filter(item=>item.status==='needs_input').slice(0,2))
   questions.push({question:`${item.description} — 진행에 필요한 대상과 설정값을 알려주세요.`.slice(0,500)});
 }
 for(const item of requirements.filter(item=>item.status==='unsupported')){
  if(checklist.length>=10)break;
  checklist.push(`지원 여부 확인 필요: ${item.description}`.slice(0,1000));
 }
 if(!questions.length&&!checklist.length)questions.push({question:'사용할 데이터, 처리 조건, 결과를 보낼 곳을 더 구체적으로 알려주세요.'});
 return {summary:safeText(result.summary,1500),questions,checklist,requirements};
}
