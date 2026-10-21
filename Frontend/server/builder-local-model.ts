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
