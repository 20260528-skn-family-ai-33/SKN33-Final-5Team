import type {ReactNode} from 'react';
import './run-answer.css';

type Block=
 | {kind:'heading';text:string}
 | {kind:'paragraph';lines:string[]}
 | {kind:'list';ordered:boolean;items:string[]}
 | {kind:'quote';lines:string[]}
 | {kind:'code';text:string};

const heading=/^\s{0,3}#{1,3}\s+(.+)$/;
const bullet=/^\s*[-*•]\s+(.+)$/;
const numbered=/^\s*\d+[.)]\s+(.+)$/;
const quote=/^\s*>\s?(.*)$/;
const fence=/^\s*```/;

function special(line:string){return heading.test(line)||bullet.test(line)||numbered.test(line)||quote.test(line)||fence.test(line);}

export function answerBlocks(value:string):Block[]{
 const lines=value.replace(/\r\n?/g,'\n').split('\n'),blocks:Block[]=[];
 for(let i=0;i<lines.length;){
  const line=lines[i];
  if(!line.trim()){i++;continue;}
  if(fence.test(line)){
   i++;const contents:string[]=[];
   while(i<lines.length&&!fence.test(lines[i]))contents.push(lines[i++]);
   if(i<lines.length)i++;
   blocks.push({kind:'code',text:contents.join('\n')});continue;
  }
  const title=heading.exec(line);
  if(title){blocks.push({kind:'heading',text:title[1].trim()});i++;continue;}
  const marker=bullet.exec(line)||numbered.exec(line);
  if(marker){const ordered=!!numbered.exec(line),items:string[]=[];
   while(i<lines.length){const item=(ordered?numbered:bullet).exec(lines[i]);if(!item)break;items.push(item[1].trim());i++;}
   blocks.push({kind:'list',ordered,items});continue;
  }
  if(quote.test(line)){
   const contents:string[]=[];
   while(i<lines.length){const part=quote.exec(lines[i]);if(!part)break;contents.push(part[1]);i++;}
   blocks.push({kind:'quote',lines:contents});continue;
  }
  const contents:string[]=[];
  while(i<lines.length&&lines[i].trim()&&!special(lines[i]))contents.push(lines[i++]);
  if(contents.length)blocks.push({kind:'paragraph',lines:contents});
 }
 return blocks;
}

const inlinePattern=/\[([^\]\n]{1,120})\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`|https?:\/\/[^\s<>]+/g;

function safeLink(url:string,label:string,key:string):ReactNode{
 try{
  const parsed=new URL(url);
  if(parsed.protocol==='http:'||parsed.protocol==='https:')return <a key={key} href={parsed.href} target="_blank" rel="noopener noreferrer">{label}</a>;
 }catch{}
 return url;
}

function inline(value:string,prefix='inline'):ReactNode[]{
 const rendered:ReactNode[]=[];let previous=0,index=0;
 for(const match of value.matchAll(inlinePattern)){
  const position=match.index??0;
  if(position>previous)rendered.push(value.slice(previous,position));
  if(match[1]&&match[2])rendered.push(safeLink(match[2],match[1],prefix+'-'+index++));
  else if(match[3])rendered.push(<strong key={prefix+'-'+index++}>{match[3]}</strong>);
  else if(match[4])rendered.push(<code key={prefix+'-'+index++}>{match[4]}</code>);
  else{
   const raw=match[0],url=raw.replace(/[.,;!?]+$/,'');
   rendered.push(safeLink(url,url,prefix+'-'+index++));
   if(url!==raw)rendered.push(raw.slice(url.length));
  }
  previous=position+match[0].length;
 }
 if(previous<value.length)rendered.push(value.slice(previous));
 return rendered;
}

function withBreaks(lines:string[]):ReactNode[]{
 return lines.flatMap((line,index)=>index?[<br key={'br-'+index}/>,...inline(line,'line-'+index)]:inline(line,'line-'+index));
}

function formattedJson(lines:string[]):string|null{
 const raw=lines.join('\n').trim();
 if(!/^[\[{]/.test(raw))return null;
 try{return JSON.stringify(JSON.parse(raw),null,2);}catch{return null;}
}

export function RunAnswer({text}:{text:string}){
 const trimmed=text.trim();
 if(!trimmed)return null;
 const blocks=answerBlocks(trimmed);
 return <section className="as-run-answer" role="document" aria-label="실행 답변">
  {blocks.map((block,index)=>{
   if(block.kind==='heading')return <h3 key={index}>{inline(block.text)}</h3>;
   if(block.kind==='paragraph'){
    const json=formattedJson(block.lines);
    return json===null?<p key={index}>{withBreaks(block.lines)}</p>:<pre key={index}><code>{json}</code></pre>;
   }
   if(block.kind==='quote')return <blockquote key={index}>{withBreaks(block.lines)}</blockquote>;
   if(block.kind==='code')return <pre key={index}><code>{block.text}</code></pre>;
   const List=block.ordered?'ol':'ul';
   return <List key={index}>{block.items.map((item,itemIndex)=><li key={itemIndex}>{inline(item)}</li>)}</List>;
  })}
 </section>;
}
