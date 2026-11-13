import {WorkspaceBrandMark} from './workspace-header';
import {AccountMenu} from './auth';
import {useEffect,useRef,useState,type CSSProperties,type KeyboardEvent,type MouseEvent,type RefObject} from 'react';
import {ArrowDown,ArrowUpRight,Check,CheckCircle2,Database,FileOutput,FileText,MessageSquare,Search,ShieldCheck} from 'lucide-react';
import {LandingStudioPreview} from './landing-studio-preview';
import {landingScenarios as scenarios,type ScenarioId} from './landing-scenarios';
import './landing-page.css';
const sections=[{id:'a4b-product-preview',label:'제품 미리보기'},{id:'a4b-how',label:'사용 방법'},{id:'a4b-usecases',label:'활용 사례'}] as const;
const steps=[
 {n:'01',Icon:MessageSquare,title:'할 일을 설명하세요.',text:'사용할 자료를 연결하고 필요한 작업을 적으세요. 조건이 부족하면 A4B가 물어봅니다.'},
 {n:'02',Icon:Check,title:'처리 순서를 확인하세요.',text:'생성된 작업과 조건을 확인하세요. 바꿀 내용은 대화로 요청할 수 있습니다.'},
 {n:'03',Icon:CheckCircle2,title:'저장하고 실행하세요.',text:'확인한 작업을 저장해 다시 실행하세요. 결과와 처리 기록은 실행 기록에 남습니다.'},
];
const order=(index:number)=>({'--a4b-reveal-order':index} as CSSProperties);
function ScenarioIllustration({id}:{id:ScenarioId}){
 if(id==='inventory')return <div className="a4b-scenario-art a4b-art-inventory" aria-hidden="true"><div className="a4b-art-sheet"><span className="a4b-art-sheet-top"><Database size={16}/>재고 목록<span>CSV</span></span><div className="a4b-art-stock-row"><span>키보드</span><span className="a4b-art-stock-bar"><i style={{width:'66%'}}/></span><b>20 / 30</b></div><div className="a4b-art-stock-row"><span>마우스</span><span className="a4b-art-stock-bar"><i style={{width:'30%'}}/></span><b>3 / 10</b></div><div className="a4b-art-stock-row a4b-art-row-muted"><span>모니터</span><span className="a4b-art-stock-bar"><i style={{width:'100%'}}/></span><b>10 / 10</b></div></div><span className="a4b-art-floating a4b-art-report"><FileOutput size={19}/><span>부족 상품 보고서<small>조건에 맞는 결과만</small></span><Check size={15}/></span></div>;
 if(id==='review')return <div className="a4b-scenario-art a4b-art-review" aria-hidden="true"><div className="a4b-art-review-paper"><span><ShieldCheck size={16}/>응대 초안 검수</span><p>환불을 완료했습니다.<br/><mark>오늘 바로 입금됩니다.</mark></p><div className="a4b-art-review-rule"/><small>정책과 확인되지 않은 표현을 대조</small></div><span className="a4b-art-floating a4b-art-review-status"><span className="a4b-art-status-dot"/>운영자 확인 대기<ArrowUpRight size={15}/></span></div>;
 return <div className="a4b-scenario-art a4b-art-document" aria-hidden="true"><div className="a4b-art-document-back"><FileText size={17}/><span/><span/><span/><span/></div><div className="a4b-art-document-front"><span><Search size={15}/>문서에서 근거 찾기</span><p>반품은 수령 후<br/><mark>7일 이내</mark> 접수합니다.</p><small>반품정책_샘플.txt</small></div><span className="a4b-art-floating a4b-art-source"><CheckCircle2 size={15}/>원문과 함께 확인</span></div>;
}

/** Intro only: scroll reveals, header state and section tracking. Workspace screens are untouched. */
function useScrollStory(root:RefObject<HTMLDivElement|null>,header:RefObject<HTMLElement|null>,setCurrent:(id:string)=>void){
 useEffect(()=>{
  const element=root.current;if(!element)return;
  const reduce=!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let frame=0;
  const measure=()=>{frame=0;const y=window.scrollY||0,max=Math.max(1,document.documentElement.scrollHeight-window.innerHeight);
   header.current?.classList.toggle('is-scrolled',y>8);header.current?.style.setProperty('--a4b-scroll-progress',String(Math.min(1,Math.max(0,y/max))));
   const line=window.innerHeight*.38;let active='';for(const {id} of sections){const top=document.getElementById(id)?.getBoundingClientRect().top;if(top!==undefined&&top<=line)active=id;}setCurrent(active);};
  const onScroll=()=>{if(!frame)frame=window.requestAnimationFrame(measure);};
  measure();window.addEventListener('scroll',onScroll,{passive:true});window.addEventListener('resize',onScroll);
  let observer:IntersectionObserver|undefined;
  if(!reduce&&typeof IntersectionObserver!=='undefined'){
   element.classList.add('a4b-reveal-ready');
   observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add('is-visible');observer?.unobserve(entry.target);}},{rootMargin:'0px 0px -10% 0px',threshold:.12});
   element.querySelectorAll('[data-reveal]:not(.is-visible)').forEach(node=>observer!.observe(node));
  }
  return()=>{window.removeEventListener('scroll',onScroll);window.removeEventListener('resize',onScroll);if(frame)window.cancelAnimationFrame(frame);observer?.disconnect();element.classList.remove('a4b-reveal-ready');};
 },[]);
}

export default function LandingPage({onStart}:{onStart:()=>void}){
 const [active,setActive]=useState<ScenarioId>('inventory'),[current,setCurrent]=useState('');
 const tabRefs=useRef<(HTMLButtonElement|null)[]>([]),root=useRef<HTMLDivElement>(null),header=useRef<HTMLElement>(null);
 useScrollStory(root,header,setCurrent);
 const scrollTo=(id:string)=>(event?:MouseEvent)=>{event?.preventDefault();const reduce=!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if(id==='a4b-landing-top')window.scrollTo({top:0,behavior:reduce?'auto':'smooth'});else document.getElementById(id)?.scrollIntoView?.({behavior:reduce?'auto':'smooth',block:'start'});};
 function handleTabKey(event:KeyboardEvent<HTMLButtonElement>,index:number){
  let next=index;if(event.key==='ArrowRight')next=(index+1)%scenarios.length;else if(event.key==='ArrowLeft')next=(index+scenarios.length-1)%scenarios.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=scenarios.length-1;else return;
  event.preventDefault();setActive(scenarios[next].id);tabRefs.current[next]?.focus();
 }
 return <div className="a4b-landing" id="a4b-landing-top" ref={root}>
  <a className="a4b-landing-skip" href="#a4b-landing-main" onClick={scrollTo('a4b-landing-main')}>본문으로 건너뛰기</a>
  <header className="a4b-landing-header" ref={header}><div className="a4b-landing-header-inner">
   <a className="a4b-landing-brand" href="#a4b-landing-top" onClick={scrollTo('a4b-landing-top')} aria-label="A4B 서비스 소개 맨 위로"><WorkspaceBrandMark/></a>
   <nav className="a4b-landing-nav" aria-label="페이지 메뉴">{sections.map(({id,label})=><a key={id} href={'#'+id} onClick={scrollTo(id)} aria-current={current===id?'location':undefined} className={current===id?'is-current':undefined}>{label}</a>)}</nav>
   <div className="a4b-landing-header-actions"><AccountMenu/></div>
  </div><span className="a4b-scroll-progress" aria-hidden="true"/></header>
  <main id="a4b-landing-main">
   <section className="a4b-landing-hero" aria-labelledby="a4b-hero-title">
    <div className="a4b-hero-eyebrow a4b-hero-enter"><span/>대화로 만드는 업무 자동화</div>
    <h1 id="a4b-hero-title" className="a4b-hero-enter" style={order(1)}><span>반복 업무를</span><span>자동으로 처리하세요.</span></h1>
    <p className="a4b-hero-enter" style={order(2)}>자료를 연결하고 필요한 작업을 설명하세요.<br className="a4b-desktop-break"/> 처리 순서를 확인해 저장하면, 같은 업무에 다시 쓸 수 있습니다.</p>
    <div className="a4b-hero-capabilities a4b-hero-enter" style={order(3)} aria-label="주요 기능"><span>자료 정리</span><i aria-hidden="true"/><span>문서 분석</span><i aria-hidden="true"/><span>예약 실행</span></div>
    <div className="a4b-hero-enter" style={order(4)}><a className="a4b-landing-text-link a4b-scroll-cue" href="#a4b-product-preview" onClick={scrollTo('a4b-product-preview')}>화면 살펴보기<ArrowDown size={16} aria-hidden="true"/></a></div>
   </section>
   <section className="a4b-product-section" id="a4b-product-preview" aria-labelledby="a4b-product-title"><div className="a4b-landing-container">
    <div className="a4b-section-heading" data-reveal><div><h2 id="a4b-product-title">설명한 업무가<br/>이렇게 만들어집니다.</h2></div><p>작업을 눌러 설정을 확인하고,<br className="a4b-desktop-break"/> 직접 실행 탭에서 결과 예시를 살펴보세요.</p></div>
    <div className="a4b-live-preview" data-reveal style={order(1)}>
     <div className="a4b-preview-tabs" role="tablist" aria-label="업무 예시">{scenarios.map((item,index)=><button key={item.id} type="button" role="tab" id={'a4b-preview-tab-'+item.id} aria-selected={active===item.id} aria-controls={'a4b-preview-panel-'+item.id} tabIndex={active===item.id?0:-1} ref={element=>{tabRefs.current[index]=element;}} onClick={()=>setActive(item.id)} onKeyDown={event=>handleTabKey(event,index)}>{item.label}</button>)}</div>
     {scenarios.map(item=><div key={item.id} role="tabpanel" hidden={active!==item.id} id={'a4b-preview-panel-'+item.id} aria-labelledby={'a4b-preview-tab-'+item.id} tabIndex={0}>{active===item.id&&<LandingStudioPreview scenario={item}/>}</div>)}
    </div>
    <p className="a4b-preview-disclaimer">실제 편집기와 같은 화면에 예시 데이터를 표시했습니다. 여기서는 작업을 저장하거나 실행하지 않습니다.</p>
   </div></section>
   <section className="a4b-how-section" id="a4b-how" aria-labelledby="a4b-how-title"><div className="a4b-landing-container">
    <div className="a4b-section-heading" data-reveal><div><h2 id="a4b-how-title">설명하고, 확인하고,<br/>실행하세요.</h2></div></div>
    <div className="a4b-steps">{steps.map(({n,Icon,title,text},index)=><article key={n} data-reveal style={order(index)}><div className="a4b-step-top"><span>{n}</span><Icon size={23} aria-hidden="true"/></div><h3>{title}</h3><p>{text}</p></article>)}</div>
   </div></section>
   <section className="a4b-usecases-section" id="a4b-usecases" aria-labelledby="a4b-usecases-title"><div className="a4b-landing-container">
    <div className="a4b-usecases-heading" data-reveal><h2 id="a4b-usecases-title">이런 업무에 써보세요.</h2><p>자료를 읽고 정리하는 반복 업무에 활용할 수 있습니다.</p></div>
    <div className="a4b-scenario-cards">{scenarios.map((item,index)=><article key={item.id} className="a4b-scenario-card" data-reveal style={order(index)}><ScenarioIllustration id={item.id}/><div className="a4b-scenario-card-body"><span className="a4b-scenario-category">{item.label}</span><h3>{item.title}</h3><p>{item.description}</p><button type="button" className="a4b-scenario-action" onClick={()=>{setActive(item.id);scrollTo('a4b-product-preview')();}} aria-label={item.label+' 예시 보기'}>예시 보기<ArrowUpRight size={17} aria-hidden="true"/></button></div></article>)}</div>
   </div></section>
   <section className="a4b-final-section" aria-labelledby="a4b-final-title"><div className="a4b-final-decoration" aria-hidden="true"><span/><span/><span/></div><div className="a4b-final-content" data-reveal><h2 id="a4b-final-title">반복할 업무부터<br/>하나씩 시작하세요.</h2><p>할 일을 설명하면 처리 순서를 함께 정리합니다.</p><button type="button" className="a4b-landing-button a4b-button-primary" onClick={onStart}>A4B 시작하기<ArrowUpRight size={19} aria-hidden="true"/></button></div></section>
  </main>
  <footer className="a4b-landing-footer"><div className="a4b-landing-container"><div className="a4b-footer-brand"><WorkspaceBrandMark/></div><p>업무를 설명하고, 확인한 순서로 실행하세요.</p><a href="#a4b-landing-top" onClick={scrollTo('a4b-landing-top')}>맨 위로<ArrowUpRight size={15} aria-hidden="true"/></a></div></footer>
 </div>;
}
