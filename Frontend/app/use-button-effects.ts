import {useEffect} from 'react';
import './button-effects.css';

// Delegate pointer tracking so native buttons and portalled menus share the effect.
export function useButtonEffects() {
 useEffect(()=>{
  let frame=0;
  let pending:{button:HTMLButtonElement;x:number;y:number}|null=null;
  const track=(event:PointerEvent)=>{
   if(event.pointerType==='touch'||!(event.target instanceof Element))return;
   const button=event.target.closest<HTMLButtonElement>('button:not(:disabled):not([aria-disabled="true"])');
   if(!button)return;
   pending={button,x:event.clientX,y:event.clientY};
   if(frame)return;
   frame=requestAnimationFrame(()=>{
    frame=0;
    if(!pending?.button.isConnected)return;
    const {button,x,y}=pending,rect=button.getBoundingClientRect();
    button.style.setProperty('--button-glow-x',`${x-rect.left}px`);
    button.style.setProperty('--button-glow-y',`${y-rect.top}px`);
   });
  };
  document.addEventListener('pointermove',track,{passive:true});
  return()=>{document.removeEventListener('pointermove',track);if(frame)cancelAnimationFrame(frame);};
 },[]);
}
