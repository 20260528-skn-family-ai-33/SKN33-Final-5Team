import type {Workflow} from './workflow-preview';
import {RunAnswer} from './run-answer';
import './gmail-summary-result.css';

type MailSummary={title:string;sender:string;points:string[];body:string};
type SummaryResult={mails:MailSummary[];maxResults?:number;unread:boolean;notices:string[];batched:boolean};

const titleLine=/^\s*(?:(?:[-•*]|\d+[.)])\s*)?(?:\*\*)?(?:제목|subject)(?:\*\*)?\s*[:：]\s*(.+?)\s*$/i;
const senderLine=/^\s*(?:[-•*]\s*)?(?:\*\*)?(?:보낸 사람|발신자|from)(?:\*\*)?\s*[:：]\s*(.+?)\s*$/i;
const pointLine=/^\s*(?:[-•*]\s*)?(?:\*\*)?(?:핵심(?: 내용)?|요약)(?:\*\*)?\s*[:：]\s*(.*?)\s*$/i;
const bulletLine=/^\s*[-•]\s+(.+)$/;

export function isGmailSlackSummaryWorkflow(workflow?:Workflow|null):boolean {
 if(!workflow||workflow.nodes.length!==3)return false;
 const [mail,summary,slack]=workflow.nodes;
 return workflow.entry===mail.id && mail.tool==='email.list' && mail.settings.operation==='read_messages'
  && summary.tool==='compose_answer' && summary.bindings.some(binding=>binding.input==='documents'&&binding.node_id===mail.id&&binding.output==='documents')
  && slack.tool==='slack.post_message' && slack.settings.operation==='execute'
  && slack.bindings.some(binding=>binding.input==='text'&&binding.node_id===summary.id&&binding.output==='text');
}

function slackReceipt(block:string):boolean {
 try{
  const value=JSON.parse(block);
  return !!value&&typeof value==='object'&&!Array.isArray(value)
   && (value.status==='accepted_by_provider'&&typeof value.channel_id==='string'
    || typeof value.channel==='string'&&typeof value.ts==='string');
 }catch{return false;}
}

function withoutSlackReceipts(answer:string):string {
 return answer.replace(/\r\n?/g,'\n').split(/\n\s*\n/).filter(block=>!slackReceipt(block.trim())).join('\n\n').trim();
}

function plainSummary(answer:string):{text:string;notices:string[]} {
 const notices:string[]=[];
 const blocks=withoutSlackReceipts(answer).split(/\n\s*\n/).filter(block=>{
  const trimmed=block.trim();
  if(/^(?:조회 범위:|조회된 메일 \d+건을 \d+개 묶음으로|검색 결과가 더 있어|긴 메일 \d+건은|메일 \d+건은 읽을 수 있는)/.test(trimmed)){
   notices.push(trimmed);return false;
  }
  return true;
 });
 return {text:blocks.join('\n\n'),notices};
}

export function parseGmailSummary(answer:string,workflow?:Workflow|null):SummaryResult|null {
 if(!isGmailSlackSummaryWorkflow(workflow))return null;
 const {text,notices}=plainSummary(answer);
 const lines=text.split('\n');
 const batched=lines.some(line=>/^\s*#{1,3}\s*메일 요약 \d+\/\d+\s*$/.test(line));
 const mails:{title:string;sender:string;points:string[];body:string[];inPoints:boolean}[]=[];
 let current:typeof mails[number]|null=null;
 for(const line of lines){
  if(/^\s*#{1,3}\s*메일 요약 \d+\/\d+\s*$/.test(line))continue;
  const title=line.match(titleLine);
  if(title){current={title:title[1].trim(),sender:'',points:[],body:[],inPoints:false};mails.push(current);continue;}
  if(!current)continue;
  const sender=line.match(senderLine);
  if(sender&&!current.sender){current.sender=sender[1].trim();continue;}
  const point=line.match(pointLine);
  if(point){if(point[1].trim())current.points.push(point[1].trim());current.inPoints=true;continue;}
  const bullet=current.inPoints&&line.match(bulletLine);
  if(bullet){current.points.push(bullet[1].trim());continue;}
  if(line.trim())current.inPoints=false;
  current.body.push(line);
 }
 const ready=mails.filter(mail=>mail.title&&mail.sender).map(mail=>({title:mail.title,sender:mail.sender,points:mail.points,body:mail.body.join('\n').trim()}));
 if(!ready.length||ready.length!==mails.length)return null;
 const reader=workflow!.nodes[0];
 let maxResults:number|undefined,unread=false;
 try{
  const args=JSON.parse(String(reader.settings.arguments_json||'{}'));
  if(Number.isInteger(args.max_results)&&args.max_results>0)maxResults=args.max_results;
  unread=typeof args.query==='string'&&/(?:^|\s)is:unread(?:\s|$)/i.test(args.query);
 }catch{/* The cards can still show verified mail headings. */}
 return {mails:ready,maxResults,unread,notices,batched};
}

function preview(body:string):string {
 const compact=body.replace(/\s+/g,' ').trim();
 if(compact.length<=260)return compact;
 const first=compact.slice(0,260).replace(/\s+\S*$/,'').trimEnd();
 return (first||compact.slice(0,260))+'…';
}

export function GmailSummaryResult({answer,workflow}:{answer:string;workflow?:Workflow|null}){
 if(!isGmailSlackSummaryWorkflow(workflow))return <RunAnswer text={answer}/>;
 const displayAnswer=withoutSlackReceipts(answer);
 const parsed=parseGmailSummary(answer,workflow);
 if(!parsed)return <RunAnswer text={displayAnswer}/>;
 return <section className="gmr" aria-label="Gmail 메일 요약 결과">
  <header className="gmr-heading"><div><p className="gmr-kicker">Gmail 요약</p><h2>{parsed.unread?'안 읽은 Gmail 메일 요약':'Gmail 메일 요약'}</h2><p className="gmr-count">{parsed.batched?'요약 카드 '+parsed.mails.length+'개':'표시된 메일 '+parsed.mails.length+'건'}{parsed.maxResults?' · 최대 '+parsed.maxResults+'건 조회':''}</p></div></header>
  <div className="gmr-list">{parsed.mails.map((mail,index)=><article className="gmr-card" key={index}>
   <div className="gmr-card-heading"><span>{parsed.batched?'요약 항목':'메일'} {index+1}</span><h3>{mail.title}</h3><p>{mail.sender}</p></div>
   <div className="gmr-card-content"><strong>{mail.points.length?'핵심 내용':'요약 미리보기'}</strong>{mail.points.length?<ul>{mail.points.slice(0,3).map((point,i)=><li key={i}>{point}</li>)}</ul>:mail.body?<p>{preview(mail.body)}</p>:<p>요약 내용은 전체 결과 텍스트에서 확인하세요.</p>}
    {mail.body&&<details className="gmr-original"><summary>요약 내용 전체 보기</summary><p>{mail.body}</p></details>}
   </div>
  </article>)}</div>
  {parsed.notices.length>0&&<div className="gmr-notices" role="note">{parsed.notices.map((notice,index)=><p key={index}>{notice}</p>)}</div>}
  <details className="gmr-full"><summary>전체 결과 텍스트 보기</summary><p>{displayAnswer}</p></details>
 </section>;
}
