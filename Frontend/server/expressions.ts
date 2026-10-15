import type {Json} from '../lib/workflow';

/** Data only crosses this boundary. Callbacks are selected by the interpreter, never exposed to expressions. */
export type ExpressionContext={
 input?:Json[];inputByPort?:Json[][];itemIndex?:number;runIndex?:number;params?:Json;
 execution?:{id:string;mode:string};
 node?:(name:string,outputIndex?:number,runIndex?:number)=>{items:Json[];isExecuted:boolean};
 linkedItem?:(name:string,itemIndex?:number)=>Json;
};
const unsafe=new Set(['__proto__','prototype','constructor','caller','callee','arguments']);
const maxOutput=4_000_000,maxList=5000;
const fail=()=>new Error('표현식은 데이터 참조·계산·비교와 지원되는 문자열 함수를 사용해주세요. 임의 코드는 맞춤 계산 단계에서 처리해주세요.');
const linking=()=>new Error('어떤 이전 항목과 연결되는지 확인할 수 없어요. .item 대신 .first(), .last(), .all()[번호]로 항목을 명시해주세요.');
type Expr={kind:'literal';value:any}|{kind:'root';name:string}|{kind:'group';value:Expr}|{kind:'get';target:Expr;key:Expr;optional?:boolean}|{kind:'call';target:Expr;args:Expr[];optional?:boolean}|{kind:'array';values:Expr[]}|{kind:'object';values:[string,Expr][]}|{kind:'unary';op:string;right:Expr}|{kind:'binary';op:string;left:Expr;right:Expr}|{kind:'choose';test:Expr;yes:Expr;no:Expr};
type Token={value:string;kind:'string'|'number'|'identifier'|'operator'};
const precedence:Record<string,number>={'??':1,'||':2,'&&':3,'==':4,'!=':4,'===':4,'!==':4,'<':5,'>':5,'<=':5,'>=':5,'+':6,'-':6,'*':7,'/':7,'%':7};
const roots=new Set(['$json','$binary','$input','$node','$','$itemIndex','$runIndex','$execution','$parameter','String','Number','Boolean','Math','JSON']);
function stringToken(value:string):string {
 if(value.startsWith('"'))return JSON.parse(value);
 return value.slice(1,-1).replace(/\\(u[0-9a-fA-F]{4}|[\s\S])/g,(_,char:string)=>char.startsWith('u')?String.fromCharCode(parseInt(char.slice(1),16)):({n:'\n',r:'\r',t:'\t',b:'\b',f:'\f',v:'\v','0':'\0'} as Record<string,string>)[char]??char);
}
function parse(source:string):Expr {
 if(source.length>8000)throw fail();const tokens:Token[]=[];let offset=0;
 const pattern=/\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([\p{L}_$][\p{L}\p{N}_$]*)|(===|!==|==|!=|>=|<=|\|\||&&|\?\?|\?\.|[()[\]{}.,!+*/%?:<>-]))/uy;
 while(offset<source.length){if(!source.slice(offset).trim())break;pattern.lastIndex=offset;const match=pattern.exec(source);if(!match)throw fail();offset=pattern.lastIndex;tokens.push({value:match[1]??match[2]??match[3]??match[4],kind:match[1]?'number':match[2]?'string':match[3]?'identifier':'operator'});if(tokens.length>1024)throw fail();}
 let index=0,depth=0;const peek=()=>tokens[index]?.value,consume=(value:string)=>{if(peek()!==value)throw fail();index++;};
 const expression=(minimum=0):Expr=>{
  if(++depth>64)throw fail();let result:Expr;const token=tokens[index++];if(!token)throw fail();
  if(token.value==='('){result={kind:'group',value:expression()};consume(')');}
  else if(['!','-','+'].includes(token.value))result={kind:'unary',op:token.value,right:expression(8)};
  else if(token.value==='['){const values:Expr[]=[];while(peek()!==']'){values.push(expression());if(peek()!==',')break;index++;if(peek()===']')break;}consume(']');result={kind:'array',values};}
  else if(token.value==='{'){const values:[string,Expr][]=[];while(peek()!=='}'){const key=tokens[index++];if(!key||!['identifier','string','number'].includes(key.kind))throw fail();const name=key.kind==='string'?stringToken(key.value):key.value;if(unsafe.has(name))throw fail();consume(':');values.push([name,expression()]);if(peek()!==',')break;index++;if(peek()==='}')break;}consume('}');result={kind:'object',values};}
  else if(token.kind==='number')result={kind:'literal',value:Number(token.value)};
  else if(token.kind==='string'){let value:string;try{value=stringToken(token.value);}catch{throw fail();}result={kind:'literal',value};}
  else if(['true','false','null','undefined'].includes(token.value))result={kind:'literal',value:token.value==='true'?true:token.value==='false'?false:token.value==='null'?null:undefined};
  else if(roots.has(token.value))result={kind:'root',name:token.value};else throw fail();
  while(['.','?.','[','('].includes(peek())){
   const op=tokens[index++].value;
   if(op==='('){const args:Expr[]=[];while(peek()!==')'){args.push(expression());if(peek()!==',')break;index++;}consume(')');result={kind:'call',target:result,args};continue;}
   let key:Expr;if(op==='['){key=expression();consume(']');if(key.kind==='literal'&&unsafe.has(String(key.value)))throw fail();}
   else if(op==='?.'&&peek()==='['){index++;key=expression();consume(']');if(key.kind==='literal'&&unsafe.has(String(key.value)))throw fail();}
   else{const property=tokens[index++];if(!property||property.kind!=='identifier'||unsafe.has(property.value))throw fail();key={kind:'literal',value:property.value};}
   result={kind:'get',target:result,key,optional:op==='?.'};
  }
  while(peek()&&precedence[peek()]!==undefined&&precedence[peek()]>=minimum){const op=tokens[index++].value;result={kind:'binary',op,left:result,right:expression(precedence[op]+1)};}
  if(minimum===0&&peek()==='?'){index++;const yes=expression();consume(':');result={kind:'choose',test:result,yes,no:expression()};}
  depth--;return result;
 };
 const tree=expression();if(index!==tokens.length)throw fail();
 const check=(expr:Expr,depth=0):void=>{if(depth>64)throw fail();const children=expr.kind==='get'?[expr.target,expr.key]:expr.kind==='call'?[expr.target,...expr.args]:expr.kind==='array'?expr.values:expr.kind==='object'?expr.values.map(([,v])=>v):expr.kind==='group'?[expr.value]:expr.kind==='unary'?[expr.right]:expr.kind==='binary'?[expr.left,expr.right]:expr.kind==='choose'?[expr.test,expr.yes,expr.no]:[];for(const child of children)check(child,depth+1);};check(tree);return tree;
}
const tag=Symbol('expression-reference');
const fileMetadata=Symbol('binary-metadata');
type Reference={ [tag]:'input'|'nodes'|'node'|'math'|'json';name?:string;legacy?:boolean };
const ref=(kind:Reference[typeof tag],extra:Partial<Reference>={}):Reference=>({[tag]:kind,...extra});
const isRef=(value:any):value is Reference=>value!=null&&typeof value==='object'&&Object.hasOwn(value,tag);
function clean(value:any):any {
 let nodes=0,size=0;const visit=(v:any,depth:number):any=>{
  if(++nodes>50000||depth>64)throw new Error('표현식 결과가 너무 복잡해요. 필요한 항목만 선택해주세요.');
  if(v==null||typeof v==='boolean')return v;
  if(typeof v==='number'){if(!Number.isFinite(v))throw new Error('표현식 계산 결과는 유한한 숫자여야 해요.');return v;}
  if(typeof v==='string'){size+=v.length;if(size>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');return v;}
  if(isRef(v)||typeof v!=='object')throw fail();
  if(Array.isArray(v)){if(v.length>maxList)throw new Error('표현식 목록은 5,000개 항목까지 사용할 수 있어요.');return Array.from({length:v.length},(_,index)=>{const d=Object.getOwnPropertyDescriptor(v,String(index));if(!d)return undefined;if(!('value'in d))throw fail();return visit(d.value,depth+1);});}
  const out:Json={};for(const key of Object.keys(v)){if(unsafe.has(key))throw fail();size+=key.length;if(size>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');const d=Object.getOwnPropertyDescriptor(v,key);if(!d||!('value'in d))throw fail();out[key]=visit(d.value,depth+1);}return out;
 };const result=visit(value,0),serialized=JSON.stringify(result);if(serialized&&Buffer.byteLength(serialized,'utf8')>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');return result;
}
function own(target:object,key:string):any {const descriptor=Object.getOwnPropertyDescriptor(target,key);if(!descriptor)return undefined;if(!('value'in descriptor))throw fail();return descriptor.value;}
function wrap(item:Json):Json {
 const json:Json={};for(const key of Object.keys(item)){if(key==='__easyflowBinary')continue;if(unsafe.has(key))throw fail();json[key]=own(item,key);}
 const files=own(item,'__easyflowBinary'),binary:Json={};if(files!==undefined){if(!files||typeof files!=='object'||Array.isArray(files))throw fail();for(const name of Object.keys(files)){if(unsafe.has(name))throw fail();const file=own(files,name);if(!file||typeof file!=='object'||Array.isArray(file))throw fail();const meta:Json={};for(const field of ['fileName','mimeType','fileExtension','fileSize']){const v=own(file,field);if(v!==undefined)meta[field]=v;}Object.defineProperty(meta,fileMetadata,{value:true});binary[name]=meta;}}
 return {json,...(Object.keys(binary).length?{binary}:{})};
}
/** Interpreter-only method dispatch. No function value can be obtained from a data object. */
function evaluate(tree:Expr,item:Json,context:ExpressionContext):any {
 let steps=0,calls=0;const input=context.input||[item],itemIndex=context.itemIndex??input.indexOf(item),currentIndex=itemIndex<0?0:itemIndex;
 const short=Symbol('optional-chain'),value=(v:any)=>v===short?undefined:v;
 const index=(value:any,fallback?:number)=>{if(value===undefined)return fallback;if(typeof value!=='number'||!Number.isSafeInteger(value)||value< -1||value>1000000)throw fail();return value;};
 const node=(name:any,branch?:any,run?:any)=>{if(typeof name!=='string'||!name||name.length>200)throw fail();if(!context.node)throw new Error('이전 단계 결과는 자동화 실행 안에서 참조할 수 있어요.');return context.node(name,index(branch),index(run));};
 const read=(target:any,key:any):any=>{
  if(!['string','number'].includes(typeof key)||unsafe.has(String(key)))throw fail();key=String(key);
  if(isRef(target)){
   if(target[tag]==='nodes')return ref('node',{name:key,legacy:true});
   if(target[tag]==='node'){
    if(key==='isExecuted')return node(target.name).isExecuted;
    if(target.legacy&&(key==='json'||key==='binary')){const result=node(target.name);if(!result.isExecuted)throw new Error(`${target.name}: 현재 실행 범위에서 아직 실행되지 않은 단계예요.`);if(!result.items[currentIndex])throw new Error('이전 단계에 현재 항목 번호의 결과가 없어요. .first() 또는 .all()[번호]로 항목을 명시해주세요.');return wrap(result.items[currentIndex])[key]||(key==='binary'?{}:undefined);}
    if(key==='item'){if(context.linkedItem)return wrap(context.linkedItem(target.name!,currentIndex));throw linking();}
    throw fail();
   }
   if(target[tag]==='input'){if(!input.length)throw new Error('현재 단계에 입력 항목이 없어요.');if(key==='item'){if(!input[currentIndex])throw new Error('현재 입력 항목 번호를 찾을 수 없어요.');return wrap(input[currentIndex]);}if(key==='params')return context.params||{};throw fail();}
   throw fail();
  }
  if(target==null)throw new Error('없는 값의 항목을 읽었어요. ?. 또는 ??로 빈 값의 처리 방법을 설정해주세요.');
  if(typeof target==='object'&&Object.hasOwn(target,fileMetadata)&&key==='data')throw new Error('일반 표현식에서는 파일 이름·종류·크기를 읽을 수 있어요. 파일 원본은 파일 추출 또는 맞춤 계산 단계에서 처리해주세요.');
  if(key==='length'&&(typeof target==='string'||Array.isArray(target)))return target.length;
  if(typeof target==='string'&&/^\d+$/.test(key))return target[Number(key)];
  if(typeof target!=='object')return undefined;
  const d=Object.getOwnPropertyDescriptor(target,key);if(!d)return undefined;if(!('value'in d)||typeof d.value==='function')throw fail();return d.value;
 };
 const method=(target:any,name:any,args:any[]):any=>{
  if(typeof name!=='string'||unsafe.has(name)||++calls>100||args.length>50)throw fail();
  if(isRef(target)){
   if(target[tag]==='input'||target[tag]==='node'){
    if(target[tag]==='node'&&name==='itemMatching'){if(args.length!==1||!context.linkedItem)throw linking();const item=index(args[0]);if(item===undefined||item<0)throw fail();return wrap(context.linkedItem(target.name!,item));}
    if(!['all','first','last'].includes(name)||args.length>2)throw fail();
    let items:Json[];
    if(target[tag]==='input'){if(name!=='all'&&args.length)throw new Error('$input.first()와 $input.last()에는 인수를 넣지 마세요.');items=input;}
    else {const result=node(target.name,args[0],args[1]);if(!result.isExecuted)throw new Error(`${target.name}: 현재 실행 범위에서 아직 실행되지 않은 단계예요.`);items=result.items;}
    if(name==='all')return items.map(wrap);if(!items.length)throw new Error('참조한 단계에 출력 항목이 없어요. .all()로 빈 목록을 처리해주세요.');return wrap(name==='first'?items[0]:items.at(-1)!);
   }
   if(target[tag]==='math'){
    if(!['round','floor','ceil','trunc','abs','min','max','pow','sqrt'].includes(name)||!args.length||args.some(x=>typeof x!=='number'||!Number.isFinite(x)))throw fail();return (Math[name as keyof Math] as (...x:number[])=>number)(...args);
   }
   if(target[tag]==='json'){if(args.length!==1)throw fail();if(name==='stringify')return JSON.stringify(clean(args[0]));if(name==='parse'&&typeof args[0]==='string'&&args[0].length<=maxOutput)return clean(JSON.parse(args[0]));throw fail();}
   throw fail();
  }
  if(typeof target==='string'){
   if(args.some(x=>typeof x==='object'||typeof x==='function'||typeof x==='number'&&!Number.isFinite(x))||!['trim','trimStart','trimEnd','toLowerCase','toUpperCase','includes','startsWith','endsWith','slice','substring','split','indexOf','replace','replaceAll','concat','padStart','padEnd','repeat','at'].includes(name))throw fail();
   if(name==='repeat'){if(args.length!==1||!Number.isSafeInteger(args[0])||args[0]<0||target.length*args[0]>maxOutput)throw fail();}
   if(['padStart','padEnd'].includes(name)&&(!Number.isSafeInteger(args[0])||args[0]<0||args[0]>maxOutput))throw fail();
   if(name==='split'){if(args.length>2||args[1]!==undefined&&(!Number.isSafeInteger(args[1])||args[1]<0||args[1]>maxList))throw fail();if(args[1]===undefined)args=[args[0],maxList+1];}
   if(name==='replace'||name==='replaceAll'){
    if(args.length!==2||typeof args[0]!=='string'||typeof args[1]!=='string')throw fail();
    const search=args[0],replacement=args[1],unit=replacement.length+(/\$(?:`|'|&)/.test(replacement)?target.length:0);let count=0,position=0;
    if(!search)count=name==='replace'?1:target.length+1;
    else while((position=target.indexOf(search,position))!==-1){count++;position+=search.length;if(name==='replace'||target.length+count*unit>maxOutput)break;}
    if(target.length+count*unit>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');
   }
   if(name==='concat'&&target.length+args.reduce((n,x)=>n+String(x).length,0)>maxOutput)throw fail();
   const result=(String.prototype[name as keyof String] as (...x:any[])=>any).apply(target,args);if(typeof result==='string'&&result.length>maxOutput||Array.isArray(result)&&result.length>maxList)throw new Error('표현식 결과 크기 또는 5,000개 목록 제한을 넘었어요.');return result;
  }
  if(Array.isArray(target)){
   if(!['join','includes','indexOf','slice','at','concat'].includes(name))throw fail();
   target=clean(target);args=args.map(clean);
   if(name==='join'){if(args.length>1||args.length&&typeof args[0]!=='string'||target.some((x:any)=>x!==null&&typeof x==='object'))throw fail();if(target.reduce((n:number,x:any)=>n+String(x??'').length,0)+(target.length-1)*String(args[0]??',').length>maxOutput)throw fail();}
   const result=(Array.prototype[name as keyof any[]] as (...x:any[])=>any).apply(target,args);if(Array.isArray(result)&&result.length>maxList)throw new Error('표현식 목록은 5,000개 항목까지 사용할 수 있어요.');return result;
  }
  throw fail();
 };
 const ev=(expr:Expr):any=>{
  if(++steps>2000)throw fail();switch(expr.kind){
   case 'literal':return expr.value;
   case 'group':return value(ev(expr.value));
   case 'array':return expr.values.map(v=>value(ev(v)));
   case 'object':return Object.fromEntries(expr.values.map(([key,v])=>[key,value(ev(v))]));
   case 'root':switch(expr.name){case '$json':return wrap(item).json;case '$binary':return wrap(item).binary||{};case '$input':return ref('input');case '$node':return ref('nodes');case '$itemIndex':return currentIndex;case '$runIndex':return context.runIndex??0;case '$execution':if(!context.execution)throw new Error('실행 정보는 자동화 실행 안에서 참조할 수 있어요.');return context.execution;case '$parameter':return context.params||{};case 'Math':return ref('math');case 'JSON':return ref('json');default:throw fail();}
   case 'get':{const target=ev(expr.target);if(target===short||target==null&&expr.optional)return short;return read(target,value(ev(expr.key)));}
   case 'call':{
    if(expr.target.kind==='root'){
     const name=expr.target.name,args=expr.args.map(v=>value(ev(v)));if(++calls>100)throw fail();
     if(name==='$'){if(args.length!==1||typeof args[0]!=='string'||!args[0]||args[0].length>200)throw fail();return ref('node',{name:args[0]});}
     if(args.length!==1||!['String','Number','Boolean'].includes(name)||typeof args[0]==='object'&&args[0]!==null)throw fail();return name==='String'?String(args[0]):name==='Number'?Number(args[0]):Boolean(args[0]);
    }
    if(expr.target.kind!=='get')throw fail();const target=ev(expr.target.target);if(target===short||target==null&&expr.target.optional)return short;return method(target,value(ev(expr.target.key)),expr.args.map(v=>value(ev(v))));
   }
   case 'unary':{const v=value(ev(expr.right));if(v!==null&&typeof v==='object'&&expr.op!=='!')throw fail();return expr.op==='!'?!v:expr.op==='-'?-Number(v):Number(v);}
   case 'choose':return ev(value(ev(expr.test))?expr.yes:expr.no);
   case 'binary':{const a=value(ev(expr.left));if(expr.op==='&&')return a&&ev(expr.right);if(expr.op==='||')return a||ev(expr.right);if(expr.op==='??')return a??ev(expr.right);const b=value(ev(expr.right));if((a!==null&&typeof a==='object'||b!==null&&typeof b==='object')&&!['===','!=='].includes(expr.op))throw fail();switch(expr.op){case '+':return a+b;case '-':return a-b;case '*':return a*b;case '/':return a/b;case '%':return a%b;case '===':return a===b;case '!==':return a!==b;case '==':return a==b;case '!=':return a!=b;case '>':return a>b;case '>=':return a>=b;case '<':return a<b;case '<=':return a<=b;default:throw fail();}}
  }
 };return clean(value(ev(tree)));
}
export function resolveExpression(value:any,item:Json,context:ExpressionContext={}):any {
 if(typeof value!=='string')return value;
 const parts:{start:number;end:number;source:string}[]=[];let cursor=0;
 while(true){const start=value.indexOf('{{',cursor);if(start===-1)break;let offset=start+2,quote='',escape=false,braces=0;
  for(;offset<value.length;offset++){const char=value[offset];if(quote){if(escape)escape=false;else if(char==='\\')escape=true;else if(char===quote)quote='';continue;}if(char==='"'||char==="'"){quote=char;continue;}if(char==='{'){braces++;continue;}if(char==='}'&&braces){braces--;continue;}if(char==='}'&&value[offset+1]==='}')break;}
  if(offset>=value.length||parts.length>=100)throw fail();parts.push({start,end:offset+2,source:value.slice(start+2,offset)});cursor=offset+2;
 }
 if(!parts.length){if(Buffer.byteLength(value,'utf8')>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');return value;}
 if(parts.length===1&&/^=?\s*$/.test(value.slice(0,parts[0].start))&&!value.slice(parts[0].end).trim())return evaluate(parse(parts[0].source),item,context);
 let result='',offset=value.startsWith('=')?1:0;for(const part of parts){const data=evaluate(parse(part.source),item,context);result+=value.slice(offset,part.start)+(data==null?'':typeof data==='object'?JSON.stringify(data):String(data));if(result.length>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');offset=part.end;}result+=value.slice(offset);if(result.length>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');if(Buffer.byteLength(result,'utf8')>maxOutput)throw new Error('표현식 결과는 4MB 이내로 줄여주세요.');return result;
}
