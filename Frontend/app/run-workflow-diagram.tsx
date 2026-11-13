import {WorkflowToolIcon} from './workflow-tool-icon';
import type {CSSProperties} from 'react';
import {ArrowDown, ArrowRight, Check, Circle, Clock3, Workflow as WorkflowIcon, X} from 'lucide-react';

type Node = {id:string; label:string; tool:string};
type Edge = {source:string; target:string; branch:string};
export type RunWorkflow = {entry:string; nodes:Node[]; edges:Edge[]};
type Step = {node:string; label:string; status:string};
type Props = {workflow?:RunWorkflow|null; steps?:Step[]; runStatus?:string; waitingNodeId?:string|null};

const branchNames:Record<string,string> = {next:'다음', yes:'조건 충족', no:'조건 미충족'};
const states = {
  completed:{text:'완료',icon:Check}, succeeded:{text:'완료',icon:Check},
  failed:{text:'실패',icon:X}, needs_input:{text:'답변 대기',icon:Clock3},
  running:{text:'실행 중',icon:Clock3}, unrecorded:{text:'기록 없음',icon:Circle},
};

function Badge({status}:{status?:string}) {
  if (!status) return null;
  const {text,icon:Icon} = states[status as keyof typeof states] || states.unrecorded;
  return <span className="rwd-status" data-state={status}><Icon aria-hidden="true" size={14}/>{text}</span>;
}

function graphLayout(flow: RunWorkflow) {
  const nodes = new Map(flow.nodes.map(node => [node.id,node]));
  const outgoing = new Map<string,Edge[]>(flow.nodes.map(node => [node.id,[]]));
  const incoming = new Map(flow.nodes.map(node => [node.id,0]));
  for (const edge of flow.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) continue;
    outgoing.get(edge.source)!.push(edge);
    incoming.set(edge.target, incoming.get(edge.target)!+1);
  }
  const ordered = flow.nodes.filter(node => incoming.get(node.id) === 0);
  for (const node of ordered) {
    for (const edge of outgoing.get(node.id)!) {
      const remaining = incoming.get(edge.target)!-1;
      incoming.set(edge.target, remaining);
      if (remaining === 0) ordered.push(nodes.get(edge.target)!);
    }
  }
  // Keep nodes visible even for incomplete historical graphs.
  const indices = new Map(ordered.map((node,index) => [node.id,index]));
  for (const node of flow.nodes) {
    if (!indices.has(node.id)) { indices.set(node.id, ordered.length); ordered.push(node); }
  }
  const lanes:number[] = [];
  const rails:{from:number; to:number; lane:number}[] = [];
  for (const [from,node] of ordered.entries()) {
    for (const edge of outgoing.get(node.id)!) {
      const to = indices.get(edge.target)!;
      if (to <= from+1) continue;
      let lane = lanes.findIndex(end => end < from);
      if (lane < 0) lane = lanes.length;
      lanes[lane] = to;
      rails.push({from,to,lane});
    }
  }
  return {ordered, outgoing, indices, rails, laneCount:lanes.length};
}

export function RunWorkflowDiagram({workflow,steps=[],runStatus,waitingNodeId}:Props) {
  const graph = workflow?.nodes.length ? graphLayout(workflow) : null;
  if (!graph && !steps.length) return null;
  const recorded = new Map(steps.map(step => [step.node,step.status]));
  // Without a saved graph, show the real trace in its observed order, without inferred links.
  const nodes = graph?.ordered ?? steps.map(step => ({id:step.node,label:step.label,tool:''}));
  const lanes = graph?.laneCount ?? 0;
  const title = graph ? '워크플로우 흐름' : '실행 단계';
  const long = nodes.length > 6;
  return <section className="run-workflow-diagram" aria-label={title}>
    <header className="rwd-heading">
      <h3><WorkflowIcon aria-hidden="true" size={20}/>{title}</h3>
      <span>{nodes.length}개 단계</span>
      {long && <span className="rwd-scroll-hint">아래로 스크롤해 다음 단계를 확인하세요.</span>}
    </header>
    <div className={long ? 'rwd-scroll' : undefined} tabIndex={long ? 0 : undefined}
      role={long ? 'region' : undefined} aria-label={long ? '전체 단계 보기' : undefined}>
    <div className="rwd-map" role="list" style={{'--rwd-gutter':`min(${lanes ? lanes*12+8 : 0}px, 18%)`} as CSSProperties}>
      {nodes.map((node,index) => {
        const edges = graph?.outgoing.get(node.id) ?? [];
        const straight = graph && edges.some(edge => graph.indices.get(edge.target) === index+1);
        const status = !graph ? steps[index].status
          : runStatus === 'needs_input' && waitingNodeId === node.id ? 'needs_input'
          : recorded.get(node.id) || (runStatus ? 'unrecorded' : undefined);
        return <div role="listitem" key={graph ? node.id : index} data-workflow-node={node.id}
          className="rwd-row" style={{gridRow:index+1}}>
          <div className="rwd-card">
            <span className="rwd-type"><WorkflowToolIcon tool={node.tool} size={21}/></span>
            <div className="rwd-copy"><span className="rwd-index">{index+1}단계</span><strong>{node.label}</strong></div>
            <Badge status={status}/>
            {graph && edges.length > 0 && <div className={straight && edges.length === 1 && edges[0].branch==='next' ? 'rwd-sr' : 'rwd-branches'}>
              {edges.map(edge => {
                const target = graph.indices.get(edge.target)!;
                return <span key={edge.branch+':'+edge.target} className="rwd-branch">{branchNames[edge.branch] || edge.branch}{' '}
                  <ArrowRight aria-hidden="true" size={13}/>{target+1}단계 {nodes[target].label}</span>;
              })}
            </div>}
          </div>
          {straight && <ArrowDown aria-hidden="true" className="rwd-next" size={20}/>}
        </div>;
      })}
      {graph?.rails.map(({from,to,lane},index) => <span aria-hidden="true" className="rwd-rail" key={index}
        style={{gridRow:`${from+1} / ${to+1}`,width:`calc(var(--rwd-gutter) * ${(lane+1)/(lanes+.35)})`}}/>)}
    </div>
    </div>
  </section>;
}
