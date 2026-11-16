import {useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import './page-transition.css';

// Loaders may overlap; restore only locks that this component owns.
const locks=new Map<Element,{owners:number;inert:boolean}>();
function lock(element:Element){
 const current=locks.get(element);
 if(current)current.owners++;
 else{locks.set(element,{owners:1,inert:element.hasAttribute('inert')});element.setAttribute('inert','');}
 return()=>{const state=locks.get(element);if(state&&!--state.owners){if(!state.inert)element.removeAttribute('inert');locks.delete(element);}};
}
const scopes=new Map<HTMLElement,{owners:number;top:string;resize:()=>void}>();
function prepareScope(main:HTMLElement){
 let state=scopes.get(main);
 if(state)state.owners++;
 else{
  const resize=()=>main.style.setProperty('--page-loading-top',Math.max(0,main.getBoundingClientRect().top+window.scrollY)+'px');
  state={owners:1,top:main.style.getPropertyValue('--page-loading-top'),resize};scopes.set(main,state);
  resize();window.addEventListener('resize',resize);
 }
 return()=>{const current=scopes.get(main);if(current&&!--current.owners){window.removeEventListener('resize',current.resize);if(current.top)main.style.setProperty('--page-loading-top',current.top);else main.style.removeProperty('--page-loading-top');scopes.delete(main);}};
}
export function PageTransition({active}:{active:boolean}){
 const settled=useRef(false),anchor=useRef<HTMLSpanElement>(null);
 const [target,setTarget]=useState<HTMLElement|null>(null);
 if(!active)settled.current=true;
 const visible=active&&!settled.current;
 useLayoutEffect(()=>{
  if(!visible)return;
  const main=anchor.current?.closest('main'),scope=main||document.getElementById('root');
  setTarget(main||document.body);
  if(!scope)return;
  const releaseScope=main?prepareScope(main):()=>{};
  const releases=new Map<Element,()=>void>();
  const update=()=>{
   const elements=main?Array.from(main.children).filter(child=>!child.classList.contains('page-transition')):[scope];
   for(const element of elements)if(!releases.has(element))releases.set(element,lock(element));
  };
  update();
  const observer=new window.MutationObserver(update);observer.observe(scope,{childList:true});
  return()=>{observer.disconnect();releaseScope();for(const release of releases.values())release();};
 },[visible]);
 if(!visible)return null;
 return <><span ref={anchor} hidden/>{target&&createPortal(<div className={'page-transition'+(target===document.body?' page-transition-fullscreen':'')} role="status" aria-label="화면을 준비하고 있습니다."><img src="/brand/a4b-flow-mark.svg" alt="" width="56" height="28"/><span className="page-transition-label">화면을 준비하고 있습니다.</span></div>,target)}</>;
}
