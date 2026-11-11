/** Apply the configured shared model connection before any server module reads it.
 * Existing explicit MAIN/VERIFIER settings always take precedence.
 * @param {Record<string,string|undefined>} [env]
 */
export function applyConfiguredModelEnvironment(env=process.env){
 const sharedUrl=env.A4B_LLM_BASE_URL;
 if(!sharedUrl)return env;
 env.A4B_MAIN_PROVIDER??=env.A4B_LLM_PROVIDER||'ollama';
 env.A4B_MAIN_BASE_URL??=sharedUrl.replace(/\/$/,'');
 env.A4B_MAIN_MODEL??=env.A4B_LLM_MODEL||'qwen3:8b-q4_K_M';
 env.A4B_MAIN_API_KEY??=env.A4B_LLM_API_KEY||'';
 env.A4B_VERIFIER_PROVIDER??=env.A4B_MAIN_PROVIDER;
 env.A4B_VERIFIER_BASE_URL??=env.A4B_VERIFIER_PROVIDER==='ollama'?sharedUrl.replace(/\/$/,'').replace(/\/v1$/,''):sharedUrl.replace(/\/$/,'');
 env.A4B_VERIFIER_API_KEY??=env.A4B_LLM_API_KEY||'';
 return env;
}
