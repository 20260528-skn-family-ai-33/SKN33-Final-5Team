import {useEffect,useRef,useState} from 'react';
import {Box,FileText,Upload,Download,FileSearch,ShieldCheck,Plus} from 'lucide-react';
const introduction={heading:'무엇을 만들까요?',message:<>자료를 요약하고, 결과를<br/>확인한 뒤 전달하는 흐름을<br/>만들고 싶어요.</>,note:'6개 작업 흐름을 제안해요.',detail:<>추가한 승인 단계를 확인한 뒤<br/>제안을 적용하세요.</>};
const nodes=[
 {id:'request',title:'요청 입력',detail:'사용자 요청',port:'요청 → AI 요청',Icon:Box},
 {id:'upload',title:'파일 입력',detail:'참고자료.md',port:'파일 → 문서 읽기',Icon:Upload},
 {id:'document',title:'문서 읽기',detail:'텍스트 추출',port:'문서 → AI 자료',Icon:FileText},
 {id:'ai',title:'AI 작업',detail:'자료 요약하기',port:'요청·자료 → 결과',Icon:FileSearch},
 {id:'approval',title:'사람 승인',detail:'결과를 검토하고 승인',port:'내용 → 승인된 내용',Icon:ShieldCheck},
 {id:'output',title:'결과 출력',detail:'최종 결과 전달',port:'승인된 내용 → 결과',Icon:Download},
];
const edges=[['request','ai'],['upload','document'],['document','ai'],['ai','approval'],['approval','output']];
type Wire={id:string;d:string};
export function IntroPreview({onEditor,onLibrary}:{onEditor:()=>void;onLibrary:()=>void}){
 const [wires,setWires]=useState<Wire[]>([]),[size,setSize]=useState({width:1,height:1});
 const canvas=useRef<HTMLDivElement>(null),refs=useRef<Record<string,HTMLElement|null>>({});
 useEffect(()=>{
  const update=()=>{const element=canvas.current;if(!element)return;const origin=element.getBoundingClientRect();setSize({width:element.clientWidth,height:element.clientHeight});
   setWires(edges.flatMap(([from,to])=>{const a=refs.current[from],b=refs.current[to];if(!a||!b)return [];const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
    const ax=ar.right-origin.left,ay=ar.top-origin.top+ar.height/2,bx=br.left-origin.left,by=br.top-origin.top+br.height/2;
    if(bx<ax&&Math.abs(by-ay)>40){const down=by>ay,x1=ar.left-origin.left+ar.width/2,x2=br.left-origin.left+br.width/2,y1=(down?ar.bottom:ar.top)-origin.top,y2=(down?br.top:br.bottom)-origin.top,mid=(y1+y2)/2;return [{id:from+'-'+to,d:`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`}];}
    const mid=(ax+bx)/2;return [{id:from+'-'+to,d:`M${ax},${ay} H${mid} V${by} H${bx}`}];}));};
  update();if(typeof ResizeObserver==='undefined'){window.addEventListener('resize',update);return()=>window.removeEventListener('resize',update);}
  const observer=new ResizeObserver(update);if(canvas.current)observer.observe(canvas.current);Object.values(refs.current).forEach(el=>{if(el)observer.observe(el);});return()=>observer.disconnect();
 },[]);
 const current=introduction;
 return <section className="entry-product-preview" id="product-preview" aria-label="예상 화면 샘플"><aside><small>예상 화면 · 샘플</small><h2>{current.heading}</h2><p>{current.message}</p><div className="entry-sample-note"><strong>{current.note}</strong><p>{current.detail}</p></div></aside><div className="entry-preview-canvas" ref={canvas}><button className="as-button entry-preview-add" onClick={onLibrary}><Plus size={14}/>작업 추가</button><span className="entry-preview-caption">AI 제안 미리보기 · 적용 전</span><svg className="entry-preview-wires" viewBox={`0 0 ${size.width} ${size.height}`} aria-hidden="true">{wires.map(w=><path key={w.id} d={w.d} className="active"/>)}</svg><div className="entry-preview-grid">{nodes.map(({id,title,detail,port,Icon})=>{const Tag=id==='ai'?'button':'div';return <Tag ref={(el:HTMLElement|null)=>{refs.current[id]=el;}} onClick={id==='ai'?onEditor:undefined} aria-label={id==='ai'?'AI 작업 설정 미리보기':undefined} className={'entry-preview-node '+id} key={id}><Icon size={19}/><strong>{title}</strong><small>{detail}</small><span>{port}</span><i className="entry-preview-port in"/><i className="entry-preview-port out"/></Tag>;})}</div></div></section>;
}
