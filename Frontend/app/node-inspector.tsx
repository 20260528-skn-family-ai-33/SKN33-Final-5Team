import {ArrowLeft} from 'lucide-react';
import {WorkflowToolIcon} from './workflow-tool-icon';
import type {Workflow} from './workflow-preview';
import type {ToolInfo} from './node-library';
type RulePlan={filters:{field:string;operator:string;compare_field?:string|null;value?:unknown}[];sort:{field:string;direction:string}[];match:string;missing_policy:string;duplicate_policy:string;duplicate_field?:string|null};
export function NodeInspector({node,tool,workflow,onClose}:{node:Workflow['nodes'][number];tool?:ToolInfo;workflow?:Workflow|null;onClose:()=>void}){
 const settings=node.settings;
 const outputNames:Record<string,string>={documents:'문서 내용',table:'표',text:'작성한 내용',answer:'답변',records:'추출한 항목',reviews:'검수 결과',artifact:'결과 파일',decision:'조건 판단'};
 const sourceName=(id:string)=>workflow?.nodes.find(n=>n.id===id)?.label||'이전 작업';
 return <div className="as-node-inspector">
 <button className="as-text-button" onClick={onClose}><ArrowLeft size={14}/>전체 설정</button>
 <div className="as-setting-section"><label>작업</label><h3 className="as-node-inspector-title"><WorkflowToolIcon tool={node.tool}/>{node.label}</h3></div>
 <div className="as-setting-section"><label>사용하는 자료</label>{node.bindings.length?node.bindings.map((b,i)=><p className="as-binding" key={i}>{sourceName(b.node_id)}의 {outputNames[b.output]||'결과'}</p>):<p className="as-small-muted">실행 요청 또는 연결한 자료를 사용합니다.</p>}</div>
 {tool&&<div className="as-setting-section"><label>처리 결과</label>{Object.entries(tool.output_ports).map(([name])=><p key={name}>{outputNames[name]||'처리 결과'}</p>)}</div>}
 {typeof settings.instructions==='string'&&settings.instructions&&<div className="as-setting-section"><label>작업 내용</label><p className="as-preserve as-instructions">{settings.instructions}</p></div>}
 {settings.execution_plan!=null&&<div className="as-setting-section"><label>처리 규칙</label><RuleSummary plan={settings.execution_plan as RulePlan}/></div>}
 <details className="as-setting-section"><summary>기술 세부 정보</summary><pre className="as-node-json">{JSON.stringify({id:node.id,tool:node.tool,bindings:node.bindings,settings},null,2)}</pre></details>
 <p className="as-small-muted as-inspector-note">변경할 내용은 대화에서 설명하세요. 저장한 작업을 수정하려면 작업 라이브러리의 작업 템플릿을 열어 주세요.</p>
 </div>;
}

export function RuleSummary({plan}:{plan?:RulePlan|null}){if(!plan)return null;return <div className="as-rule-summary">{plan.filters.map((f,i)=><p key={i}>{f.field} {f.operator} {f.compare_field||String(f.value??'')}</p>)}{plan.sort.map((v,i)=><p key={i}>{v.field} · {v.direction}</p>)}<p>조건 결합: {plan.match==='all'?'모두 충족':'하나 이상 충족'} · 빈 값: {plan.missing_policy}</p><p>중복: {plan.duplicate_policy}{plan.duplicate_field?' · '+plan.duplicate_field:''}</p></div>;}
