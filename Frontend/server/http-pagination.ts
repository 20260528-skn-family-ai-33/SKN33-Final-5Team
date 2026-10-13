import {createHash} from 'node:crypto';
import {clone,type Json} from '../lib/workflow';

export type PaginationResponse={body:any;headers:Record<string,string>;statusCode:number;statusMessage?:string;binaryBytes?:number};
export type PaginationRequest={url:string;method:string;headers:Record<string,string>;qs:Json;body?:any};
export type PaginationContext={$json:Json;$pageCount:number;$response?:PaginationResponse;$request?:PaginationRequest};
export type PaginationParameter={type:'qs'|'body'|'headers';name:string;value:any};
export type PaginationConfig={paginationMode:'responseContainsNextURL'|'updateAParameterInEachRequest';nextURL?:any;parameters:PaginationParameter[];paginationCompleteWhen:'responseIsEmpty'|'receiveSpecificStatusCodes'|'other';statusCodes:number[];completeExpression?:any;maxRequests:number;explicitLimit:boolean;requestInterval:number};
export type PaginationUpdates={qs:Json;body:Json;headers:Json};
const unsafe=new Set(['__proto__','prototype','constructor']);
const error=()=>new Error('페이지 표현식은 $pageCount, $response.body·headers·statusCode, $request, $json과 숫자 계산·비교를 사용해주세요. 함수 호출이나 코드는 맞춤 계산 단계에서 처리해주세요.');
type Expr={kind:'literal';value:any}|{kind:'root';name:string}|{kind:'get';target:Expr;key:Expr}|{kind:'unary';op:string;right:Expr}|{kind:'binary';op:string;left:Expr;right:Expr}|{kind:'choose';test:Expr;yes:Expr;no:Expr};
type Token={value:string;kind:'string'|'number'|'identifier'|'operator'};
const precedence:Record<string,number>={'??':1,'||':2,'&&':3,'==':4,'!=':4,'===':4,'!==':4,'<':5,'>':5,'<=':5,'>=':5,'+':6,'-':6,'*':7,'/':7,'%':7};
function parse(source:string):Expr {
 if(source.length>2000)throw error();const tokens:Token[]=[];let offset=0;
 const pattern=/\s*(?:(\d+(?:\.\d+)?)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([\p{L}_$][\p{L}\p{N}_$]*)|(===|!==|==|!=|>=|<=|\|\||&&|\?\?|\?\.|[()[\].!+*/%?:<>-]))/uy;
 while(offset<source.length){if(!source.slice(offset).trim())break;pattern.lastIndex=offset;const match=pattern.exec(source);if(!match)throw error();offset=pattern.lastIndex;tokens.push({value:match[1]??match[2]??match[3]??match[4],kind:match[1]?'number':match[2]?'string':match[3]?'identifier':'operator'});if(tokens.length>256)throw error();}
 let index=0,depth=0;const peek=()=>tokens[index]?.value,consume=(value:string)=>{if(peek()!==value)throw error();index++;};
 const expression=(minimum=0):Expr=>{
  if(++depth>40)throw error();let result:Expr;const token=tokens[index++];if(!token)throw error();
  if(token.value==='('){result=expression();consume(')');}
  else if(['!','-','+'].includes(token.value))result={kind:'unary',op:token.value,right:expression(8)};
  else if(token.kind==='number')result={kind:'literal',value:Number(token.value)};
  else if(token.kind==='string'){let value:string;try{value=token.value.startsWith('"')?JSON.parse(token.value):token.value.slice(1,-1).replace(/\\(['\\nrt])/g,(_,char)=>(({n:'\n',r:'\r',t:'\t'} as Record<string,string>)[char]??char));}catch{throw error();}result={kind:'literal',value};}
  else if(['true','false','null','undefined'].includes(token.value))result={kind:'literal',value:token.value==='true'?true:token.value==='false'?false:token.value==='null'?null:undefined};
  else if(['$pageCount','$response','$request','$json'].includes(token.value))result={kind:'root',name:token.value};
  else throw error();
  while(['.','?.','['].includes(peek())){
   const operator=tokens[index++].value;let key:Expr;
   if(operator==='['){key=expression();consume(']');if(key.kind==='literal'&&unsafe.has(String(key.value)))throw error();}else{const property=tokens[index++];if(!property||property.kind!=='identifier'||property.value.startsWith('$')||unsafe.has(property.value))throw error();key={kind:'literal',value:property.value};}
   result={kind:'get',target:result,key};
  }
  while(peek()&&precedence[peek()]!==undefined&&precedence[peek()]>=minimum){const op=tokens[index++].value;result={kind:'binary',op,left:result,right:expression(precedence[op]+1)};}
  if(minimum===0&&peek()==='?'){index++;const yes=expression();consume(':');result={kind:'choose',test:result,yes,no:expression()};}
  depth--;return result;
 };
 const tree=expression();if(index!==tokens.length)throw error();return tree;
}
function evaluate(tree:Expr,context:PaginationContext):any {
 switch(tree.kind){
  case 'literal':return tree.value;
  case 'root':return context[tree.name as keyof PaginationContext];
  case 'get':{const target=evaluate(tree.target,context),key=evaluate(tree.key,context);if(!['string','number'].includes(typeof key)||unsafe.has(String(key)))throw error();if(target==null)return undefined;if(key==='length'&&(Array.isArray(target)||typeof target==='string'))return target.length;return typeof target==='object'&&Object.hasOwn(target,key)?target[key]:undefined;}
  case 'unary':{const value=evaluate(tree.right,context);return tree.op==='!'?!value:tree.op==='-'?-Number(value):Number(value);}
  case 'choose':return evaluate(evaluate(tree.test,context)?tree.yes:tree.no,context);
  case 'binary':{
   const a=evaluate(tree.left,context);if(tree.op==='&&')return a&&evaluate(tree.right,context);if(tree.op==='||')return a||evaluate(tree.right,context);if(tree.op==='??')return a??evaluate(tree.right,context);const b=evaluate(tree.right,context);
   switch(tree.op){case '+':return a+b;case '-':return a-b;case '*':return a*b;case '/':return a/b;case '%':return a%b;case '===':return a===b;case '!==':return a!==b;case '==':return a==b;case '!=':return a!=b;case '>':return a>b;case '>=':return a>=b;case '<':return a<b;case '<=':return a<=b;default:throw error();}
  }
 }
}
export function resolvePagination(value:any,context:PaginationContext):any {
 if(typeof value!=='string')return value;
 const exact=/^=?\s*\{\{\s*([\s\S]*?)\s*\}\}$/.exec(value);if(exact)return evaluate(parse(exact[1]),context);
 return value.replace(/\{\{\s*([\s\S]*?)\s*\}\}/g,(_,source)=>{const result=evaluate(parse(source),context);return result==null?'':typeof result==='object'?JSON.stringify(result):String(result);});
}
function validateExpression(value:any):void {if(typeof value==='string')for(const match of value.matchAll(/\{\{\s*([\s\S]*?)\s*\}\}/g))parse(match[1]);}
function validName(name:any):string {if(typeof name!=='string'||!name.trim()||name.length>200||/[\x00-\x1f]/.test(name)||name.split('.').some(part=>!part||unsafe.has(part)))throw new Error('페이지 매개변수 이름을 확인해주세요.');return name;}
export function readPagination(parameters:Json):PaginationConfig|undefined {
 const outer=parameters.options?.pagination;if(outer==null)return undefined;if(typeof outer!=='object'||Array.isArray(outer))throw new Error('페이지 순회 설정은 JSON 객체로 입력해주세요.');
 const p=Object.hasOwn(outer,'pagination')?outer.pagination:outer;if(!p||typeof p!=='object'||Array.isArray(p))throw new Error('페이지 순회 설정을 확인해주세요.');const mode=p.paginationMode??'off';if(mode==='off')return undefined;
 if(p.limitPagesFetched!==undefined&&typeof p.limitPagesFetched!=='boolean')throw new Error('페이지 수 제한은 true 또는 false로 설정해주세요.');
 if(!['responseContainsNextURL','updateAParameterInEachRequest'].includes(mode))throw new Error('페이지 순회 방식을 확인해주세요.');
 const complete=p.paginationCompleteWhen??'responseIsEmpty';if(!['responseIsEmpty','receiveSpecificStatusCodes','other'].includes(complete))throw new Error('페이지 순회의 종료 조건을 확인해주세요.');
 const maxRequests=Number(p.limitPagesFetched?p.maxRequests??100:100),requestInterval=Number(p.requestInterval??0);
 if(!Number.isSafeInteger(maxRequests)||maxRequests<1||maxRequests>1000)throw new Error('최대 페이지 수는 1~1,000 사이의 정수로 설정해주세요.');
 if(!Number.isFinite(requestInterval)||requestInterval<0||requestInterval>60000)throw new Error('페이지 요청 간격은 0~60,000ms 사이로 설정해주세요.');
 const params:PaginationParameter[]=mode==='updateAParameterInEachRequest'?p.parameters?.parameters:[];
 if(!Array.isArray(params)||mode==='updateAParameterInEachRequest'&&!params.length||params.length>50)throw new Error('페이지마다 바꿀 매개변수를 1~50개 설정해주세요.');
 const names=new Set<string>();for(const parameter of params){if(!parameter||!['qs','body','headers'].includes(parameter.type))throw new Error('페이지 매개변수는 쿼리, 본문 또는 헤더를 선택해주세요.');validName(parameter.name);const key=parameter.type+':'+parameter.name.toLowerCase();if(names.has(key))throw new Error('페이지 매개변수 이름이 겹쳐요.');names.add(key);if(parameter.value===undefined||parameter.value==='')throw new Error('페이지 매개변수 값을 입력해주세요.');validateExpression(parameter.value);}
 if(mode==='responseContainsNextURL'){if(typeof p.nextURL!=='string'||!p.nextURL.trim())throw new Error('다음 페이지 주소 또는 주소를 읽는 표현식을 입력해주세요.');validateExpression(p.nextURL);}
 const statusCodes=complete==='receiveSpecificStatusCodes'?String(p.statusCodesWhenComplete??'').split(',').map((value:string)=>Number(value.trim())):[];
 if(complete==='receiveSpecificStatusCodes'&&(!statusCodes.length||statusCodes.some((code:number)=>!Number.isInteger(code)||code<100||code>599)))throw new Error('종료 상태 코드는 100~599 사이 숫자를 쉼표로 나눠 입력해주세요.');
 if(complete==='other'){if(typeof p.completeExpression!=='string'||!/^=?\s*\{\{[\s\S]+\}\}$/.test(p.completeExpression))throw new Error('페이지 종료 조건은 {{ ... }} 표현식으로 설정해주세요.');validateExpression(p.completeExpression);}
 return {paginationMode:mode,nextURL:p.nextURL,parameters:clone(params),paginationCompleteWhen:complete,statusCodes,completeExpression:p.completeExpression,maxRequests,explicitLimit:p.limitPagesFetched===true,requestInterval};
}
export function paginationUpdates(config:PaginationConfig|undefined,context:PaginationContext):PaginationUpdates {
 const result:PaginationUpdates={qs:{},body:{},headers:{}};
 for(const parameter of config?.parameters||[])result[parameter.type][parameter.name]=resolvePagination(parameter.value,context);return result;
}
export function patchPaginationBody(body:any,updates:Json):Json {
 if(!body||typeof body!=='object'||Array.isArray(body))throw new Error('페이지 본문 매개변수는 JSON 객체 또는 폼 본문에서 사용할 수 있어요.');
 const result=clone(body);for(const [name,value] of Object.entries(updates)){const keys=validName(name).split('.');let target=result;for(const key of keys.slice(0,-1)){if(target[key]===undefined)target[key]={};if(!target[key]||typeof target[key]!=='object'||Array.isArray(target[key]))throw new Error('페이지 본문 항목 이름이 기존 값과 겹쳐요.');target=target[key];}if(value===undefined)delete target[keys.at(-1)!];else target[keys.at(-1)!]=value;}return result;
}
export function paginationComplete(config:PaginationConfig,context:PaginationContext):boolean {
 const response=context.$response!;
 if(config.paginationCompleteWhen==='receiveSpecificStatusCodes')return config.statusCodes.includes(response.statusCode);
 if(config.paginationCompleteWhen==='other'){const complete=resolvePagination(config.completeExpression,context);if(typeof complete!=='boolean')throw new Error('페이지 종료 표현식은 true 또는 false를 반환해야 해요.');return complete;}
 if(response.binaryBytes!==undefined)return response.binaryBytes===0;
 return Array.isArray(response.body)?!response.body.length:response.body==null||response.body===false||response.body===0||typeof response.body==='string'&&!response.body.trim();
}
export function paginationNextURL(value:any,base:URL):URL {
 if(typeof value!=='string'||!value.trim())throw new Error('다음 페이지 주소를 읽을 수 없어요. 종료 조건과 응답의 주소 항목을 확인해주세요.');
 let next:URL;try{next=new URL(value,base);}catch{throw new Error('다음 페이지 주소가 올바르지 않아요.');}
 if(!['http:','https:'].includes(next.protocol)||next.username||next.password)throw new Error('페이지 주소는 인증 정보가 없는 http 또는 https 주소를 사용해주세요.');next.hash='';return next;
}
/** Request claims contain hashes only; changing auth tokens cannot hide a repeated page. */
export class PaginationGuard {
 private seen=new Set<string>();private origin?:string;private count=0;
 limited=false;
 get requestsFetched(){return this.count;}
 constructor(readonly config:PaginationConfig|undefined){}
 claim(request:PaginationRequest,authenticated:boolean):void {
  const url=paginationNextURL(request.url,new URL(request.url));if(this.origin&&authenticated&&url.origin!==this.origin)throw new Error('계정 정보를 사용하는 페이지 요청은 처음 주소와 같은 사이트에서만 이어갈 수 있어요.');this.origin??=url.origin;
  const canonical=new URL(url);canonical.searchParams.sort();const key=createHash('sha256').update(JSON.stringify({url:canonical.toString(),method:request.method,body:request.body,headers:Object.fromEntries(Object.entries(request.headers).filter(([name])=>!/authorization|cookie|token|api[-_]?key|secret|password/i.test(name)))})).digest('hex');
  if(this.seen.has(key))throw new Error('이미 요청한 페이지로 다시 돌아왔어요. 다음 주소나 페이지 번호의 설정을 확인해주세요.');
  if(this.count>=(this.config?.maxRequests??1))throw new Error('페이지 요청 수 제한에 도달했어요. 종료 조건이나 최대 페이지 수를 확인해주세요.');this.seen.add(key);this.count++;
 }
 shouldStop(context:PaginationContext):boolean {
  if(!this.config)return true;if(paginationComplete(this.config,context))return true;
  if(this.count>=this.config.maxRequests){if(this.config.explicitLimit){this.limited=true;return true;}throw new Error('100페이지 제한에 도달했어요. 종료 조건이나 최대 페이지 수를 확인해주세요.');}return false;
 }
}
