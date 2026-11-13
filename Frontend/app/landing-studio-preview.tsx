import {useState} from 'react';
import {ArrowUp,FileText,MessageSquare,Paperclip,Settings2} from 'lucide-react';
import {WorkflowPreview} from './workflow-preview';
import {NodeInspector} from './node-inspector';
import {StudioMessage} from './studio-message';
import {RunWorkflowDiagram} from './run-workflow-diagram';
import type {LandingScenario} from './landing-scenarios';

/** Sample data in the same conversation, graph, inspector and run views used by the editor. No writes or model calls. */
export function LandingStudioPreview({scenario}:{scenario:LandingScenario}){
 const [view,setView]=useState<'design'|'run'>('design'),[panel,setPanel]=useState('canvas'),[selected,setSelected]=useState<string|null>(null);
 const current=scenario.workflow.nodes.find(node=>node.id===selected);
 return <div className="as-app screen-workflow landing-studio-preview" aria-label={scenario.label+' 편집기 화면 예시'}>
  <div className="workspace-context"><nav className="studio-tabs" aria-label="예시 화면 선택"><button type="button" className={view==='design'?'active':''} aria-pressed={view==='design'} onClick={()=>setView('design')}>설계</button><button type="button" className={view==='run'?'active':''} aria-pressed={view==='run'} onClick={()=>setView('run')}>직접 실행</button></nav><span className="landing-preview-label">화면 예시</span></div>
  <div className="as-page-title"><h3>{scenario.title}</h3></div>
  {view==='design'?<>
   <nav className="as-panel-switcher" aria-label="예시 편집 영역">{[['conversation','대화'],['canvas','처리 흐름'],['settings','설정']].map(([id,label])=><button key={id} type="button" aria-pressed={panel===id} onClick={()=>setPanel(id)}>{label}</button>)}</nav>
   <div className="as-editor-grid" data-panel={panel}>
    <section className="as-conversation" aria-label="업무 대화 예시"><div className="as-panel-header"><span><MessageSquare size={17}/>업무 대화</span><small>대화로 설정 만들기</small></div><div className="as-messages"><div className="as-resource"><FileText size={16}/><span>{scenario.file}</span></div><StudioMessage role="user" content={scenario.request}/><StudioMessage role="assistant" content={scenario.reply}/><StudioMessage role="user" content={scenario.confirmation}/></div><div className="as-composer"><textarea aria-label="예시 업무 설명" placeholder="실제 업무 설명은 시작하기에서 입력하세요." readOnly/><div className="as-composer-footer"><span className="as-text-button"><Paperclip size={16}/>자료 연결</span><small>Enter 전송 · Shift+Enter 줄바꿈</small><button type="button" className="as-send" disabled aria-label="예시 전송"><ArrowUp size={19}/></button></div></div></section>
    <section className="as-workspace" aria-label="워크플로우 캔버스 예시"><WorkflowPreview workflow={scenario.workflow} autoFit selectedId={selected} onSelect={node=>{setSelected(node.id);setPanel('settings');}}/></section>
    {(current||panel==='settings')&&<section className="as-config" aria-label="작업 설정 예시"><div className="as-panel-header"><span><Settings2 size={17}/>설정</span></div><div className="as-config-body"><NodeInspector node={current||scenario.workflow.nodes[0]} workflow={scenario.workflow} onClose={()=>{setSelected(null);setPanel('canvas');}}/></div></section>}
   </div>
  </>:<div className="landing-preview-run"><section className="as-conversation" aria-label="실행 결과 예시"><div className="as-panel-header"><span><MessageSquare size={17}/>직접 실행 · 결과</span><small>예시 데이터</small></div><div className="as-config-body"><h3>워크플로우 실행 완료</h3><p className="as-preserve">{scenario.result}</p>{scenario.rows&&<div className="as-table-scroll"><table><caption>재고 정리 결과 예시</caption><thead><tr>{scenario.rows[0].map(cell=><th key={cell}>{cell}</th>)}</tr></thead><tbody>{scenario.rows.slice(1).map((row,i)=><tr key={i}>{row.map((cell,j)=><td key={j}>{cell}</td>)}</tr>)}</tbody></table></div>}</div></section><RunWorkflowDiagram workflow={scenario.workflow} runStatus="succeeded" steps={scenario.workflow.nodes.map(node=>({node:node.id,label:node.label,status:'completed'}))}/></div>}
 </div>;
}
