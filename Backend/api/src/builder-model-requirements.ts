/** Inspect the pinned graph, including saved graph nodes, without loading credentials. */
import {readFileSync} from 'node:fs';
import {backendPath} from './paths';

type OperationContract={id:string;status:string;backend_tool?:string};
const catalog=JSON.parse(readFileSync(backendPath('workflow_engine/unified_tools/catalog.json'),'utf8')) as
 {tools:{id:string;operations:OperationContract[]}[]};
const operations=new Map(catalog.tools.map(tool=>[tool.id,tool.operations.filter(op=>!op.backend_tool)]));
const nativeModelTools=new Set(['search_document','extract_information','compose_answer','reply_review','agent_node']);
const gmailReadTools=new Set(['email.list','email.get','email.attachment.get']);

export function builderNeedsGmail(config:any,dependencies:Record<string,any>={}):boolean{
 return !!config?.workflow?.nodes?.some((node:any)=>{
  if(gmailReadTools.has(node.tool))return true;
  const child=node.tool==='saved_workflow'?dependencies[node.id]:undefined;
  return !!child&&builderNeedsGmail(child.config,child.workflow_dependencies);
 });
}

export function builderNeedsModel(config:any,dependencies:Record<string,any>={}):boolean{
 return !config?.workflow||config.workflow.nodes.some((n:any)=>{
  const settings=n.settings||{},available=operations.get(n.tool)||[];
  if(settings.operation)return available.find(op=>op.id===settings.operation)?.status==='model_connection_required';
  if(nativeModelTools.has(n.tool))return true;
  if(n.tool==='condition')return settings.condition_operator==='semantic';
  if(n.tool==='ask_user')return !!settings.instructions;
  if(n.tool==='saved_workflow')return !!dependencies[n.id]&&builderNeedsModel(dependencies[n.id].config,dependencies[n.id].workflow_dependencies||{});
  // Non-native tools with a single operation may omit its ID. The Python
  // executor resolves the same default; aliases never substitute tool IDs.
  return available.length===1&&available[0].status==='model_connection_required';
 });
}
