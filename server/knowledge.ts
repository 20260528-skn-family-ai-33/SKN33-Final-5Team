import {createHash} from 'node:crypto';
import type {FlowNode,Json} from '../lib/workflow';
import {attachItemPairs} from './provenance';
export type Citation={filename:string;version:string;contentHash:string;start:number;end:number;line:number;quote:string;context:string;step:string};
export function verifyCitation(c:Citation,text:string){return Number.isInteger(c.start)&&Number.isInteger(c.end)&&c.start>=0&&c.end>c.start&&c.end<=text.length&&createHash('sha256').update(text).digest('hex')===c.contentHash&&text.slice(c.start,c.end)===c.quote;}
export function searchDocument(node:FlowNode,input:Json[],resolve:(value:any,item:Json)=>any):Json[][]{
 const output=input.map(item=>{
  const text=resolve(node.parameters.text,item),query=resolve(node.parameters.query,item),filename=resolve(node.parameters.filename,item);
  if(typeof text!=='string'||!text.trim()||text.length>200000)throw new Error('검색할 실제 문서 본문은 1~200,000자로 연결해주세요.');
  if(typeof query!=='string'||!query.trim()||query.length>2000)throw new Error('문서에서 확인할 질문을 입력해주세요.');
  if(typeof filename!=='string'||!filename.trim()||filename.length>240)throw new Error('원본 문서 이름을 연결해주세요.');
  const limit=Number(node.parameters.limit??3);if(!Number.isInteger(limit)||limit<1||limit>10)throw new Error('근거 개수는 1~10개로 설정해주세요.');
  const hash=createHash('sha256').update(text).digest('hex'),terms=[...new Set(query.toLowerCase().match(/[a-z0-9가-힣]{2,}/g)||[])];
  const chunks=[...text.matchAll(/[^\r\n]+/g)].flatMap(m=>{const line=m[0],start=m.index!;const result:{start:number;end:number;score:number}[]=[];for(let i=0;i<line.length;i+=700){const quote=line.slice(i,i+900);const score=terms.filter(t=>quote.toLowerCase().includes(t)).length;if(score)result.push({start:start+i,end:start+i+quote.length,score});}return result;});
  const citations:Citation[]=chunks.sort((a,b)=>b.score-a.score||a.start-b.start).slice(0,limit).map(c=>({filename,version:hash.slice(0,12),contentHash:hash,start:c.start,end:c.end,line:text.slice(0,c.start).split('\n').length,quote:text.slice(c.start,c.end),context:text.slice(Math.max(0,c.start-200),Math.min(text.length,c.end+200)),step:node.name}));
  if(citations.some(c=>!verifyCitation(c,text)))throw new Error('원문 인용 검증에 실패했어요.');
  return {...item,documentEvidence:{query,method:'keyword',status:citations.length?'found':'no_evidence',citations,note:'실제 원문·해시·위치 일치 검사이며 의미적 답변 검증은 아닙니다.'}};
 });
 return [attachItemPairs(output,output.map((_,i)=>[{item:i}]))];
}
