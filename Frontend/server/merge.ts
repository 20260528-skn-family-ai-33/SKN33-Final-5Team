import type {FlowNode,Json} from '../lib/workflow';
import {BINARY_KEY,MAX_BINARY_BYTES,assertBinaryItems,splitBinaryItem,type BinaryMap} from './binary';
import {attachItemPairs,type ItemPair} from './provenance';

export const MERGE_NODE_TYPE='n8n-nodes-base.merge';
export const MAX_MERGE_ITEMS=5000;
export const MAX_MERGE_JSON_BYTES=4_000_000;
const unsafe=new Set(['__proto__','prototype','constructor',BINARY_KEY]);
type Clash={resolveClash:'preferInput1'|'preferLast'|'addSuffix';mergeMode:'deepMerge'|'shallowMerge';overrideEmpty:boolean};
type Entry={json:Json;binary:BinaryMap;source:ItemPair};
type Pair={field1:string;field2:string};
const empty=(value:any)=>value===undefined||value===null||value==='';
const object=(value:any):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value);

function copy(value:any,depth=0):any {
 if(depth>100)throw new Error('합칠 데이터의 중첩 깊이는 100단계 이하로 줄여주세요.');
 if(value===null||['string','boolean'].includes(typeof value))return value;
 if(typeof value==='number'&&Number.isFinite(value))return value;
 if(Array.isArray(value))return value.map(entry=>copy(entry,depth+1));
 if(object(value)&&(Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null))return Object.fromEntries(Object.entries(value).map(([key,entry])=>{if(unsafe.has(key))throw new Error('합칠 데이터에 사용할 수 없는 항목 이름이 있어요.');return [key,copy(entry,depth+1)];}));
 throw new Error('흐름 합치기에는 JSON으로 표현할 수 있는 데이터만 입력해주세요.');
}
function boolean(value:any,fallback=false):boolean {if(value===undefined)return fallback;if(value===true||value==='true')return true;if(value===false||value==='false')return false;throw new Error('흐름 합치기 옵션은 참 또는 거짓으로 선택해주세요.');}
function option(parameters:Json,path:string,fallback?:any):any {
 if(Object.hasOwn(parameters,path))return parameters[path];let value:any=parameters;for(const key of path.split('.')){if(!object(value)||!Object.hasOwn(value,key))return fallback;value=value[key];}return value??fallback;
}
function canonical(value:any):string {
 if(value===undefined)return 'undefined';if(value===null)return 'null';if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(object(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';return typeof value+':'+JSON.stringify(value);
}
function fuzzyEqual(left:any,right:any):boolean {
 if(left!==null&&right!==null&&typeof left===typeof right)return canonical(left)===canonical(right);
 const falsy=(value:any)=>value===null||value===''||Array.isArray(value)&&!value.length;
 if(falsy(left)&&falsy(right)||falsy(left)&&right===undefined||left===undefined&&falsy(right))return true;
 if(typeof left==='number'&&typeof right==='string')return String(left)===right;
 if(typeof left==='string'&&typeof right==='number')return left===String(right);
 if(left!==null&&typeof left==='object'&&typeof right==='string'||right!==null&&typeof right==='object'&&typeof left==='string'){
  try{const parsed=JSON.parse(typeof left==='string'?left:right),original=typeof left==='string'?right:left;return canonical(parsed)===canonical(original);}catch{return false;}
 }
 if(typeof left==='boolean'&&typeof right==='string')return right.toLowerCase()===String(left)||right===(left?'1':'0');
 if(typeof right==='boolean'&&typeof left==='string')return left.toLowerCase()===String(right)||left===(right?'1':'0');
 if(typeof left==='boolean'&&typeof right==='number')return right===(left?1:0);
 if(typeof right==='boolean'&&typeof left==='number')return left===(right?1:0);
 return canonical(left)===canonical(right);
}
// Candidate buckets include all officially documented fuzzy relations. Each
// candidate is subsequently compared exactly: fuzzy equality isn't transitive.
function variants(value:any,fuzzy:boolean):string[] {
 const keys=['strict:'+canonical(value)];if(!fuzzy)return keys;
 if(value===undefined||value===null||value===''||Array.isArray(value)&&!value.length)keys.push('fuzzy:empty');
 if(typeof value==='number')keys.push('numeric:'+String(value));
 if(typeof value==='string'){
  const number=Number(value);if(Number.isFinite(number)&&String(number)===value)keys.push('numeric:'+value);
  try{const parsed=JSON.parse(value);if(parsed!==null&&typeof parsed==='object')keys.push('object:'+canonical(parsed));}catch{}
 }
 if(value!==null&&typeof value==='object')keys.push('object:'+canonical(value));
 if(value===true||value===1||value==='1'||typeof value==='string'&&value.toLowerCase()==='true')keys.push('boolean:true');
 if(value===false||value===0||value==='0'||typeof value==='string'&&value.toLowerCase()==='false')keys.push('boolean:false');
 return keys;
}
function pathParts(path:any,disableDotNotation:boolean):string[] {
 if(typeof path!=='string'||!path.trim()||path.length>300||/[\x00-\x1f]/.test(path))throw new Error('일치시킬 항목 이름을 입력해주세요.');
 let parts:string[];
 if(disableDotNotation)parts=[path];
 else {if(!/^[^.[\]]+(?:(?:\.[^.[\]]+)|(?:\[\d+\]))*$/.test(path))throw new Error('중첩 항목은 customer.id 또는 rows[0].id처럼 입력해주세요.');parts=path.replace(/\[(\d+)\]/g,'.$1').split('.');}
 if(parts.some(part=>!part||unsafe.has(part)))throw new Error('일치시킬 항목 이름을 확인해주세요.');return parts;
}
function read(item:Json,path:string,disableDotNotation:boolean):any {let value:any=item;for(const part of pathParts(path,disableDotNotation)){if(value===null||typeof value!=='object'||!Object.hasOwn(value,part))return undefined;value=value[part];}return value;}
function fields(parameters:Json,disableDotNotation:boolean):Pair[] {
 const advanced=boolean(parameters.advanced),pairs=advanced?parameters.mergeByFields?.values:typeof parameters.fieldsToMatchString==='string'?parameters.fieldsToMatchString.split(',').map(field=>({field1:field.trim(),field2:field.trim()})):[];
 if(!Array.isArray(pairs)||!pairs.length||pairs.length>50)throw new Error('일치시킬 항목을 1~50개 입력해주세요.');const targets=new Set<string>();
 return pairs.map(pair=>{if(!object(pair))throw new Error('일치시킬 두 항목 이름을 입력해주세요.');pathParts(pair.field1,disableDotNotation);pathParts(pair.field2,disableDotNotation);if(targets.has(pair.field2))throw new Error('입력 2의 일치 항목 이름이 중복돼요. 비교 기준을 하나로 정리해주세요.');targets.add(pair.field2);return {field1:pair.field1,field2:pair.field2};});
}
function clashes(parameters:Json,joinMode?:string):Clash {
 const resolveClash=option(parameters,'options.clashHandling.values.resolveClash',joinMode==='enrichInput2'?'preferInput1':'preferLast'),mergeMode=option(parameters,'options.clashHandling.values.mergeMode','deepMerge'),overrideEmpty=boolean(option(parameters,'options.clashHandling.values.overrideEmpty'));
 if(!['preferInput1','preferLast','addSuffix'].includes(resolveClash)||!['deepMerge','shallowMerge'].includes(mergeMode))throw new Error('항목 충돌 처리와 중첩 데이터 합치기 방식을 확인해주세요.');return {resolveClash,mergeMode,overrideEmpty};
}
function deepMerge(target:any,source:any,clash:Clash,depth=0):any {
 if(depth>100)throw new Error('합칠 데이터의 중첩 깊이는 100단계 이하로 줄여주세요.');if(clash.overrideEmpty&&empty(source)&&target!==undefined)return copy(target);
 if(Array.isArray(source)){
  const result=Array.isArray(target)?copy(target):[];source.forEach((value,index)=>{result[index]=deepMerge(result[index],value,clash,depth+1);});return result;
 }
 if(object(source)){
  // Preserve JSON data instead of dropping named properties on an array.
  if(Array.isArray(target))throw new Error('같은 항목에 배열과 객체가 섞여 있어요. 얕게 합치기를 선택하거나 데이터 종류를 먼저 맞춰주세요.');
  const result=object(target)?copy(target):{};for(const [key,value] of Object.entries(source))result[key]=deepMerge(result[key],value,clash,depth+1);return result;
 }
 return source===undefined?(target===undefined?undefined:copy(target)):copy(source);
}
function mergeEntries(entries:(Entry|undefined)[],clash:Clash):Json {
 const available=entries.map((entry,index)=>({entry,index})).filter(value=>value.entry!==undefined) as {entry:Entry;index:number}[];
 if(clash.resolveClash==='preferInput1'&&available.length>1){const first=available.shift()!;available.push(first);}
 let json:Json={},binary:BinaryMap={};
 for(const {entry,index} of available){
  const source=clash.resolveClash==='addSuffix'?Object.fromEntries(Object.entries(entry.json).map(([key,value])=>[key+'_'+(index+1),value])):entry.json;
  if(clash.mergeMode==='deepMerge')json=deepMerge(json,source,clash);
  else for(const [key,value] of Object.entries(source)){if(clash.overrideEmpty&&empty(value)&&Object.hasOwn(json,key))continue;json[key]=copy(value);}
  // File bytes and their metadata are one atomic value; never mix two files.
  binary={...binary,...entry.binary};
 }
 return {...json,...(Object.keys(binary).length?{[BINARY_KEY]:binary}:{})};
}
const asItem=(entry:Entry):Json=>({...copy(entry.json),...(Object.keys(entry.binary).length?{[BINARY_KEY]:{...entry.binary}}:{})});
const suffixed=(entry:Entry,index:number):Json=>({...Object.fromEntries(Object.entries(entry.json).map(([key,value])=>[key+'_'+(index+1),copy(value)])),...(Object.keys(entry.binary).length?{[BINARY_KEY]:{...entry.binary}}:{})});

/** Pure, independent Merge implementation: no n8n runtime, SQL, I/O or eval. */
export async function executeMerge(node:FlowNode,inputsByPort:Json[][]):Promise<Json[][]> {
 if(node.type!==MERGE_NODE_TYPE||!object(node.parameters))throw new Error('올바른 흐름 합치기 단계를 선택해주세요.');const p=node.parameters,mode=p.mode??'append';
 if(mode==='combineBySql')throw new Error('SQL로 흐름 합치기는 아직 지원하지 않아요. 항목 값·순서·모든 조합으로 합치기를 선택해주세요.');
 if(!['append','combine','chooseBranch'].includes(mode))throw new Error('지원하지 않는 흐름 합치기 방식이에요.');
 const configuredCount=Number(p.numberInputs??2);if(!Number.isSafeInteger(configuredCount)||configuredCount<2||configuredCount>10)throw new Error('합칠 입력 수는 2~10개로 선택해주세요.');
 // numberInputs is hidden for these two modes; ignore a retained UI value,
 // while still rejecting any actual third input rather than losing its data.
 const count=mode==='combine'&&['combineByFields','combineAll'].includes(p.combineBy??'combineByFields')?2:configuredCount;
 if(!Array.isArray(inputsByPort)||inputsByPort.length>count||inputsByPort.some(group=>!Array.isArray(group)))throw new Error('입력 포트별 데이터 목록을 전달해주세요.');
 let inputJsonBytes=2,inputBinaryBytes=0;const groups:Entry[][]=Array.from({length:count},(_,index)=>{
  const items=inputsByPort[index]??[];assertBinaryItems(items);inputJsonBytes+=(index?1:0)+2;return items.map((item,itemIndex)=>{const {json,binary}=splitBinaryItem(item),safe=copy(json);inputJsonBytes+=Buffer.byteLength(JSON.stringify(safe),'utf8')+(itemIndex?1:0);if(inputJsonBytes>MAX_MERGE_JSON_BYTES)throw new Error('합칠 입력 JSON 총 크기는 4MB 이하로 줄여주세요.');for(const file of Object.values(binary)){inputBinaryBytes+=file.fileSize;if(inputBinaryBytes>MAX_BINARY_BYTES)throw new Error('합칠 입력 파일 총 크기는 8MB 이하로 줄여주세요.');}return {json:safe,binary,source:{item:itemIndex,input:index}};});
 });
 const output:Json[]=[],outputPairs:ItemPair[][]=[];let outputJsonBytes=4,outputBinaryBytes=0;
 const done=()=>[attachItemPairs(output,outputPairs)];
 const add=(item:Json,sources:(Entry|undefined)[]=[])=>{if(output.length>=MAX_MERGE_ITEMS)throw new Error('흐름 합치기 결과는 5,000개 항목까지 만들 수 있어요. 비교 기준이나 입력 수를 줄여주세요.');const {json,binary}=splitBinaryItem(item);outputJsonBytes+=Buffer.byteLength(JSON.stringify(json),'utf8')+(output.length?1:0);if(outputJsonBytes>MAX_MERGE_JSON_BYTES)throw new Error('흐름 합치기 JSON 결과는 4MB 이하로 줄여주세요.');for(const file of Object.values(binary)){outputBinaryBytes+=file.fileSize;if(outputBinaryBytes>MAX_BINARY_BYTES)throw new Error('흐름 합치기 결과 파일 총 크기는 8MB 이하로 줄여주세요.');}output.push(item);outputPairs.push(sources.filter((entry):entry is Entry=>entry!==undefined).map(entry=>entry.source));};
 if(mode==='append'){for(const group of groups)for(const entry of group)add(asItem(entry),[entry]);return done();}
 if(mode==='chooseBranch'){
  if((p.chooseBranchMode??'waitForAll')!=='waitForAll')throw new Error('입력 선택은 모든 입력을 기다린 뒤 진행하는 방식을 지원해요.');
  // An empty item still depends on the inputs whose completion it awaited.
  const selection=p.output??'specifiedInput';if(selection==='empty')add({},groups.flat());else if(selection==='specifiedInput'){const chosen=Number(p.useDataOfInput??1);if(!Number.isSafeInteger(chosen)||chosen<1||chosen>count)throw new Error('사용할 입력 번호를 확인해주세요.');for(const entry of groups[chosen-1])add(asItem(entry),[entry]);}else throw new Error('선택한 입력 또는 빈 항목 출력을 선택해주세요.');return done();
 }
 const combine=p.combineBy??'combineByFields';if(!['combineByPosition','combineByFields','combineAll'].includes(combine))throw new Error('항목 값·순서·모든 조합 중 합치는 기준을 선택해주세요.');
 if(combine==='combineByPosition'){const clash=clashes(p),unpaired=boolean(option(p,'options.includeUnpaired'));const length=unpaired?Math.max(...groups.map(group=>group.length)):Math.min(...groups.map(group=>group.length));for(let index=0;index<length;index++){const entries=groups.map(group=>group[index]);add(mergeEntries(entries,clash),entries);}return done();}
 const [left,right]=groups;
 if(combine==='combineAll'){
  const clash=clashes(p);if(left.length*right.length>MAX_MERGE_ITEMS)throw new Error('모든 조합 결과가 5,000개를 넘어요. 입력 항목을 줄여주세요.');
  if(clash.resolveClash==='preferInput1'){for(const b of right)for(const a of left)add(mergeEntries([a,b],clash),[a,b]);}else for(const a of left)for(const b of right)add(mergeEntries([a,b],clash),[a,b]);return done();
 }
 const disableDot=boolean(option(p,'options.disableDotNotation')),fuzzy=boolean(option(p,'options.fuzzyCompare')),pairs=fields(p,disableDot),joinMode=p.joinMode??'keepMatches',outputDataFrom=p.outputDataFrom??'both',multiple=option(p,'options.multipleMatches','all'),clash=clashes(p,joinMode);
 if(!['keepMatches','keepNonMatches','keepEverything','enrichInput1','enrichInput2'].includes(joinMode)||!['both','input1','input2'].includes(outputDataFrom)||!['all','first'].includes(multiple))throw new Error('일치 결과 선택과 여러 일치 결과 처리 방식을 확인해주세요.');
 if(!left.length||!right.length){
  if(joinMode==='keepMatches'||joinMode==='enrichInput1'&&!left.length||joinMode==='enrichInput2'&&!right.length||joinMode==='keepNonMatches'&&outputDataFrom==='input1'&&!left.length||joinMode==='keepNonMatches'&&outputDataFrom==='input2'&&!right.length)return done();
  for(const entry of [...left,...right])add(asItem(entry),[entry]);return done();
 }
 const rightValues=right.map(entry=>pairs.map(pair=>read(entry.json,pair.field2,disableDot))),indexes=pairs.map(()=>new Map<string,number[]>()),exactIndex=new Map<string,number[]>();
 rightValues.forEach((values,index)=>{const key=canonical(values),bucket=exactIndex.get(key);if(bucket)bucket.push(index);else exactIndex.set(key,[index]);});
 rightValues.forEach((values,index)=>values.forEach((value,field)=>{for(const key of variants(value,fuzzy)){const bucket=indexes[field].get(key);if(bucket)bucket.push(index);else indexes[field].set(key,[index]);}}));
 const matchedRight=new Set<number>(),unmatchedLeft:Entry[]=[];let comparisons=0;
 for(let index=0;index<left.length;index++){
  const a=left[index],values=pairs.map(pair=>read(a.json,pair.field1,disableDot));if(values.some(value=>value===undefined)){unmatchedLeft.push(a);continue;}
  const buckets=values.map((value,field)=>variants(value,fuzzy).map(key=>indexes[field].get(key)||[]));let chosen=buckets[0];for(const bucket of buckets)if(bucket.reduce((sum,items)=>sum+items.length,0)<chosen.reduce((sum,items)=>sum+items.length,0))chosen=bucket;
  const candidates=fuzzy?[...new Set(chosen.flat())].sort((a,b)=>a-b):exactIndex.get(canonical(values))||[],matches:number[]=[];
  for(const match of candidates){if(fuzzy&&++comparisons>1_000_000)throw new Error('일치 비교가 너무 많아요. 비교할 항목을 추가하거나 데이터 종류를 맞춰서 정확히 비교해주세요.');if(!fuzzy||values.every((value,field)=>fuzzyEqual(value,rightValues[match][field]))){matches.push(match);if(multiple==='first')break;}}
  if(!matches.length){unmatchedLeft.push(a);continue;}for(const match of matches)matchedRight.add(match);
  if(joinMode==='keepNonMatches')continue;
  if((joinMode==='keepMatches'||joinMode==='keepEverything')&&outputDataFrom==='input1')add(asItem(a),[a]);
  else if((joinMode==='keepMatches'||joinMode==='keepEverything')&&outputDataFrom==='input2')continue;
  else for(const match of matches)add(mergeEntries([a,right[match]],clash),[a,right[match]]);
 }
 const unmatchedRight=right.filter((_,index)=>!matchedRight.has(index));
 // Output follows Input 2 order, independent of lookup order.
 if((joinMode==='keepMatches'||joinMode==='keepEverything')&&outputDataFrom==='input2')right.forEach((entry,index)=>{if(matchedRight.has(index))add(asItem(entry),[entry]);});
 if(joinMode==='keepNonMatches'){
  if(outputDataFrom!=='input2')for(const entry of unmatchedLeft)add(outputDataFrom==='both'?{...asItem(entry),_source:'input1'}:asItem(entry),[entry]);
  if(outputDataFrom!=='input1')for(const entry of unmatchedRight)add(outputDataFrom==='both'?{...asItem(entry),_source:'input2'}:asItem(entry),[entry]);
 } else {
  if(joinMode==='keepEverything'||joinMode==='enrichInput1')for(const entry of unmatchedLeft)add(clash.resolveClash==='addSuffix'?suffixed(entry,0):asItem(entry),[entry]);
  if(joinMode==='keepEverything'||joinMode==='enrichInput2')for(const entry of unmatchedRight)add(clash.resolveClash==='addSuffix'?suffixed(entry,1):asItem(entry),[entry]);
 }
 return done();
}
