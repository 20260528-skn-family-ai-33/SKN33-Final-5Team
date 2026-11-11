// Explicit evaluation-only overlays. They do not edit .env or production settings.
import {readFileSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {resolve} from 'node:path';

export function applyEvaluationProfile(profile, env, root) {
 if (profile === 'documented-9b-deepagent') {
  for (const name of ['.env.aws.qwen35-9b.example', '.env.aws.qwen35-9b-deepagent.example'])
   Object.assign(env, parseEnv(readFileSync(resolve(root, 'infrastructure/env', name), 'utf8')));
 } else if (profile === 'local-public-9b-deepagent') {
  Object.assign(env, parseEnv(readFileSync(resolve(root, 'infrastructure/env/.env.aws.qwen35-9b-deepagent.example'), 'utf8')));
  Object.assign(env, {
   A4B_MAIN_PROVIDER: 'ollama',
   A4B_MAIN_BASE_URL: 'http://127.0.0.1:11434/v1',
   A4B_MAIN_API_KEY: '',
   A4B_MAIN_MODEL: 'a4b-public-qwen35-9b-32k',
   A4B_CAPABILITY_MODEL: 'a4b-public-qwen35-9b-32k',
   A4B_OLLAMA_NUM_CTX: '32768',
   A4B_OLLAMA_NUM_PREDICT: '4096',
   A4B_OLLAMA_TEMPERATURE: '0',
   A4B_MAIN_TIMEOUT_S: '180',
   A4B_REQUIRE_LOCAL_MODEL: '1',
  });
  // Public weights must never masquerade as either private fine-tuned model.
  delete env.A4B_OLLAMA_MODEL_ALIASES;
 } else if (profile && profile !== 'configured') {
  throw Error('Unknown profile');
 }
 return env;
}
