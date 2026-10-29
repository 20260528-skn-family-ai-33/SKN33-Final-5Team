import {useEffect,useRef,useState,type CSSProperties,type KeyboardEvent,type MouseEvent,type RefObject} from 'react';
import {IntroPreview} from './intro-preview';
import {ArrowDown,ArrowRight,ArrowUp,ArrowUpRight,CalendarClock,Check,CheckCircle2,Database,FileOutput,FileText,Filter,MessageSquare,Paperclip,Search,ShieldCheck,Sparkles} from 'lucide-react';
import './landing-page.css';

type ScenarioId='inventory'|'review'|'document';
type Scenario={
 id:ScenarioId;
 label:string;
 title:string;
 description:string;
 file:string;
 request:string;
 reply:string;
 confirmation:string;
 nodes:{label:string;detail:string;kind:'input'|'process'|'output'}[];
};

const scenarios:Scenario[]=[
 {id:'inventory',label:'재고 보고서',title:'매일의 재고 확인을, 하나의 흐름으로.',description:'재고 파일을 읽고 부족한 상품을 골라 보고서로 저장하세요. 확인한 조건으로 매일·매주 실행을 예약할 수 있습니다.',file:'inventory.csv',request:'매일 오전 9시에 재고가 기준보다 적은 상품을 찾아 보고서로 저장해줘.',reply:'재고와 기준 수량을 비교하는 흐름을 만들게요. 빈 값은 제외하고, 결과는 CSV로 저장할까요?',confirmation:'네, 빈 값은 제외하고 CSV로 저장해주세요.',nodes:[{label:'재고 파일 읽기',detail:'상품 · 현재 재고 · 기준 수량',kind:'input'},{label:'부족 상품 찾기',detail:'현재 재고 < 기준 수량',kind:'process'},{label:'CSV 보고서 만들기',detail:'조건에 맞는 상품만 저장',kind:'output'}]},
 {id:'review',label:'고객 응대 검수',title:'보내기 전에, 한 번 더 확인하세요.',description:'고객 문의와 응대 초안을 정책 원문에 대조하세요. 검수 의견과 수정안을 보고, 운영자가 최종 문장을 확인하고 승인합니다.',file:'환불 정책.txt',request:'환불을 안내하는 응대 초안이 정책과 맞는지 검수하고, 수정안을 보여줘.',reply:'정책과 고객 문의를 함께 확인할게요. 확인되지 않은 처리 완료 표현을 찾고, 최종 문장은 운영자가 승인하도록 구성하겠습니다.',confirmation:'좋아요. 정책의 근거도 함께 보여주세요.',nodes:[{label:'정책 원문 읽기',detail:'환불 조건과 처리 기간 확인',kind:'input'},{label:'응대 초안 검수',detail:'문의 · 초안 · 정책 대조',kind:'process'},{label:'검수 보고서 만들기',detail:'수정 의견 · 운영자 확인',kind:'output'}]},
 {id:'document',label:'문서 기반 답변',title:'문서에 있는 답을, 근거와 함께.',description:'연결한 문서에서 필요한 내용을 찾아 답변 초안을 만드세요. 결과와 함께 사용한 원문을 확인할 수 있습니다.',file:'반품정책_샘플.txt',request:'반품은 언제까지 접수할 수 있는지, 연결한 문서를 바탕으로 안내해줘.',reply:'문서에서 반품 접수 기간을 찾아 안내할게요. 답변에 사용한 원문도 함께 확인할 수 있도록 구성합니다.',confirmation:'문서에 없는 내용은 추정하지 마세요.',nodes:[{label:'안내 문서 읽기',detail:'연결한 문서 본문 사용',kind:'input'},{label:'필요한 근거 찾기',detail:'반품 접수 기간 검색',kind:'process'},{label:'응대 초안 만들기',detail:'확인한 문서에 따라 답변',kind:'output'}]},
];


function ScenarioResult({scenario}:{scenario:Scenario}){
 if(scenario.id==='inventory')return <div className="a4b-result-content"><span className="a4b-result-kicker">재고 확인 결과</span><h4>부족 상품 <strong>2개</strong></h4><p>기준 수량에 못 미치는 상품을 모았어요.</p><table className="a4b-demo-table"><caption className="a4b-sr-only">재고 보고서 예시: 현재 재고가 기준 수량보다 적은 상품</caption><thead><tr><th scope="col">상품</th><th scope="col">재고</th><th scope="col">기준</th></tr></thead><tbody><tr><th scope="row">키보드</th><td>20</td><td>30</td></tr><tr><th scope="row">마우스</th><td>3</td><td>10</td></tr></tbody></table><div className="a4b-result-file"><span className="a4b-file-symbol"><FileOutput size={20}/></span><span><b>재고_보고서.csv</b><small>부족 상품 2개 · 결과 예시</small></span><CheckCircle2 size={17}/></div><div className="a4b-result-note"><CalendarClock size={15}/><span>매일 오전 9시 · 예약 설정 예시</span></div></div>;
 if(scenario.id==='review')return <div className="a4b-result-content"><span className="a4b-result-kicker">고객 응대 검수 결과</span><h4>확인이 필요한 <strong>표현 1곳</strong></h4><div className="a4b-review-quote"><small>응대 초안</small><p>“환불을 완료했습니다.<br/>오늘 바로 입금됩니다.”</p><span>처리 완료 여부 · 정책 확인 필요</span></div><div className="a4b-review-suggestion"><span><Sparkles size={13}/>수정안 예시</span><p>환불 신청 후 처리 기간은 영업일 3일입니다. 실제 처리 여부는 담당자의 확인이 필요합니다.</p></div><div className="a4b-result-note"><ShieldCheck size={16}/><span>최종 응대는 운영자가 확인·승인</span></div></div>;
 return <div className="a4b-result-content"><span className="a4b-result-kicker">문서 기반 답변</span><h4>필요한 답을,<br/><strong>문서에서 찾았어요.</strong></h4><div className="a4b-document-answer"><span className="a4b-answer-icon"><MessageSquare size={16}/></span><p>반품은 수령 후 <strong>7일 이내</strong>에 접수할 수 있습니다.</p></div><div className="a4b-evidence"><span><FileText size={14}/>답변에 사용한 원문</span><p>“반품은 수령 후 7일 이내 접수합니다.”</p><small>반품정책_샘플.txt</small></div><div className="a4b-result-note"><CheckCircle2 size={15}/><span>사용한 근거를 결과와 함께 확인</span></div></div>;
}

function ProductPreview({scenario,selected}:{scenario:Scenario;selected:boolean}){
 return <div className="a4b-demo-board" role="tabpanel" id={'a4b-preview-panel-'+scenario.id} aria-labelledby={'a4b-preview-tab-'+scenario.id} hidden={!selected} tabIndex={0}>
  <section className="a4b-demo-conversation" aria-label="업무 설계 대화 예시"><div className="a4b-demo-column-title"><span><MessageSquare size={14}/>업무 설명</span><small>01</small></div><div className="a4b-demo-chat"><div className="a4b-demo-resource"><Paperclip size={13}/>{scenario.file}</div><p className="a4b-demo-user">{scenario.request}</p><div className="a4b-demo-assistant"><span className="a4b-assistant-avatar"><span className="entry-mark">A</span></span><div><b>A4B</b><p>{scenario.reply}</p></div></div><p className="a4b-demo-user a4b-demo-confirmation">{scenario.confirmation}</p></div><div className="a4b-demo-composer" aria-hidden="true"><span>업무의 조건을 이야기하세요</span><span className="a4b-demo-send"><ArrowUp size={15}/></span></div></section>
  <section className="a4b-demo-flow" aria-label="구성된 워크플로우 예시"><div className="a4b-demo-column-title"><span><Sparkles size={14}/>워크플로우 설계</span><small>02</small></div><div className="a4b-demo-flow-body"><span className="a4b-flow-start"><span/>업무 시작</span><span className="a4b-flow-link" aria-hidden="true"/>{scenario.nodes.map((node,index)=><div className="a4b-flow-step" key={node.label}><div className={'a4b-preview-node a4b-preview-node-'+node.kind}><span className="a4b-preview-node-icon">{node.kind==='input'?<FileText size={17}/>:node.kind==='process'?<Filter size={17}/>:<FileOutput size={17}/>}</span><div><span>{node.kind==='input'?'입력':node.kind==='process'?'처리':'출력'}</span><b>{node.label}</b><small>{node.detail}</small></div></div>{index<scenario.nodes.length-1&&<span className="a4b-flow-link" aria-hidden="true"/>}</div>)}<span className="a4b-flow-link" aria-hidden="true"/><span className="a4b-flow-end"><Check size={12}/>확인한 흐름을 저장</span></div><div className="a4b-demo-flow-caption">조건을 확인하고, 실행할 순서로 연결합니다.</div></section>
  <section className="a4b-demo-result" aria-label="업무 실행 결과 예시"><div className="a4b-demo-column-title"><span><FileOutput size={14}/>결과 확인</span><small>03</small></div><ScenarioResult scenario={scenario}/></section>
 </div>;
}

function ScenarioIllustration({id}:{id:ScenarioId}){
 if(id==='inventory')return <div className="a4b-scenario-art a4b-art-inventory" aria-hidden="true"><div className="a4b-art-sheet"><span className="a4b-art-sheet-top"><Database size={16}/>재고 목록<span>CSV</span></span><div className="a4b-art-stock-row"><span>키보드</span><span className="a4b-art-stock-bar"><i style={{width:'66%'}}/></span><b>20 / 30</b></div><div className="a4b-art-stock-row"><span>마우스</span><span className="a4b-art-stock-bar"><i style={{width:'30%'}}/></span><b>3 / 10</b></div><div className="a4b-art-stock-row a4b-art-row-muted"><span>모니터</span><span className="a4b-art-stock-bar"><i style={{width:'100%'}}/></span><b>10 / 10</b></div></div><span className="a4b-art-floating a4b-art-report"><FileOutput size={19}/><span>부족 상품 보고서<small>조건에 맞는 결과만</small></span><Check size={15}/></span></div>;
 if(id==='review')return <div className="a4b-scenario-art a4b-art-review" aria-hidden="true"><div className="a4b-art-review-paper"><span><ShieldCheck size={16}/>응대 초안 검수</span><p>환불을 완료했습니다.<br/><mark>오늘 바로 입금됩니다.</mark></p><div className="a4b-art-review-rule"/><small>정책과 확인되지 않은 표현을 대조</small></div><span className="a4b-art-floating a4b-art-review-status"><span className="a4b-art-status-dot"/>운영자 확인 대기<ArrowUpRight size={15}/></span></div>;
 return <div className="a4b-scenario-art a4b-art-document" aria-hidden="true"><div className="a4b-art-document-back"><FileText size={17}/><span/><span/><span/><span/></div><div className="a4b-art-document-front"><span><Search size={15}/>문서에서 근거 찾기</span><p>반품은 수령 후<br/><mark>7일 이내</mark> 접수합니다.</p><small>반품정책_샘플.txt</small></div><span className="a4b-art-floating a4b-art-source"><CheckCircle2 size={15}/>원문과 함께 확인</span></div>;
}

type IntroTarget='workflows'|'nodes'|'history';
type LandingProps={
 /** Existing entry route: ?screen=workflows */
 onStart:()=>void;
 /** Existing sample route: ?screen=preview (node=true adds previewNode=ai) */
 onPreview:(node:boolean)=>void;
 /** Existing workspace routes reached from the intro */
 onRoute:(route:IntroTarget)=>void;
};
const sections=[{id:'a4b-product-preview',label:'제품 미리보기'},{id:'a4b-how',label:'작동 방식'},{id:'a4b-usecases',label:'활용 사례'}] as const;
const steps=[
 {n:'01',Icon:Sparkles,title:'역할을 준비하세요.',text:'에이전트의 역할이나 파일 처리 기능을 노드로 저장하세요. 기본 노드를 선택해 바로 설계를 시작할 수도 있습니다.',action:'노드 라이브러리',route:'nodes'},
 {n:'02',Icon:MessageSquare,title:'업무를 이야기하세요.',text:'자료와 원하는 결과를 설명하세요. 부족한 조건을 대화로 확인하고, 연결된 노드와 설정을 살펴본 뒤 저장합니다.',action:'워크플로우 목록',route:'workflows'},
 {n:'03',Icon:CheckCircle2,title:'결과를 확인하세요.',text:'저장한 흐름에 요청과 자료를 넣어 실행하세요. 결과, 사용한 근거, 처리 단계를 함께 확인할 수 있습니다.',action:'실행 기록',route:'history'},
] as const;
const order=(index:number)=>({'--a4b-reveal-order':index} as CSSProperties);

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

export default function LandingPage({onStart,onPreview,onRoute}:LandingProps){
 const [active,setActive]=useState<ScenarioId>('inventory'),[current,setCurrent]=useState('');
 const tabRefs=useRef<(HTMLButtonElement|null)[]>([]);
 const root=useRef<HTMLDivElement>(null),header=useRef<HTMLElement>(null);
 useScrollStory(root,header,setCurrent);
 const scrollTo=(id:string)=>(event?:MouseEvent)=>{event?.preventDefault();const reduce=!!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if(id==='a4b-landing-top')window.scrollTo({top:0,behavior:reduce?'auto':'smooth'});else document.getElementById(id)?.scrollIntoView?.({behavior:reduce?'auto':'smooth',block:'start'});};
 function handleTabKey(event:KeyboardEvent<HTMLButtonElement>,index:number){
  let next=index;
  if(event.key==='ArrowRight')next=(index+1)%scenarios.length;
  else if(event.key==='ArrowLeft')next=(index+scenarios.length-1)%scenarios.length;
  else if(event.key==='Home')next=0;
  else if(event.key==='End')next=scenarios.length-1;
  else return;
  event.preventDefault();setActive(scenarios[next].id);tabRefs.current[next]?.focus();
 }
 return <div className="a4b-landing" id="a4b-landing-top" ref={root}>
  <a className="a4b-landing-skip" href="#a4b-landing-main" onClick={scrollTo('a4b-landing-main')}>본문으로 건너뛰기</a>
  <header className="a4b-landing-header" ref={header}><div className="a4b-landing-header-inner">
   <a className="a4b-landing-brand" href="#a4b-landing-top" onClick={scrollTo('a4b-landing-top')} aria-label="A4B 서비스 소개 맨 위로"><span className="entry-mark">A</span><span>A4B</span></a>
   <nav className="a4b-landing-nav" aria-label="페이지 메뉴">{sections.map(({id,label})=><a key={id} href={'#'+id} onClick={scrollTo(id)} aria-current={current===id?'location':undefined} className={current===id?'is-current':undefined}>{label}</a>)}</nav>
   <div className="a4b-landing-header-actions"><button type="button" className="a4b-landing-button a4b-button-header a4b-button-quiet" onClick={()=>onPreview(false)}>제품 살펴보기</button><button type="button" className="a4b-landing-button a4b-button-header" onClick={onStart}>워크플로우 열기<ArrowUpRight size={16} aria-hidden="true"/></button></div>
  </div><span className="a4b-scroll-progress" aria-hidden="true"/></header>
  <main id="a4b-landing-main">
   <section className="a4b-landing-hero" aria-labelledby="a4b-hero-title">
    <div className="a4b-hero-eyebrow a4b-hero-enter"><span/>대화로 설계하는 워크플로우</div>
    <h1 id="a4b-hero-title" className="a4b-hero-enter" style={order(1)}><span>업무를 말하세요.</span><span>흐름은 A4B가 만듭니다.</span></h1>
    <p className="a4b-hero-enter" style={order(2)}>대화로 시작하고, 노드로 다듬고, 확인한 뒤 실행하세요.<br className="a4b-desktop-break"/> 자료와 처리 단계를 연결해 내 작업에 맞는 흐름을 만듭니다.</p>
    <div className="a4b-hero-actions a4b-hero-enter" style={order(3)}><button type="button" className="a4b-landing-button a4b-button-primary" onClick={onStart}>A4B 시작하기<ArrowUpRight size={19} aria-hidden="true"/></button><button type="button" className="a4b-landing-button a4b-button-secondary" onClick={()=>onPreview(false)}>편집기 미리보기</button></div>
    <div className="a4b-hero-capabilities a4b-hero-enter" style={order(4)} aria-label="주요 기능"><span>재사용할 노드</span><i aria-hidden="true"/><span>대화로 만든 설계</span><i aria-hidden="true"/><span>버전으로 실행</span></div>
    <div className="a4b-hero-visual a4b-hero-enter" style={order(5)}><IntroPreview onEditor={()=>onPreview(true)} onLibrary={()=>onRoute('nodes')}/><a className="a4b-landing-text-link a4b-scroll-cue" href="#a4b-product-preview" onClick={scrollTo('a4b-product-preview')}>스크롤해서 살펴보기<ArrowDown size={16} aria-hidden="true"/></a></div>
   </section>
   <section className="a4b-product-section" id="a4b-product-preview" aria-labelledby="a4b-product-title"><div className="a4b-landing-container">
    <div className="a4b-section-heading" data-reveal><div><span className="a4b-landing-eyebrow">FROM WORDS TO WORKFLOWS</span><h2 id="a4b-product-title">설명하던 업무가,<br/>눈에 보이는 흐름으로.</h2></div><p>자료를 읽고, 조건을 확인하고, 결과를 만드는 과정.<br className="a4b-desktop-break"/> 하나의 업무가 어떻게 연결되는지 살펴보세요.</p></div>
    <div className="a4b-product-preview" data-reveal style={order(1)}>
     <div className="a4b-preview-topbar"><div className="a4b-preview-brand"><span className="entry-mark">A</span><span>A4B <small>Studio</small></span></div><span className="a4b-preview-example"><span/>제품 미리보기 · 예시</span></div>
     <div className="a4b-preview-tabs" role="tablist" aria-label="업무 시나리오">{scenarios.map((scenario,index)=><button key={scenario.id} type="button" role="tab" id={'a4b-preview-tab-'+scenario.id} aria-selected={active===scenario.id} aria-controls={'a4b-preview-panel-'+scenario.id} tabIndex={active===scenario.id?0:-1} ref={element=>{tabRefs.current[index]=element;}} onClick={()=>setActive(scenario.id)} onKeyDown={event=>handleTabKey(event,index)}>{scenario.id==='inventory'?<CalendarClock size={15}/>:scenario.id==='review'?<ShieldCheck size={15}/>:<FileText size={15}/>}<span>{scenario.label}</span></button>)}</div>
     {scenarios.map(scenario=><ProductPreview key={scenario.id} scenario={scenario} selected={active===scenario.id}/>)}
     <div className="a4b-preview-bottom"><span><span/>설명 → 설계 → 실행</span><span>업무의 시작부터 결과까지, 한곳에서.</span></div>
    </div>
    <p className="a4b-preview-disclaimer">프로젝트의 시연 자료로 구성한 예시입니다. 실제 설계와 실행은 작업 공간에서 진행합니다.</p>
   </div></section>
   <section className="a4b-how-section" id="a4b-how" aria-labelledby="a4b-how-title"><div className="a4b-landing-container">
    <div className="a4b-section-heading" data-reveal><div><span className="a4b-landing-eyebrow">A CLEAR WAY TO WORK</span><h2 id="a4b-how-title">복잡한 업무도,<br/>한 단계씩 명확하게.</h2></div><p>도구 이름부터 외울 필요는 없어요.<br/>어떤 일을 맡길지, 어떤 결과가 필요한지부터 시작하세요.</p></div>
    <div className="a4b-steps">{steps.map(({n,Icon,title,text,action,route},index)=><article key={n} data-reveal style={order(index)}><div className="a4b-step-top"><span>{n}</span><Icon size={23} aria-hidden="true"/></div><h3>{title}</h3><p>{text}</p><button type="button" className="a4b-step-caption" onClick={()=>onRoute(route)}>{action}<ArrowRight size={15} aria-hidden="true"/></button></article>)}</div>
   </div></section>
   <section className="a4b-usecases-section" id="a4b-usecases" aria-labelledby="a4b-usecases-title"><div className="a4b-landing-container">
    <div className="a4b-usecases-heading" data-reveal><span className="a4b-landing-eyebrow">MADE FOR YOUR EVERYDAY</span><h2 id="a4b-usecases-title">매일의 일에,<br className="a4b-mobile-break"/> 새로운 흐름을.</h2><p>자주 반복하는 업무부터 시작해보세요.</p></div>
    <div className="a4b-scenario-cards">{scenarios.map((scenario,index)=><article key={scenario.id} className="a4b-scenario-card" data-reveal style={order(index)}><ScenarioIllustration id={scenario.id}/><div className="a4b-scenario-card-body"><span className="a4b-scenario-category">{scenario.label}</span><h3>{scenario.title}</h3><p>{scenario.description}</p><button type="button" className="a4b-scenario-action" onClick={onStart} aria-label={scenario.label+' 흐름을 내 워크플로우에서 시작하기'}>내 워크플로우에서 시작<ArrowUpRight size={17} aria-hidden="true"/></button></div></article>)}</div>
   </div></section>
   <section className="a4b-final-section" aria-labelledby="a4b-final-title"><div className="a4b-final-decoration" aria-hidden="true"><span/><span/><span/></div><div className="a4b-final-content" data-reveal><span className="a4b-landing-eyebrow">YOUR WORK. YOUR FLOW.</span><h2 id="a4b-final-title">다음 업무는,<br/>A4B와 함께.</h2><p>머릿속에 있던 업무를 실행할 흐름으로 만들어보세요.</p><button type="button" className="a4b-landing-button a4b-button-primary" onClick={onStart}>워크플로우 시작하기<ArrowUpRight size={19} aria-hidden="true"/></button></div></section>
  </main>
  <footer className="a4b-landing-footer"><div className="a4b-landing-container"><div className="a4b-footer-brand"><span className="entry-mark">A</span><span>A4B<small>AI for Business</small></span></div><p>대화로 설계하고, 확인한 흐름으로 실행합니다.</p><a href="#a4b-landing-top" onClick={scrollTo('a4b-landing-top')}>맨 위로<ArrowUpRight size={15} aria-hidden="true"/></a></div></footer>
 </div>;
}
