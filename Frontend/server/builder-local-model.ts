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

export async function localModelHealth(){
 const connection=localModelConnection();
 if(!connection)return {status:'unconfigured',message:'Qwen 서버 설정이 필요합니다.'};
 try{const headers:Record<string,string>={};if(process.env.A4B_MAIN_API_KEY)headers.Authorization='Bearer '+process.env.A4B_MAIN_API_KEY;
  const response=await fetch(process.env.A4B_MAIN_BASE_URL!.replace(/\/$/,'')+'/models',{headers,signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw Error('unavailable');const body=await response.json() as {data?:{id:string}[]};
  if(!body.data?.some(m=>m.id===connection.model))return {status:'unavailable',message:'설정한 Qwen 모델을 서버에서 찾지 못했습니다.'};
  return {status:'ready',message:'Qwen 서버 응답 확인 · 모델 준비됨'};
 }catch{return {status:'unavailable',message:'Qwen 서버 응답을 확인하지 못했습니다. 서버 실행 상태를 확인하세요.'};}
}
