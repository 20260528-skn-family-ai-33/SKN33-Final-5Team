import type {Flow} from './workflow';
/** Only require uploaded files when the reader receives start data directly.
 * Files produced by an upstream step must be checked at runtime instead. */
export function missingStartFiles(flow:Flow,input:any[]):string[]{
 const triggers=new Set(['n8n-nodes-base.manualTrigger','n8n-nodes-base.webhook','n8n-nodes-base.scheduleTrigger']);
 return flow.nodes.filter(node=>['easyflow.documentRead','n8n-nodes-base.extractFromFile'].includes(node.type)).flatMap(node=>{
  const parents=flow.nodes.filter(parent=>flow.connections[parent.name]?.main?.some(branch=>branch.some(edge=>edge.node===node.name)));
  if(parents.length!==1||!triggers.has(parents[0].type))return [];
  const property=node.parameters.binaryPropertyName||'data';
  if(typeof property!=='string'||property.includes('{{'))return [];
  return input.length&&input.every(item=>item?.__easyflowBinary?.[property])?[]:[`「${node.name}」에 필요한 '${property}' 파일이 없어요. 파일을 선택하거나 앞 단계에서 파일을 생성·가져오도록 연결해주세요.`];
 });
}
