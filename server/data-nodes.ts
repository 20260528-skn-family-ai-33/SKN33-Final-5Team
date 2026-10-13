import {randomInt} from 'node:crypto';
import {clone,type FlowNode,type Json} from '../lib/workflow';
import {attachItemPairs,type ItemPair} from './provenance';

export const dataNodeTypes=['switch','filter','limit','sort','splitOut','removeDuplicates','aggregate','stopAndError'].map(id=>`n8n-nodes-base.${id}`);
export type DataContext={resolve:(value:any,item:Json)=>any;matches:(conditions:Json,item:Json)=>boolean;code:(source:string,input:Json[],mode:string)=>Promise<Json[]>};
const unsafe=new Set(['__proto__','prototype','constructor']);
const fields=(value:any):string[]=>{if(typeof value!=='string'&&!Array.isArray(value))throw new Error('항목 이름을 쉼표로 구분해서 입력해주세요.');return (Array.isArray(value)?value:String(value).split(',')).map(v=>String(v).trim()).filter(Boolean);};
function path(field:string,literal=false):string[] {const keys=literal?[field]:field.replace(/\[(\d+)\]/g,'.$1').split('.');if(keys.some(k=>!k||unsafe.has(k)))throw new Error('사용할 수 없는 항목 이름이에요.');return keys;}
function get(item:any,field:string,literal=false):any {let value=item;for(const key of path(field,literal))value=value!=null&&Object.prototype.hasOwnProperty.call(value,key)?value[key]:undefined;return value;}
function put(item:Json,field:string,value:any,literal=false):void {const keys=path(field,literal);let target=item;for(const key of keys.slice(0,-1)){if(target[key]===undefined)target[key]={};if(!target[key]||typeof target[key]!=='object'||Array.isArray(target[key]))throw new Error('출력 항목 이름이 서로 겹쳐요.');target=target[key];}target[keys[keys.length-1]]=value;}
function remove(item:Json,field:string,literal=false):void {const keys=path(field,literal);let target:any=item;for(const key of keys.slice(0,-1)){target=target?.[key];if(target==null)return;}delete target[keys[keys.length-1]];}
function select(item:Json,selected:string[],literal=false):Json {const result:Json={};for(const field of selected){const value=get(item,field,literal);if(value!==undefined)put(result,field,clone(value),literal);}return result;}
function stable(value:any):string {if(value===undefined)return 'undefined';if(value===null)return 'null';if(Array.isArray(value))return `[${value.map(stable).join(',')}]`;if(typeof value==='object')return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${stable(value[k])}`).join(',')}}`;return `${typeof value}:${JSON.stringify(value)}`;}
export function normalOutputCount(node:FlowNode):number {
 if(node.type==='n8n-nodes-base.if')return 2;
 if(node.type==='n8n-nodes-base.stopAndError')return 0;
 if(node.type==='n8n-nodes-base.switch'){const p=node.parameters;return p.mode==='expression'?Number(p.numberOutputs??4):(Array.isArray(p.rules?.values)?p.rules.values.length:0)+(p.options?.fallbackOutput==='extra'?1:0);}
 return 1;
}
export async function executeDataNode(node:FlowNode,input:Json[],ctx:DataContext):Promise<Json[][]> {
 const p=node.parameters,options=p.options||{},literal=!!options.disableDotNotation;
 const indexed=input.map((item,index)=>({item,index}));
 const paired=(entries:typeof indexed)=>attachItemPairs(entries.map(entry=>entry.item),entries.map(entry=>[{item:entry.index}]));
 switch(node.type){
  case 'n8n-nodes-base.filter':return [paired(indexed.filter(({item})=>ctx.matches({...p.conditions,options:{...p.conditions?.options,...(options.ignoreCase!==undefined?{caseSensitive:!options.ignoreCase}:{}),...(p.looseTypeValidation||options.looseTypeValidation?{typeValidation:'loose'}:{})}},item)))];
  case 'n8n-nodes-base.switch':{
   const count=normalOutputCount(node),maximum=p.mode==='expression'?16:100;if(!Number.isInteger(count)||count<1||count>maximum)throw new Error(`나눌 경로 수는 1~${maximum}개로 설정해주세요.`);const output:(typeof indexed)[]=Array.from({length:count},()=>[]);
   if(p.mode==='expression'){for(const entry of indexed){const route=ctx.resolve(p.output,entry.item);if(typeof route!=='number'||!Number.isInteger(route)||route<0||route>=count)throw new Error('경로 번호는 0부터 경로 수보다 작은 정수여야 해요.');output[route].push(entry);}return output.map(paired);}
   if(p.mode&&p.mode!=='rules')throw new Error('경로 나누기는 조건 또는 경로 번호 방식을 선택해주세요.');
   const rules=p.rules?.values;if(!Array.isArray(rules)||!rules.length)throw new Error('경로별 조건을 하나 이상 입력해주세요.');
   const fallback=options.fallbackOutput??'none';if(fallback!=='none'&&fallback!=='extra'&&(!Number.isInteger(fallback)||fallback<0||fallback>=rules.length))throw new Error('기본 경로 번호를 확인해주세요.');
   for(const entry of indexed){let matched=false;for(let index=0;index<rules.length;index++){const spec={...rules[index].conditions,options:{...rules[index].conditions?.options,...(options.ignoreCase!==undefined?{caseSensitive:!options.ignoreCase}:{}),...(p.looseTypeValidation||options.looseTypeValidation?{typeValidation:'loose'}:{})}};if(ctx.matches(spec,entry.item)){output[index].push(entry);matched=true;if(!options.allMatchingOutputs)break;}}if(!matched&&fallback!=='none')output[fallback==='extra'?count-1:fallback].push(entry);}
   return output.map(paired);
  }
  case 'n8n-nodes-base.limit':{const count=ctx.resolve(p.maxItems??1,input[0]||{});if(typeof count!=='number'||!Number.isInteger(count)||count<0||count>5000)throw new Error('남길 항목 수는 0~5,000 사이의 정수여야 해요.');if(p.keep&&p.keep!=='firstItems'&&p.keep!=='lastItems')throw new Error('처음 또는 마지막 항목을 선택해주세요.');return [paired(p.keep==='lastItems'?indexed.slice(Math.max(0,indexed.length-count)):indexed.slice(0,count))];}
  case 'n8n-nodes-base.sort':{
   if(p.type==='random'){const result=[...indexed];for(let i=result.length-1;i>0;i--){const j=randomInt(i+1);[result[i],result[j]]=[result[j],result[i]];}return [paired(result)];}
   if(p.type==='code')return [await ctx.code(`const items=$input.all();items.sort((a,b)=>{${p.code||'return 0;'}\n});return items;`,input,'runOnceForAllItems')];
   if(p.type&&p.type!=='simple')throw new Error('정렬 방식을 확인해주세요.');const entries=p.sortFieldsUi?.sortField;if(!Array.isArray(entries)||!entries.length)throw new Error('정렬할 항목을 하나 이상 지정해주세요.');
   for(const field of entries){if(typeof field.fieldName!=='string'||!field.fieldName||!input.some(v=>get(v,field.fieldName,literal)!==undefined))throw new Error('정렬할 항목을 입력 데이터에서 찾을 수 없어요.');if(field.order&&field.order!=='ascending'&&field.order!=='descending')throw new Error('오름차순 또는 내림차순을 선택해주세요.');}
   const comparable=(value:any)=>typeof value==='string'?value.toLowerCase():value;
   return [paired([...indexed].sort((a,b)=>{for(const field of entries){const left=comparable(get(a.item,field.fieldName,literal)),right=comparable(get(b.item,field.fieldName,literal));if(stable(left)===stable(right))continue;const order=field.order==='descending'?-1:1;if(left==null)return order;if(right==null)return -order;return (left<right?-1:1)*order;}return 0;}))];
  }
  case 'n8n-nodes-base.splitOut':{
   if(options.includeBinary)throw new Error('목록 펼치기는 JSON 데이터를 지원해요. 바이너리 파일은 아직 지원하지 않아요.');const output:Json[]=[],pairs:ItemPair[][]=[];
   for(const {item,index:itemIndex} of indexed){const names=fields(ctx.resolve(p.fieldToSplitOut,item)).map(v=>v.replace(/^\$json\./,''));if(!names.length)throw new Error('펼칠 목록의 항목 이름을 입력해주세요.');if(names.includes('$binary'))throw new Error('바이너리 목록은 아직 지원하지 않아요.');const destinations=fields(options.destinationFieldName||'');if(destinations.length&&destinations.length!==names.length)throw new Error('펼칠 목록 수와 출력 항목 수를 같게 지정해주세요.');const include=p.include||'noOtherFields';if(!['noOtherFields','allOtherFields','selectedOtherFields'].includes(include))throw new Error('함께 남길 항목 범위를 선택해주세요.');const keep=include==='selectedOtherFields'?fields(p.fieldsToInclude||''):[];if(include==='selectedOtherFields'&&!keep.length)throw new Error('함께 남길 항목을 입력해주세요.');
    const lists=names.map(name=>{const value=get(item,name,literal);return value===undefined?[]:Array.isArray(value)?value:value!==null&&typeof value==='object'?Object.values(value):[value];});
    const length=Math.max(0,...lists.map(v=>v.length));if(output.length+length>5000)throw new Error('목록 펼치기 결과는 5,000개 항목까지 지원해요.');
    for(let index=0;index<length;index++){let row:Json=include==='allOtherFields'?clone(item):include==='selectedOtherFields'?select(item,keep,literal):{};if(include==='allOtherFields')for(const name of names)remove(row,name,literal);
     for(let field=0;field<names.length;field++){if(index>=lists[field].length)continue;const value=lists[field][index];if(names.length===1&&!destinations.length&&include==='noOtherFields'&&value!==null&&typeof value==='object'&&!Array.isArray(value))row={...row,...clone(value)};else put(row,destinations[field]||names[field],clone(value),literal);}
     output.push(row);pairs.push([{item:itemIndex}]);
    }
   }return [attachItemPairs(output,pairs)];
  }
  case 'n8n-nodes-base.removeDuplicates':{
   if(p.operation&&p.operation!=='removeDuplicateInputItems')throw new Error('중복 제거는 현재 입력 안의 중복 제거를 지원해요. 이전 실행 기록과의 비교는 아직 지원하지 않아요.');
   const compare=p.compare||'allFields';if(!['allFields','allFieldsExcept','selectedFields'].includes(compare))throw new Error('중복을 비교할 항목 범위를 선택해주세요.');const chosen=fields(compare==='selectedFields'?p.fieldsToCompare||'':p.fieldsToExclude||'');if(compare!=='allFields'&&!chosen.length)throw new Error('중복을 비교하거나 제외할 항목을 입력해주세요.');const seen=new Set<string>(),output:Json[]=[],pairs:ItemPair[][]=[];
   if(compare==='selectedFields')for(const field of chosen){let type:string|undefined;for(const item of input){const value=get(item,field,literal);if(value===undefined)throw new Error('중복을 비교할 항목이 없는 데이터가 있어요.');if(value===null)continue;if(type!==undefined&&type!==typeof value)throw new Error('중복을 비교할 항목의 종류가 서로 달라요.');type=typeof value;}}
   for(const {item,index} of indexed){const data=compare==='selectedFields'?select(item,chosen,literal):clone(item);if(compare==='allFieldsExcept')for(const name of chosen)remove(data,name,literal);const key=stable(data);if(seen.has(key))continue;seen.add(key);output.push(options.removeOtherFields?data:item);pairs.push([{item:index}]);}return [attachItemPairs(output,pairs)];
  }
  case 'n8n-nodes-base.aggregate':{
   if(options.includeBinaries)throw new Error('집계는 JSON 데이터를 지원해요. 바이너리 파일은 아직 지원하지 않아요.');
   if(p.aggregate==='aggregateAllItemData'){const include=p.include||'allFields';if(!['allFields','specifiedFields','allFieldsExcept'].includes(include))throw new Error('집계할 항목 범위를 선택해주세요.');const names=fields(include==='specifiedFields'?p.fieldsToInclude||'':p.fieldsToExclude||'');if(include!=='allFields'&&!names.length)throw new Error('집계에 포함하거나 제외할 항목을 입력해주세요.');const items=indexed.map(({item,index})=>{const row=include==='specifiedFields'?select(item,names,true):clone(item);if(include==='allFieldsExcept')for(const name of names)remove(row,name,true);return {row,index};}).filter(({row})=>include==='allFields'||Object.keys(row).length);const row:Json={};put(row,p.destinationFieldName||'data',items.map(item=>item.row),true);return [attachItemPairs([row],[items.map(item=>({item:item.index}))])];}
   if(p.aggregate&&p.aggregate!=='aggregateIndividualFields')throw new Error('집계 방식을 확인해주세요.');const entries=p.fieldsToAggregate?.fieldToAggregate;if(!Array.isArray(entries)||!entries.length)throw new Error('집계할 항목을 하나 이상 입력해주세요.');const row:Json={},outputs=new Set<string>();
   for(const field of entries){if(!field.fieldToAggregate)throw new Error('집계할 항목 이름을 입력해주세요.');const name=field.renameField?field.outputFieldName:field.outputFieldName||path(field.fieldToAggregate,literal).at(-1);if(!name)throw new Error('집계 출력 항목 이름을 입력해주세요.');if(outputs.has(name))throw new Error('집계 출력 항목 이름은 서로 달라야 해요.');outputs.add(name);const list:any[]=[];
    for(const item of input){let value=get(item,field.fieldToAggregate,literal);if(value==null){if(options.keepMissing)list.push(null);continue;}if(Array.isArray(value)&&!options.keepMissing)value=value.filter(v=>v!==null);if(Array.isArray(value)&&options.mergeLists)list.push(...clone(value));else list.push(clone(value));if(list.length>5000)throw new Error('집계 목록은 5,000개 항목까지 지원해요.');}put(row,name,list,literal);
   }return [attachItemPairs([row],[indexed.map(entry=>({item:entry.index}))])];
  }
  case 'n8n-nodes-base.stopAndError':{if(p.errorType==='errorObject'){const raw=ctx.resolve(p.errorObject,input[0]||{});const data=typeof raw==='string'?JSON.parse(raw):raw;throw new Error(String(data?.message||data?.description||'자동화가 오류로 중단됐어요.'));}if(p.errorType&&p.errorType!=='errorMessage')throw new Error('중단 오류 형식을 확인해주세요.');throw new Error(String(ctx.resolve(p.errorMessage||'자동화를 중단했어요.',input[0]||{})));}
  default:throw new Error('지원하지 않는 데이터 처리 단계예요.');
 }
}
