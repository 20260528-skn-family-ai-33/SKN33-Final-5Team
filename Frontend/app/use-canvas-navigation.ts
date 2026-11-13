import {useEffect,useRef,useState,type PointerEvent} from 'react';

const clamp = (value:number) => Math.min(2,Math.max(.35,value));

export function useCanvasNavigation(enabled:boolean) {
  const viewport=useRef<HTMLDivElement>(null);
  const [zoom,setZoom]=useState(1);
  const [pan,setPan]=useState({x:0,y:0});
  const [dragging,setDragging]=useState(false);
  const current=useRef({zoom,pan});
  current.current={zoom,pan};
  const drag=useRef<{id:number;x:number;y:number;pan:{x:number;y:number}}|null>(null);

  function changeZoom(value:number,point?:{x:number;y:number}) {
    const element=viewport.current;
    const canvas=element?.querySelector('.as-flow-canvas');
    const before=current.current;
    const next=clamp(value);
    if(!element||!canvas||next===before.zoom)return;
    const bounds=canvas.getBoundingClientRect();
    const view=element.getBoundingClientRect();
    const anchor=point??{x:view.left+view.width/2,y:view.top+view.height/2};
    const ratio=next/before.zoom;
    const style=getComputedStyle(element);
    const innerWidth=element.clientWidth-parseFloat(style.paddingLeft||'0')-parseFloat(style.paddingRight||'0');
    const innerHeight=element.clientHeight-parseFloat(style.paddingTop||'0')-parseFloat(style.paddingBottom||'0');
    const marginChange=(available:number,size:number)=>
      (Math.max(0,available-size*ratio)-Math.max(0,available-size))/2;
    const nextPan={
      x:before.pan.x+(anchor.x-bounds.left)*(1-ratio)-marginChange(innerWidth,bounds.width),
      y:before.pan.y+(anchor.y-bounds.top)*(1-ratio)-marginChange(innerHeight,bounds.height),
    };
    current.current={zoom:next,pan:nextPan};
    setZoom(next);setPan(nextPan);
  }

  useEffect(()=>{
    const element=viewport.current;
    if(!element||!enabled)return;
    function wheel(event:WheelEvent) {
      if(!event.ctrlKey&&!event.metaKey)return;
      event.preventDefault();
      const pixels=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?element!.clientHeight:1);
      changeZoom(current.current.zoom*Math.exp(-pixels*.002),{x:event.clientX,y:event.clientY});
    }
    element.addEventListener('wheel',wheel,{passive:false});
    return()=>element.removeEventListener('wheel',wheel);
  },[enabled]);

  function pointerDown(event:PointerEvent<HTMLDivElement>) {
    if(!enabled||event.button!==0||(event.target as Element).closest('button'))return;
    drag.current={id:event.pointerId,x:event.clientX,y:event.clientY,pan:current.current.pan};
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }
  function pointerMove(event:PointerEvent<HTMLDivElement>) {
    const start=drag.current;
    if(!start||start.id!==event.pointerId)return;
    const next={x:start.pan.x+event.clientX-start.x,y:start.pan.y+event.clientY-start.y};
    current.current={...current.current,pan:next};setPan(next);
  }
  function pointerUp(event:PointerEvent<HTMLDivElement>) {
    if(drag.current?.id!==event.pointerId)return;
    drag.current=null;setDragging(false);
    if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function reset(value=1) {
    if(viewport.current){
      viewport.current.scrollLeft=0;
      viewport.current.scrollTop=0;
    }
    const next={zoom:clamp(value),pan:{x:0,y:0}};
    current.current=next;setZoom(next.zoom);setPan(next.pan);
  }
  return {viewport,zoom,pan,dragging,changeZoom,reset,pointerDown,pointerMove,pointerUp};
}
