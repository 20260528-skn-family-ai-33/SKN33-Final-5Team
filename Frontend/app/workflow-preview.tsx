import {useEffect,useId,useState} from 'react';
import {Box,ZoomIn,ZoomOut,Maximize2} from 'lucide-react';
import {useCanvasNavigation} from './use-canvas-navigation';
import {WorkflowToolIcon} from './workflow-tool-icon';
export type Workflow={entry:string;nodes:{id:string;tool:string;label:string;bindings:{input:string;node_id:string;output:string}[];settings:Record<string,unknown>}[];edges:{source:string;target:string;branch:string}[]};
export function WorkflowPreview({workflow,selectedId,onSelect,autoFit=false}:{workflow?:Workflow|null;selectedId?:string|null;onSelect?:(node:Workflow['nodes'][number])=>void;autoFit?:boolean}){
 const [vertical,setVertical]=useState(true);
 const markerId=useId();
 const {viewport,zoom,pan,dragging,changeZoom,reset,pointerDown,pointerMove,pointerUp}=useCanvasNavigation(!!workflow);
 const nodeWidth=236,nodeHeight=116,margin=20;
 const columnGap=vertical?32:78,rowGap=vertical?58:28;
 const levels:Record<string,number>=workflow?{[workflow.entry]:0}:{};
 if(workflow)for(let pass=0;pass<workflow.nodes.length;pass++)for(const e of workflow.edges)if(levels[e.source]!==undefined)levels[e.target]=Math.min(workflow.nodes.length,Math.max(levels[e.target]||0,levels[e.source]+1));
 const counts:Record<number,number>={}, positions:Record<string,{x:number;y:number}>={};
 for(const n of workflow?.nodes||[]){
  const level=levels[n.id]||0, lane=counts[level]||0;
  positions[n.id]={x:margin+(vertical?lane:level)*(nodeWidth+columnGap),y:margin+(vertical?level:lane)*(nodeHeight+rowGap)};
  counts[level]=lane+1;
 }
 const positionsList=Object.values(positions);
 const width=Math.max(0,...positionsList.map(p=>p.x))+nodeWidth+margin, height=Math.max(0,...positionsList.map(p=>p.y))+nodeHeight+margin;
 function fit(){
  const el=viewport.current;
  if(el)reset(Math.min(1.5,Math.max(.35,Math.min((el.clientWidth-48)/width,(el.clientHeight-48)/height))));
 }
 useEffect(()=>{
  if(!autoFit)return;const element=viewport.current;if(!element)return;
  const resize=()=>{if(element.clientWidth>0&&element.clientHeight>0)fit();};resize();
  if(typeof ResizeObserver==='undefined')return;
  const observer=new ResizeObserver(resize);observer.observe(element);return()=>observer.disconnect();
 },[autoFit,width,height]);
 function changeDirection(){setVertical(v=>!v);reset();}
 const label=(tool:string)=>['read_document','read_table'].includes(tool)?'입력':tool==='condition'?'조건':tool==='ask_user'?'확인':['finish','export_result','write_result','create_spreadsheet'].includes(tool)?'출력':tool==='agent_node'?'분석·작성':'처리';
 return <><div className="as-canvas-toolbar"><span><Box size={16}/>워크플로우 <small>{workflow?workflow.nodes.length+'개 작업':'초안 대기'}</small></span>{workflow&&<button type="button" className="as-text-button" onClick={changeDirection}>{vertical?'가로로 보기':'세로로 보기'}</button>}</div><div tabIndex={0} role="region" aria-label="처리 흐름" className={'as-desktop-canvas'+(dragging?' is-dragging':'')} ref={viewport} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onLostPointerCapture={pointerUp} onKeyDown={event=>{
 if(!workflow||event.target!==event.currentTarget)return;
 if(event.key==='+'||event.key==='='){event.preventDefault();changeZoom(zoom+.1);}
 if(event.key==='-'){event.preventDefault();changeZoom(zoom-.1);}
 if(event.key==='0'){event.preventDefault();fit();}
}}>{workflow?<div style={{width:width*zoom,height:height*zoom,translate:`${pan.x}px ${pan.y}px`}} className="as-canvas-size"><div className={'as-flow-canvas '+(vertical?'flow-vertical':'flow-horizontal')} style={{width,height,transform:`scale(${zoom})`,transformOrigin:'top left'}}><svg width={width} height={height} aria-hidden="true"><defs><marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="var(--a4b-flow-line)"/></marker></defs>{workflow.edges.map((e,i)=>{const a=positions[e.source],b=positions[e.target];if(!a||!b)return null;const x1=a.x+(vertical?nodeWidth/2:nodeWidth), y1=a.y+(vertical?nodeHeight:nodeHeight/2);
 const x2=b.x+(vertical?nodeWidth/2:0), y2=b.y+(vertical?0:nodeHeight/2);
 const mx=(x1+x2)/2, my=(y1+y2)/2;
 const path=vertical?`M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`:`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
 return <g key={i}><path d={path} fill="none" stroke="var(--a4b-flow-line)" strokeWidth="1.7" markerEnd={'url(#'+markerId+')'}/>{e.branch!=='next'&&<text x={mx+4} y={my-6} fill="var(--a4b-muted)" fontSize="13">{e.branch==='yes'?'조건 충족':'조건 미충족'}</text>}</g>;})}</svg>{workflow.nodes.map((n,index)=><button key={n.id} aria-pressed={selectedId===n.id} className={'as-flow-node kind-'+n.tool+(selectedId===n.id?' selected':'')+(n.tool==='condition'?' branch':'')} style={{left:positions[n.id].x,top:positions[n.id].y,width:nodeWidth,height:nodeHeight,animationDelay:`${Math.min(index,8)*45}ms`}} title={n.label} onClick={()=>onSelect?.(n)}><span className="as-node-kind"><WorkflowToolIcon tool={n.tool} size={17}/>{label(n.tool)}</span><strong>{n.label}</strong><small>{n.bindings.length?'앞 단계 결과 사용':'실행 요청·연결 자료'}</small><i className="as-port-in"/><i className="as-port-out"/></button>)}</div></div>:<div className="as-canvas-empty"><div className="as-empty-flow"><span>자료 입력</span><span>작업 처리</span><span>결과 출력</span></div><h2>워크플로우 초안 대기 중</h2><p>업무를 설명하면 작업과 연결 순서가 여기에 표시됩니다.</p></div>}</div><div className="as-canvas-footer"><small>{workflow?'배경 드래그로 이동 · Ctrl/⌘+휠로 확대·축소':'설명한 업무에 필요한 작업을 구성합니다.'}</small><div className="as-zoom"><button onClick={()=>changeZoom(zoom-.1)} disabled={!workflow||zoom<=.35} aria-label="캔버스 축소"><ZoomOut size={16}/></button><span>{Math.round(zoom*100)}%</span><button onClick={()=>changeZoom(zoom+.1)} disabled={!workflow||zoom>=2} aria-label="캔버스 확대"><ZoomIn size={16}/></button><button onClick={fit} disabled={!workflow} aria-label="화면에 맞추기"><Maximize2 size={16}/></button></div></div></>;
}
