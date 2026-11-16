import {useEffect,useRef,useState} from 'react';
import './studio-message.css';

export function StudioMessage({role,content,animate=false}:{role:string;content:string;animate?:boolean}){
 const [shown,setShown]=useState(animate&&role==='assistant'?0:content.length),[finished,setFinished]=useState<string|null>(null);
 const paragraph=useRef<HTMLParagraphElement>(null);
 const canAnimate=role==='assistant'&&animate&&finished!==content;
 useEffect(()=>{
  if(!canAnimate||!window.matchMedia||window.matchMedia('(prefers-reduced-motion: reduce)').matches){setShown(content.length);return;}
  const media=window.matchMedia('(prefers-reduced-motion: reduce)');
  const characters=Array.from(content),step=Math.max(1,Math.ceil(characters.length/60));
  let count=0,timer:ReturnType<typeof setTimeout>|undefined,cancelled=false;
  setShown(0);
  const reveal=()=>{
   if(cancelled)return;
   count=Math.min(count+step,characters.length);
   setShown(characters.slice(0,count).join('').length);
   if(count<characters.length)timer=setTimeout(reveal,24);
  };
  const stop=()=>{if(media.matches){cancelled=true;clearTimeout(timer);setShown(content.length);}};
  media.addEventListener?.('change',stop);
  timer=setTimeout(reveal,24);
  return()=>{cancelled=true;clearTimeout(timer);media.removeEventListener?.('change',stop);};
 },[content,canAnimate]);
 useEffect(()=>{
  const messages=paragraph.current?.closest<HTMLElement>('.as-messages');
  if(messages&&messages.scrollHeight-messages.clientHeight-messages.scrollTop<80)messages.scrollTo?.({top:messages.scrollHeight});
 },[shown]);
 const typing=canAnimate&&shown<content.length;
 return <div className={'as-message '+role}><span className="as-message-label">{role==='user'?'나':'A4B'}</span><p ref={paragraph}>{typing?<><span className="sr-only">{content}</span><span aria-hidden="true">{content.slice(0,shown)}<span className="studio-typing-caret"/></span></>:content}</p>{typing&&<button type="button" className="studio-reveal-message" onClick={()=>{setFinished(content);setShown(content.length);}}>전체 보기</button>}</div>;
}
