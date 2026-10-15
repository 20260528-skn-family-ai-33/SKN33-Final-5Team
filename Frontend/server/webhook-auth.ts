import {createHmac,timingSafeEqual} from 'node:crypto';
import {validateHeaderName,type IncomingHttpHeaders} from 'node:http';
import type {Flow,FlowNode,Json} from '../lib/workflow';
import type {CredentialSecret,Provider} from './contracts';
import {verifyWebhookJwt} from './webhook-response';
import {validateWebhookRequestOptions} from './webhook-request';

const methods=new Set(['DELETE','GET','HEAD','PATCH','POST','PUT']);
const providers:Record<string,Provider>={basicAuth:'basic',headerAuth:'header',bearerAuth:'bearer',jwtAuth:'jwt'};
const forbidden=new Set(['__proto__','prototype','constructor']);
export class WebhookAuthError extends Error {
 constructor(message:string,readonly status:number,readonly headers:Record<string,string>={}){super(message);}
}
export function webhookAuthentication(node:FlowNode):string{return String(node.parameters.authentication??'easyflowToken');}
export function webhookCredentialProvider(node:FlowNode):Provider|undefined{return providers[webhookAuthentication(node)];}
export function webhookPath(node:FlowNode):string {
 const raw=node.parameters.path??node.id;
 if(typeof raw!=='string'||raw.length>200)throw new Error('요청 주소 이름은 200자 이내의 글자로 입력해주세요.');
 const path=raw.replace(/^\/+|\/+$/g,'');if(!path)throw new Error('요청 주소 이름을 입력해주세요.');const names=new Set<string>();
 for(const part of path.split('/')){if(!part||['.','..'].includes(part)||/[?#\u0000-\u001f]/.test(part))throw new Error('요청 주소 이름에 사용할 수 없는 문자가 있어요.');if(part.startsWith(':')){const name=part.slice(1);if(!/^[A-Za-z_$][\w$]*$/.test(name)||forbidden.has(name)||names.has(name))throw new Error('주소 변수는 서로 다른 항목 이름으로 입력해주세요.');names.add(name);}}
 return path;
}
export function encodedWebhookPath(node:FlowNode):string{return webhookPath(node).split('/').map(part=>part.startsWith(':')?part:encodeURIComponent(part)).join('/');}
export function webhookParams(node:FlowNode,rawPath:string):Json|undefined {
 const expected=webhookPath(node).split('/');let parts:string[];try{parts=rawPath.replace(/^\/+|\/+$/g,'').split('/').map(part=>decodeURIComponent(part));}catch{return undefined;}
 if(parts.length!==expected.length){if(parts.length===1&&!expected.some(part=>part.startsWith(':'))&&parts[0]===expected.join('/'))return {};return undefined;}const values:[string,string][]=[];
 for(let i=0;i<expected.length;i++){if(expected[i].startsWith(':')){if(!parts[i])return undefined;values.push([expected[i].slice(1),parts[i]]);}else if(expected[i]!==parts[i])return undefined;}
 return Object.fromEntries(values);
}
export function validateWebhookConfiguration(flow:Flow,node:FlowNode):void {
 validateWebhookRequestOptions(node);
 webhookPath(node);if(!methods.has(node.parameters.httpMethod??'GET'))throw new Error('Webhook 요청 방식은 DELETE, GET, HEAD, PATCH, POST, PUT 중에서 선택해주세요.');
 const auth=webhookAuthentication(node);if(!['easyflowToken','none',...Object.keys(providers)].includes(auth))throw new Error('지원되는 Webhook 인증 방식을 선택해주세요.');
 const mode=node.parameters.responseMode??'onReceived';if(!['onReceived','lastNode','responseNode'].includes(mode))throw new Error('Webhook 스트리밍 응답은 아직 지원하지 않아요. 접수 알림, 마지막 결과 또는 응답 단계를 선택해주세요.');
 const seen=new Set<string>(),queue=[node.name];while(queue.length){const name=queue.shift()!;if(seen.has(name))continue;seen.add(name);for(const port of flow.connections[name]?.main??[])for(const edge of port??[])queue.push(edge.node);}
 const responses=flow.nodes.filter(n=>seen.has(n.name)&&n.type==='n8n-nodes-base.respondToWebhook');
 if(mode==='responseNode'&&!responses.length)throw new Error('‘응답 단계에서 정하기’를 선택했다면 Webhook 뒤에 요청에 응답 단계를 연결해주세요.');
 if(mode!=='responseNode'&&responses.length)throw new Error('요청에 응답 단계를 사용하려면 Webhook의 응답 방법을 ‘응답 단계에서 정하기’로 바꿔주세요.');
}
function equal(a:string,b:string):boolean {const left=Buffer.from(a),right=Buffer.from(b);return left.length===right.length&&timingSafeEqual(left,right);}
function value(headers:IncomingHttpHeaders,name:string):string {const entry=headers[name.toLowerCase()];return typeof entry==='string'?entry:'';}
export async function authenticateWebhook(node:FlowNode,token:string|undefined,headers:IncomingHttpHeaders,query:URLSearchParams,getCredential:(id:string)=>CredentialSecret|Promise<CredentialSecret>):Promise<{headers:IncomingHttpHeaders;jwtPayload?:Json}> {
 const auth=webhookAuthentication(node),clean={...headers};for(const name of ['x-easyflow-token','authorization','cookie','x-auth-token'])delete clean[name];
 if(auth==='none')return {headers:clean};
 if(auth==='easyflowToken'){const supplied=value(headers,'x-easyflow-token')||query.get('token')||'';if(!token||!equal(supplied,token))throw new WebhookAuthError('Webhook 토큰을 확인해주세요.',401);return {headers:clean};}
 const expectedProvider=providers[auth];if(!expectedProvider)throw new WebhookAuthError('지원되는 Webhook 인증 방식을 선택해주세요.',400);
 if(!node.credentialId)throw new WebhookAuthError('Webhook에서 연결 계정을 선택해주세요.',500);
 const credential=await getCredential(node.credentialId);if(credential.provider!==expectedProvider)throw new WebhookAuthError('Webhook 인증 방식에 맞는 연결 계정을 선택해주세요.',500);const data=credential.data;
 if(auth==='basicAuth'){
  const user=data.user??data.username,password=data.password;if(typeof user!=='string'||!user||typeof password!=='string'||!password)throw new WebhookAuthError('Webhook 연결 계정의 아이디와 비밀번호를 확인해주세요.',500);
  const authorization=value(headers,'authorization');if(!authorization){const alternate=value(headers,'x-auth-token');if(alternate){const signature=createHmac('sha256',`${user}:${password}`).update(`${node.id}-${node.webhookId}`).digest('hex');if(!equal(alternate,signature))throw new WebhookAuthError('Webhook 인증 정보를 확인해주세요.',403);}else throw new WebhookAuthError('Webhook 아이디와 비밀번호를 보내주세요.',401,{'WWW-Authenticate':'Basic realm="Easyflow", charset="UTF-8"'});}
  else {const match=/^Basic ([A-Za-z0-9+/]+={0,2})$/i.exec(authorization);if(!match||!equal(Buffer.from(match[1],'base64').toString('utf8'),`${user}:${password}`))throw new WebhookAuthError('Webhook 아이디와 비밀번호를 확인해주세요.',401,{'WWW-Authenticate':'Basic realm="Easyflow", charset="UTF-8"'});}
 }else if(auth==='headerAuth'){
  const name=data.name??data.headerName,secret=data.value??data.headerValue;if(typeof name!=='string'||!name||typeof secret!=='string'||!secret)throw new WebhookAuthError('Webhook 연결 계정의 헤더 이름과 값을 확인해주세요.',500);try{validateHeaderName(name);}catch{throw new WebhookAuthError('Webhook 인증 헤더 이름을 확인해주세요.',500);}delete clean[name.toLowerCase()];if(!equal(value(headers,name),secret))throw new WebhookAuthError('Webhook 인증 헤더를 확인해주세요.',403);
 }else if(auth==='bearerAuth'){
  if(typeof data.token!=='string'||!data.token)throw new WebhookAuthError('Webhook 연결 계정의 API 토큰을 확인해주세요.',500);if(!equal(value(headers,'authorization'),`Bearer ${data.token}`))throw new WebhookAuthError('Webhook 인증 토큰을 확인해주세요.',403);
 }else if(auth==='jwtAuth'){
  const match=/^Bearer\s+(\S+)$/i.exec(value(headers,'authorization'));if(!match)throw new WebhookAuthError('Webhook JWT 인증을 보내주세요.',401);try{return {headers:clean,jwtPayload:verifyWebhookJwt(match[1],credential)};}catch{throw new WebhookAuthError('Webhook JWT의 서명과 유효 시간을 확인해주세요.',403);}
 }
 return {headers:clean};
}
