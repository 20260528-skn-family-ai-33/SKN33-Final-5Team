import catalogData from './catalog.json';
import {renameNodeReferences} from './node-references';
export type Json = Record<string, any>;
export type FlowNode = {id:string; name:string; type:string; typeVersion:number; position:[number,number]; parameters:Json; disabled?:boolean; credentials?:Json; [key:string]:any};
export type Connection = {node:string; type:string; index:number; [key:string]:any};
export type Flow = {id:string; name:string; nodes:FlowNode[]; connections:Record<string,Record<string,Connection[][]>>; settings:Json; active?:boolean; n8nId?:string; [key:string]:any};
export type NodeRun = {nodeId:string; name:string; input:Json[]; output:Json[][]; status:'success'|'error'|'skipped'; duration:number; error?:string;attempts?:number;continued?:boolean;pinned?:boolean;[key:string]:any};
export type Execution = {id:string; workflowId:string; workflowName:string; status:'success'|'error'|'queued'|'running'|'waiting'|'canceled'; startedAt:string; duration:number; runs:NodeRun[]; mode:'learning'|'live'; error?:string; [key:string]:any};
export type NodeField={key:string;label:string;type:'text'|'textarea'|'number'|'select'|'json';options?:{value:string;label:string}[];placeholder?:string;help?:string;showWhen?:Record<string,string[]>};
export type NodeDescriptor={id:string;name:string;englishName:string;category:string;n8nType:string;typeVersion:number;description:string;parameters:Json;locallyExecutable:boolean;setupGuidance:string[];fields?:NodeField[];credentialProviders?:string[];credentialService?:string;credentialRequired?:boolean;inputs?:number;outputs?:string[];[key:string]:any};
export const catalog:NodeDescriptor[] = catalogData.catalog as NodeDescriptor[];
export const templates = catalogData.templates;
export const clone = <T,>(value:T):T => JSON.parse(JSON.stringify(value));
export function nodeInfo(node:FlowNode) { return catalog.find(c=>c.n8nType===node.type); }
export function validateFlow(value:any):Flow {
 if(!value||typeof value.name!=='string'||!value.name.trim()||!Array.isArray(value.nodes)||!value.connections||typeof value.connections!=='object'||Array.isArray(value.connections)) throw new Error('워크플로 이름, 단계, 연결이 있는 워크플로 JSON을 넣어주세요.');
 if(value.nodes.length>200)throw new Error('한 번에 200개 단계까지 가져올 수 있어요.');
 const names=new Set<string>(); const ids=new Set<string>();
 for(const node of value.nodes) { if(!node||typeof node.name!=='string'||!node.name.trim()||typeof node.type!=='string'||!Number.isFinite(node.typeVersion)||!Array.isArray(node.position)||node.position.length!==2||!node.position.every(Number.isFinite)||!node.parameters||typeof node.parameters!=='object')throw new Error('이름, 종류, 버전, 위치, 설정이 없는 단계가 있어요.'); if(!node.id)node.id=crypto.randomUUID();if(typeof node.id!=='string')throw new Error('단계 ID를 확인해주세요.'); if(names.has(node.name)||ids.has(node.id))throw new Error('단계 이름과 ID는 서로 달라야 해요.'); names.add(node.name);ids.add(node.id);if(!node.id)node.id=crypto.randomUUID(); }
 for(const [name,channels] of Object.entries(value.connections)) { if(!names.has(name)||!channels||typeof channels!=='object')throw new Error('연결의 시작 단계가 존재하지 않아요.'); for(const outputs of Object.values(channels as Json)) { if(!Array.isArray(outputs))throw new Error('잘못된 연결 형식이에요.');for(const edges of outputs) { if(!Array.isArray(edges))throw new Error('잘못된 연결 경로예요.');for(const edge of edges)if(!names.has(edge.node)||!Number.isInteger(edge.index)||edge.index<0)throw new Error('연결된 다음 단계가 없거나 입력 번호가 잘못됐어요.'); } } }
 return {...value,id:value.id||crypto.randomUUID(),settings:value.settings||{executionOrder:'v1',timezone:'Asia/Seoul'}};
}
export function makeFlow(templateId='first'):Flow {
 if(templateId!=='first') { const template=templates.find(t=>t.id===templateId); if(template)return {...clone(template.workflow),id:crypto.randomUUID()} as unknown as Flow; }
 const ids=['start','fields','condition','result'];
 const nodes:FlowNode[]=[
 {id:ids[0],name:'클릭해서 시작',type:'n8n-nodes-base.manualTrigger',typeVersion:1,position:[110,325],parameters:{}},
 {id:ids[1],name:'데이터 정리',type:'n8n-nodes-base.set',typeVersion:3.4,position:[335,325],parameters:{mode:'manual',assignments:{assignments:[{id:'name',name:'name',value:'예시 고객 김민지',type:'string'},{id:'score',name:'score',value:92,type:'number'},{id:'content',name:'content',value:'처음 만든 자동화가 잘 실행되는지 확인하고 있어요.',type:'string'}]},includeOtherFields:false,options:{}}},
 {id:ids[2],name:'조건 확인',type:'n8n-nodes-base.if',typeVersion:2.2,position:[560,325],parameters:{conditions:{options:{typeValidation:'strict',version:2},conditions:[{id:'score',leftValue:'={{ $json.score }}',rightValue:80,operator:{type:'number',operation:'gte'}}],combinator:'and'},options:{}}},
 {id:ids[3],name:'결과 만들기',type:'n8n-nodes-base.set',typeVersion:3.4,position:[785,325],parameters:{mode:'manual',assignments:{assignments:[{id:'result',name:'result',value:'첫 번째 자동화를 완성했어요!',type:'string'},{id:'name',name:'name',value:'={{ $json.name }}',type:'string'},{id:'score',name:'score',value:'={{ $json.score }}',type:'number'}]},includeOtherFields:false,options:{}}}];
 return {id:crypto.randomUUID(),name:'나의 첫 번째 자동화',nodes,connections:{'클릭해서 시작':{main:[[{node:'데이터 정리',type:'main',index:0}]]},'데이터 정리':{main:[[{node:'조건 확인',type:'main',index:0}]]},'조건 확인':{main:[[{node:'결과 만들기',type:'main',index:0}],[]]}},settings:{executionOrder:'v1',timezone:'Asia/Seoul'},active:false};
}
export function exportFlow(flow:Flow) { const {id,n8nId,updatedAt,...data}=flow; void id;void n8nId;void updatedAt;return data; }
export function renameNode(flow:Flow,id:string,name:string) {const next=clone(flow);const node=next.nodes.find(n=>n.id===id);if(!node)return next;const old=node.name;if(old===name)return next;if(!name.trim()||next.nodes.some(n=>n.id!==id&&n.name===name))throw new Error('단계 이름은 비어 있거나 다른 단계와 같을 수 없어요.'); node.name=name;if(next.pinData&&Object.hasOwn(next.pinData,old)){next.pinData[name]=next.pinData[old];delete next.pinData[old];} if(next.connections[old]){next.connections[name]=next.connections[old];delete next.connections[old];}for(const channels of Object.values(next.connections))for(const outputs of Object.values(channels))for(const edges of outputs)for(const edge of edges)if(edge.node===old)edge.node=name;for(const n of next.nodes)n.parameters=renameNodeReferences(n.parameters,old,name,{nodeType:n.type});return next;}
export function deleteNode(flow:Flow,id:string) {const next=clone(flow);const node=next.nodes.find(n=>n.id===id);if(!node)return next;next.nodes=next.nodes.filter(n=>n.id!==id);delete next.connections[node.name];if(next.pinData)delete next.pinData[node.name];for(const channels of Object.values(next.connections))for(const outputs of Object.values(channels))for(let i=0;i<outputs.length;i++)outputs[i]=outputs[i].filter(e=>e.node!==node.name);return next;}

export function nodePorts(node:FlowNode):{inputs:number;outputs:string[]} {
 if(node.type==='n8n-nodes-base.stickyNote')return {inputs:0,outputs:[]};
 const info=nodeInfo(node);const p=node.parameters;let outputs=info?.outputs||['결과'];
 if(node.type==='n8n-nodes-base.respondToWebhook')outputs=node.typeVersion===1.3||node.typeVersion>=1.4&&p.enableResponseOutput===true?['받은 정보','보낸 응답']:['받은 정보'];
 if(node.type==='n8n-nodes-base.if')outputs=['맞아요','아니에요'];
 if(node.type==='n8n-nodes-base.switch'){
  if(p.mode==='expression')outputs=Array.from({length:Math.max(1,Math.min(16,Number(p.numberOutputs)||4))},(_,i)=>'경로 '+(i+1));
  else {outputs=(Array.isArray(p.rules?.values)?p.rules.values:[]).map((rule:any,i:number)=>rule?.renameOutput&&rule?.outputKey?String(rule.outputKey):'조건 '+(i+1));if(p.options?.fallbackOutput==='extra')outputs.push('나머지');if(!outputs.length)outputs=['결과'];}
 }
 if(node.onError==='continueErrorOutput')outputs=[...outputs,'오류'];
 const inputs=node.type==='n8n-nodes-base.merge'?(p.mode==='combine'&&['combineByFields','combineAll'].includes(p.combineBy??'combineByFields')?2:Math.max(2,Math.min(10,Number(p.numberInputs)||2))):(info?.inputs??(info?.category==='시작'?0:1));
 return {inputs,outputs};
}
