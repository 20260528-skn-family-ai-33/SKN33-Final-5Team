import {clone,type Flow,type FlowNode,type Json} from '../lib/workflow';

export const loopNodeType='n8n-nodes-base.splitInBatches';
export type LoopEdge={source:string;target:string;output:number;input:number;key:string};
export type LoopRegion={owner?:string;ordered:FlowNode[]};
export type LoopPlan={node:FlowNode;body:LoopRegion;entry:LoopEdge[];feedback:LoopEdge;members:Set<string>};
export type StructuredPlan={root:LoopRegion;loops:Map<string,LoopPlan>;edges:LoopEdge[];feedbackKeys:Set<string>};
export type LoopContext={items:Json[];processedItems:Json[];currentRunIndex:number;maxRunIndex:number;noItemsLeft:boolean;done:boolean};

/** This is our own bounded implementation of the documented v3 state transitions. */
export function nextBatch(previous:LoopContext|undefined,input:Json[],batchSize:number,reset=false):{context:LoopContext;output:Json[][]} {
 if(!Number.isSafeInteger(batchSize)||batchSize<1||batchSize>5000)throw new Error('반복 묶음 크기는 1~5,000 사이의 정수로 설정해주세요.');
 if(input.length>5000)throw new Error('반복은 한 번에 5,000개 항목까지 처리할 수 있어요.');
 const fresh=!previous||reset;
 const context:LoopContext=fresh?{items:clone(input),processedItems:[],currentRunIndex:0,maxRunIndex:Math.ceil(input.length/batchSize),noItemsLeft:false,done:false}:clone(previous);
 if(!fresh){context.currentRunIndex++;context.processedItems.push(...clone(input));if(context.processedItems.length>5000)throw new Error('반복 결과는 5,000개 항목까지 모을 수 있어요.');}
 const batch=context.items.splice(0,batchSize);context.noItemsLeft=!context.items.length;context.done=!batch.length;
 return {context,output:context.done?[clone(context.processedItems),[]]:[[],batch]};
}

function ordered(nodes:FlowNode[],edges:LoopEdge[]):FlowNode[] {
 const byName=new Map(nodes.map(node=>[node.name,node])),counts=new Map(nodes.map(node=>[node.name,0]));
 for(const edge of edges)if(byName.has(edge.source)&&byName.has(edge.target))counts.set(edge.target,counts.get(edge.target)!+1);
 const queue=nodes.filter(node=>!counts.get(node.name)),result:FlowNode[]=[];
 while(queue.length){const node=queue.shift()!;result.push(node);for(const edge of edges)if(edge.source===node.name&&byName.has(edge.target)){counts.set(edge.target,counts.get(edge.target)!-1);if(!counts.get(edge.target))queue.push(byName.get(edge.target)!);}}
 if(result.length!==nodes.length)throw new Error('지원하지 않는 순환 연결이 있어요. 반복 경로는 반복 단계의 입력으로 돌아와야 해요.');
 return result;
}

/** Validate the entire graph before any effect. Only structured loop feedback may form cycles. */
export function compileLoopPlan(flow:Flow):StructuredPlan {
 const byName=new Map(flow.nodes.map(node=>[node.name,node])),edges:LoopEdge[]=[],loops=new Map<string,LoopPlan>();
 for(const [source,channels] of Object.entries(flow.connections))for(const [output,connections] of (channels.main||[]).entries())for(const [index,edge] of connections.entries())edges.push({source,target:edge.node,output,input:edge.index,key:`${source}:${output}:${index}`});
 for(const node of flow.nodes.filter(node=>node.type===loopNodeType)){
  if(node.typeVersion!==3)throw new Error(`${node.name}: 반복은 Loop Over Items 버전 3을 지원해요.`);
  const entry=edges.filter(edge=>edge.source===node.name&&edge.output===1),members=new Set<string>(),feedback:LoopEdge[]=[];
  const visit=(edge:LoopEdge)=>{
   if(edge.target===node.name){feedback.push(edge);return;}
   if(members.has(edge.target))return;members.add(edge.target);
   for(const next of edges.filter(item=>item.source===edge.target))visit(next);
  };
  for(const edge of entry)visit(edge);
  if(!node.disabled&&feedback.length!==1)throw new Error(`${node.name}: 반복 본문에서 입력으로 돌아오는 연결을 하나 만들어주세요. 여러 복귀 경로는 먼저 하나로 모아주세요.`);
  loops.set(node.name,{node,entry,members,feedback:feedback[0],body:{owner:node.name,ordered:[]}});
 }
 const feedbackKeys=new Set([...loops.values()].flatMap(loop=>loop.feedback?[loop.feedback.key]:[]));
 const dag=edges.filter(edge=>!feedbackKeys.has(edge.key));ordered(flow.nodes,dag);
 const owner=new Map<string,string|undefined>();
 for(const node of flow.nodes){
  const containing=[...loops.values()].filter(loop=>loop.members.has(node.name));
  for(const a of containing)for(const b of containing)if(a!==b&&!a.members.has(b.node.name)&&!b.members.has(a.node.name))throw new Error('반복 본문이 서로 겹쳐요. 반복을 차례대로 연결하거나 하나의 반복 안에 넣어주세요.');
  const deepest=containing.find(loop=>!containing.some(other=>other!==loop&&loop.members.has(other.node.name)));
  owner.set(node.name,deepest?.node.name);
 }
 for(const edge of edges){
  const sourceLoop=loops.get(edge.source),sourceOwner=owner.get(edge.source),targetOwner=owner.get(edge.target);
  if(feedbackKeys.has(edge.key)){
   if(sourceOwner!==edge.target&&!(edge.source===edge.target&&edge.output===1))throw new Error('안쪽 반복을 건너뛰는 복귀 연결은 지원하지 않아요. 안쪽 완료 경로에서 바깥 반복으로 돌아와주세요.');
  }else if(sourceLoop&&edge.output===1){
   if(targetOwner!==edge.source)throw new Error('반복 경로는 해당 반복의 본문으로 연결해주세요.');
  }else if(sourceOwner!==targetOwner)throw new Error('반복 본문에는 반복 출력에서만 들어갈 수 있어요. 반복 밖 연결은 완료 경로를 사용해주세요.');
 }
 for(const loop of loops.values())loop.body.ordered=ordered(flow.nodes.filter(node=>owner.get(node.name)===loop.node.name),dag);
 const root={ordered:ordered(flow.nodes.filter(node=>owner.get(node.name)===undefined),dag)};
 // Missing names are rejected by validateFlow; retain this assertion for direct callers.
 if(edges.some(edge=>!byName.has(edge.source)||!byName.has(edge.target)))throw new Error('연결된 단계를 찾을 수 없어요.');
 return {root,loops,edges,feedbackKeys};
}
