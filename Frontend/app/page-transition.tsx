import {useEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import './page-transition.css';
const owners=new Set<symbol>();
let originalInert=false;
export function PageTransition({active}:{active:boolean}){
 const settled=useRef(false);
 if(!active)settled.current=true;
 const visible=active&&!settled.current;
 useEffect(()=>{
  if(!visible)return;
  const root=document.getElementById('root'),owner=Symbol();
  if(!owners.size)originalInert=root?.hasAttribute('inert')??false;
  owners.add(owner);root?.setAttribute('inert','');
  return()=>{owners.delete(owner);if(!owners.size&&!originalInert)root?.removeAttribute('inert');};
 },[visible]);
 if(!visible)return null;
 return createPortal(<div className="page-transition" role="status" aria-label="화면을 준비하고 있습니다."><img src="/brand/a4b-flow-mark.svg" alt="" width="56" height="28"/><span className="page-transition-label">화면을 준비하고 있습니다.</span></div>,document.body);
}
