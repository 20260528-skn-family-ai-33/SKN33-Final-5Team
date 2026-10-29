import type {LibraryNode} from './node-library';

const defaults={node_ref:null,file_ids:[],query:null,fields:[],instructions:null,format:null,question:null,condition_field:null,condition_operator:null,aggregation:null,sum_field:null};
const numericOperators=['lt','lte','gt','gte','eq'];
export function canEditFirstFilter(node:LibraryNode|null,tool:string):boolean{
 if(!node||node.spec.tool!==tool)return true;
 const first=node.spec.execution_plan?.filters?.[0];
 return !!first&&!first.compare_field&&numericOperators.includes(first.operator)&&typeof first.value==='number'&&(!first.value_type||first.value_type==='number');
}
export type NodeForm={kind:'agent'|'processing';name:string;purpose:string;instructions:string;reference:string;model:string;tool:string;field:string;operator:string;value:string;group:string;aggregation:string;sumField:string};
export function nodeSpecFromForm(form:NodeForm,editing:LibraryNode|null){
 const {kind,name,purpose,instructions,reference,model,field,operator,value,group,aggregation,sumField}=form;
 const tool=kind==='agent'?'agent_node':form.tool;
 const sameTool=editing?.spec.tool===tool;
 const prior=sameTool?editing!.spec:null;
 let execution_plan=prior?.execution_plan?structuredClone(prior.execution_plan):null;
 if(tool==='filter_sort'&&canEditFirstFilter(editing,tool)){
  if(!field.trim()||!value.trim()||!Number.isFinite(Number(value)))throw new Error('필터 열과 유한한 숫자 기준을 입력하세요.');
  const first={field:field.trim(),operator,value:Number(value),compare_field:null,value_type:'number'};
  execution_plan=execution_plan?{...execution_plan,filters:[first,...execution_plan.filters.slice(1)]}:{filters:[first],sort:[],match:'all',missing_policy:'error',duplicate_policy:'keep',duplicate_field:null,output_format:'json'};
 }
 const settings={...defaults,...(prior?.settings||{})};
 if(tool==='aggregate_table')Object.assign(settings,{fields:group.split(',').map(v=>v.trim()).filter(Boolean),aggregation,sum_field:aggregation==='sum'?sumField.trim():null});
 return {kind,name,purpose,tool,instructions:kind==='agent'?instructions:'',reference:kind==='agent'?reference:'',model:kind==='agent'&&model.trim()?model.trim():null,settings,execution_plan};
}
