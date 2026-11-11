// Read-only compact evidence view. Never changes frozen gold or raw run artifacts.
import fs from 'node:fs';
const [path,...options]=process.argv.slice(2);
const corpusArgument=options.indexOf('--corpus');
const corpusPath=corpusArgument<0?new URL('../../docs/qwen-live-evaluation-cases.json',import.meta.url):options.splice(corpusArgument,2)[1];
const ids=options;
const report=JSON.parse(fs.readFileSync(path,'utf8'));
const corpus=JSON.parse(fs.readFileSync(corpusPath,'utf8'));
if(report.gold_sha256!==corpus.gold_sha256)throw Error('Select the frozen corpus matching this run');
const compact=x=>Array.isArray(x)?x.map(compact):x&&typeof x==='object'?Object.fromEntries(Object.entries(x).filter(([k,v])=>v!==null&&(!Array.isArray(v)||v.length)&&!['trace','requirements','evidence','evidence_ids','question_options','document_ids','tool_ids'].includes(k)).map(([k,v])=>[k,compact(v)])):x;
for(const row of report.cases.filter(c=>!ids.length||ids.includes(c.id))){
 const gold=corpus.cases.find(c=>c.id===row.id);
 const stages=row.stages||[];
 const first=name=>stages.find(s=>s.stage===name)?.parsed_state;
 const {intent,slots,missing_required_slots,final_slots}=gold.gold;
 console.log(JSON.stringify({id:row.id,request:gold.request,gold:{intent,slots,missing_required_slots,final_slots},split:gold.split,status:row.status,calls:row.live_model_calls,
   actual_route:row.actual_route,final_route:row.final_route,stop:row.answer_stop_reason,error:row.error_type,
   turns:row.turns?.map(t=>({user:t.user,status:t.response.status,questions:t.response.pending_questions,issues:t.response.issues,
     message:t.response.messages?.at(-1),schedule:t.response.draft?.schedule,plan:t.response.draft?.execution_plan})),
   meaning:first('request_meaning')?.request_meaning,capability:first('capability_check')?.capability_assessment,
   conditions:stages.filter(s=>s.stage==='condition_check').map(s=>s.parsed_state.condition_ledger),
   requirements:stages.filter(s=>s.stage==='requirements').map(s=>s.parsed_state.requirement_ledger),
   workflow:compact(row.workflow),execution:row.execution_success,execution_rows:row.local_execution?.rows,
   timing:row.elapsed_seconds,model_stages:row.model_calls?.map(c=>({stage:c.stage,status:c.status,model:c.actual_model,time:c.elapsed_seconds}))
 },(k,v)=>['gold','conditions','requirements','capability','meaning'].includes(k)?compact(v):v));
}
