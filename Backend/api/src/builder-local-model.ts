/** Local model selection is explicit and needs no persisted dummy API credential. */
export const LOCAL_MODEL_ID='local-model';
export function localModelConnection(){
 const provider=process.env.A4B_MAIN_PROVIDER;
 if(provider!=='ollama'&&provider!=='vllm')return null;
 if(!process.env.A4B_MAIN_BASE_URL)throw new Error('A4B_MAIN_BASE_URL이 필요합니다.');
 const url=new URL(process.env.A4B_MAIN_BASE_URL);
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error('모델 서버 주소를 확인하세요.');
 const model=process.env.A4B_MAIN_MODEL;
 if(!model||model.length>120||!/^[-\w./:]+$/.test(model))throw new Error('A4B_MAIN_MODEL을 확인하세요.');
 return {model,credential:{id:LOCAL_MODEL_ID,name:`${provider==='ollama'?'Ollama':'vLLM'} · ${model}`,provider:'openai' as const,createdAt:'',updatedAt:''}};
}

export type ModelHealthTarget='runpod'|'local'|'remote'|'unconfigured';
const canonicalOllamaModel=(name:string)=>name.slice(name.lastIndexOf('/')+1).includes(':')?name:name+':latest';
function ollamaModelAliases():Map<string,string>{
 const raw=process.env.A4B_OLLAMA_MODEL_ALIASES?.trim();
 if(process.env.A4B_MAIN_PROVIDER!=='ollama'||!raw)return new Map();
 const aliases:unknown=JSON.parse(raw);
 if(!aliases||typeof aliases!=='object'||Array.isArray(aliases))throw Error('Invalid model aliases');
 const entries=Object.entries(aliases);
 if(entries.some(pair=>pair.some(name=>typeof name!=='string'||!/^[-A-Za-z0-9_./:]{1,120}$/.test(name))))throw Error('Invalid model aliases');
 const normalized=new Map<string,string>(entries.map(([source,target])=>[canonicalOllamaModel(source),target]));
 if(normalized.size!==entries.length||[...normalized.values()].some(target=>normalized.has(canonicalOllamaModel(target))))throw Error('Ambiguous model aliases');
 return normalized;
}
/** The shared service badge must never report a personal/local model as RunPod. */
export function modelHealthTarget(baseUrl:string):ModelHealthTarget{
 const url=new URL(baseUrl),host=url.hostname.toLowerCase();
 if(url.protocol==='https:'&&(host.endsWith('.proxy.runpod.net')||host==='api.runpod.ai'))return 'runpod';
 if(host==='localhost'||host.endsWith('.localhost')||/^127\./.test(host)||host==='[::1]')return 'local';
 return 'remote';
}
export async function localModelHealth(){
 const result=(status:'ready'|'unavailable'|'unconfigured',target:ModelHealthTarget,message:string)=>({status,target,message,checked_at:new Date().toISOString()});
 let connection:ReturnType<typeof localModelConnection>;
 try{connection=localModelConnection();}catch{return result('unconfigured','unconfigured','공용 RunPod AI 서버 설정을 확인해주세요.');}
 if(!connection)return result('unconfigured','unconfigured','공용 RunPod AI 서버 설정이 필요합니다.');
 const required=[{role:'설계',model:connection.model}];
 if(process.env.A4B_CAPABILITY_MODE==='enforce'){
  const model=process.env.A4B_CAPABILITY_MODEL||connection.model;
  if(model.length>120||!/^[-\w./:]+$/.test(model))return result('unconfigured','unconfigured','지원 판정 모델 설정(A4B_CAPABILITY_MODEL)을 확인해주세요.');
  required.push({role:'지원 판정',model});
 }
 try{
  const aliases=ollamaModelAliases();
  for(const item of required)item.model=aliases.get(canonicalOllamaModel(item.model))||item.model;
  for(const model of aliases.values())if(!required.some(item=>item.model===model))required.push({role:'이전 모델 호환',model});
 }catch{return result('unconfigured','unconfigured','이전 모델 전환 설정(A4B_OLLAMA_MODEL_ALIASES)을 확인해주세요.');}
 const target=modelHealthTarget(process.env.A4B_MAIN_BASE_URL!);
 if(target!=='runpod')return result('unconfigured',target,'공용 RunPod AI 서버가 연결되어 있지 않습니다.');
 try{const headers:Record<string,string>={'Cache-Control':'no-store'};if(process.env.A4B_MAIN_API_KEY)headers.Authorization='Bearer '+process.env.A4B_MAIN_API_KEY;
  const response=await fetch(process.env.A4B_MAIN_BASE_URL!.replace(/\/$/,'')+'/models',{headers,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw Error('unavailable');const body=await response.json() as {data?:{id:string}[]};
  // Ollama expands an omitted tag to :latest in /v1/models. Registry ports
  // belong to the prefix, so inspect only the final model-name segment.
  const canonical=(name:string)=>process.env.A4B_MAIN_PROVIDER==='ollama'?canonicalOllamaModel(name):name;
  const missing=required.filter(({model})=>!body.data?.some(m=>canonical(m.id)===canonical(model)));
  if(missing.length)return result('unavailable',target,'RunPod에서 '+missing.map(({role,model})=>`${role} 모델 (${model})`).join(', ')+'을 찾지 못했습니다.');
  return result('ready',target,'공용 RunPod AI 서버 응답과 모델 준비를 확인했습니다.');
 }catch{return result('unavailable',target,'공용 RunPod AI 서버에 연결할 수 없습니다. 잠시 후 다시 확인해주세요.');}
}
