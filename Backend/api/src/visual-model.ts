import {boundedResponse,SERVER_VISUAL_MODEL_ID} from './assistant';
import type {CredentialSecret} from './contracts';

/** Reuse A4B's server model for visual workflow design; no model API key is requested from members. */
export function visualModelConnection(env:NodeJS.ProcessEnv=process.env):{credential:CredentialSecret;model:string;fetch:typeof fetch}{
 const provider=env.A4B_MAIN_PROVIDER,base=env.A4B_MAIN_BASE_URL,model=env.A4B_MAIN_MODEL;
 if(provider!=='ollama'&&provider!=='vllm'||!base||!model)throw new Error('A4B 설계 모델 연결을 확인해주세요.');
 const url=new URL(base);
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error('A4B 설계 모델 주소를 확인해주세요.');
 const endpoint=new URL(url.pathname.replace(/\/$/,'')+'/chat/completions',url.origin);
 const key=env.A4B_MAIN_API_KEY||'local-not-a-secret';
 const credential:CredentialSecret={id:SERVER_VISUAL_MODEL_ID,name:'A4B 설계 모델',provider:'openai',createdAt:'',updatedAt:'',data:{apiKey:key}};
 const modelFetch:typeof fetch=async(input,init)=>{
  if(String(input)!=='https://api.openai.com/v1/responses'||init?.method!=='POST'||typeof init.body!=='string')throw new Error('지원하지 않는 설계 모델 요청입니다.');
  const request=JSON.parse(init.body) as {instructions:string;input:string;text?:{format?:{type:string;name?:string;schema?:unknown}};max_output_tokens?:number};
  const format=request.text?.format;
  const responseFormat=format?.type==='json_schema'?{type:'json_schema',json_schema:{name:format.name||'workflow',schema:format.schema,strict:true}}:{type:'json_object'};
  const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${key}`},body:JSON.stringify({model,messages:[{role:'system',content:request.instructions},{role:'user',content:request.input}],response_format:responseFormat,max_tokens:request.max_output_tokens||4000,stream:false,...(provider==='ollama'?{think:false,reasoning_effort:'none'}:{temperature:0.7,top_p:0.8,chat_template_kwargs:{enable_thinking:false}})}),signal:init.signal});
  if(!response.ok)return Response.json({error:'model_request_failed'},{status:response.status});
  const value=JSON.parse(await boundedResponse(response)) as {choices?:{message?:{content?:unknown;refusal?:unknown}}[]};
  const message=value.choices?.[0]?.message;
  if(typeof message?.content!=='string')throw new Error('A4B 설계 모델 응답을 확인해주세요.');
  return Response.json({status:'completed',output:[{type:'message',content:[message.refusal?{type:'refusal'}:{type:'output_text',text:message.content}]}]});
 };
 return {credential,model,fetch:modelFetch};
}
