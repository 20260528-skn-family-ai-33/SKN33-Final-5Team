import {ArrowLeft} from 'lucide-react';
import {WorkflowToolIcon} from './workflow-tool-icon';
import type {Workflow} from './workflow-preview';
import type {ToolInfo} from './node-library';
type RulePlan={filters:{field:string;operator:string;compare_field?:string|null;value?:unknown}[];sort:{field:string;direction:string}[];match:string;missing_policy:string;duplicate_policy:string;duplicate_field?:string|null};
const settingLabels:Record<string,string>={max_results:'조회 한도',limit:'한도',count:'건수',query:'검색 조건',channel_id:'채널 ID',group_by:'묶을 열',value_column:'계산할 열',null_policy:'빈 값 처리',format:'결과 형식',output_format:'결과 형식',threshold:'기준값',temperature:'모델 온도',fields:'사용할 열'};
function displayedSettings(settings:Record<string,unknown>){
 let args:unknown;
 try{args=typeof settings.arguments_json==='string'?JSON.parse(settings.arguments_json):null;}catch{args=null;}
 const source=args&&typeof args==='object'&&!Array.isArray(args)?args as Record<string,unknown>:{};
 const entries=Object.entries({...source,...Object.fromEntries(['fields','query','format'].filter(key=>settings[key]!==undefined).map(key=>[key,settings[key]]))});
 return entries.flatMap(([key,value])=>{
  if(/secret|token|password|credential|api.?key|authorization/i.test(key))return [];
  const shown=Array.isArray(value)&&value.every(item=>['string','number','boolean'].includes(typeof item))?value.join(', '):
   ['string','number','boolean'].includes(typeof value)?String(value):'';
  return shown?[{key,name:settingLabels[key]||key,value:shown.slice(0,300)}]:[];
 });
}
export function NodeInspector({node,tool,workflow,onClose,backLabel='전체 설정',footerNote='변경할 내용은 대화에서 설명하세요. 저장한 작업을 수정하려면 작업 라이브러리의 작업 템플릿을 열어 주세요.'}:{node:Workflow['nodes'][number];tool?:ToolInfo;workflow?:Workflow|null;onClose:()=>void;backLabel?:string;footerNote?:string}){
 const settings=node.settings;
 const values=displayedSettings(settings);
 const outputNames:Record<string,string>={documents:'문서 내용',table:'표',text:'작성한 내용',answer:'답변',records:'추출한 항목',reviews:'검수 결과',artifact:'결과 파일',decision:'조건 판단'};
 const sourceName=(id:string)=>workflow?.nodes.find(n=>n.id===id)?.label||'이전 작업';
 return <div className="as-node-inspector">
 <button className="as-text-button" onClick={onClose}><ArrowLeft size={14}/>{backLabel}</button>
 <div className="as-setting-section"><label>작업</label><h3 className="as-node-inspector-title"><WorkflowToolIcon tool={node.tool}/>{node.label}</h3></div>
 <div className="as-setting-section"><label>사용하는 자료</label>{node.bindings.length?node.bindings.map((b,i)=><p className="as-binding" key={i}>{sourceName(b.node_id)}의 {outputNames[b.output]||'결과'}</p>):<p className="as-small-muted">실행 요청 또는 연결한 자료를 사용합니다.</p>}</div>
 {tool&&<div className="as-setting-section"><label>처리 결과</label>{Object.entries(tool.output_ports).map(([name])=><p key={name}>{outputNames[name]||'처리 결과'}</p>)}</div>}
 {typeof settings.instructions==='string'&&settings.instructions&&<div className="as-setting-section"><label>작업 내용</label><p className="as-preserve as-instructions">{settings.instructions}</p></div>}
 {values.length>0&&<div className="as-setting-section"><label>설정값</label><dl className="as-node-values">{values.map(({key,name,value})=><div key={key}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></div>}
 {settings.execution_plan!=null&&<div className="as-setting-section"><label>처리 규칙</label><RuleSummary plan={settings.execution_plan as RulePlan}/></div>}
 <details className="as-setting-section"><summary>기술 세부 정보</summary><pre className="as-node-json">{JSON.stringify({id:node.id,tool:node.tool,bindings:node.bindings,settings},null,2)}</pre></details>
 <p className="as-small-muted as-inspector-note">{footerNote}</p>
 </div>;
}

export function RuleSummary({plan}:{plan?:RulePlan|null}){if(!plan)return null;return <div className="as-rule-summary">{plan.filters.map((f,i)=><p key={i}>{f.field} {f.operator} {f.compare_field||String(f.value??'')}</p>)}{plan.sort.map((v,i)=><p key={i}>{v.field} · {v.direction}</p>)}<p>조건 결합: {plan.match==='all'?'모두 충족':'하나 이상 충족'} · 빈 값: {plan.missing_policy}</p><p>중복: {plan.duplicate_policy}{plan.duplicate_field?' · '+plan.duplicate_field:''}</p></div>;}
