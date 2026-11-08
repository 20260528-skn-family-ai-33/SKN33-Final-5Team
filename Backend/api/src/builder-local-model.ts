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
 const target=modelHealthTarget(process.env.A4B_MAIN_BASE_URL!);
 if(target!=='runpod')return result('unconfigured',target,'공용 RunPod AI 서버가 연결되어 있지 않습니다.');
 try{const headers:Record<string,string>={'Cache-Control':'no-store'};if(process.env.A4B_MAIN_API_KEY)headers.Authorization='Bearer '+process.env.A4B_MAIN_API_KEY;
  const response=await fetch(process.env.A4B_MAIN_BASE_URL!.replace(/\/$/,'')+'/models',{headers,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw Error('unavailable');const body=await response.json() as {data?:{id:string}[]};
  // Ollama expands an omitted tag to :latest in /v1/models. Registry ports
  // belong to the prefix, so inspect only the final model-name segment.
  const canonical=(name:string)=>process.env.A4B_MAIN_PROVIDER==='ollama'&&!name.slice(name.lastIndexOf('/')+1).includes(':')?name+':latest':name;
  if(!body.data?.some(m=>canonical(m.id)===canonical(connection.model)))return result('unavailable',target,'RunPod에서 설정한 AI 모델을 찾지 못했습니다.');
  return result('ready',target,'공용 RunPod AI 서버 응답과 모델 준비를 확인했습니다.');
 }catch{return result('unavailable',target,'공용 RunPod AI 서버에 연결할 수 없습니다. 잠시 후 다시 확인해주세요.');}
}
