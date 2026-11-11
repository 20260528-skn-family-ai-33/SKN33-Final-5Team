// Evaluation-only counterpart to qwen_live_policy.py; no implicit localhost fallback.
export function endpointPolicy(endpoint, allowLocalEndpoint) {
 const url = new URL(endpoint);
 if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname.replace(/\/$/, '') !== '/v1')
  throw Error('Explicit model endpoint ending in /v1 is required');
 const local = ['127.0.0.1', '[::1]'].includes(url.hostname);
 if (local) {
  if (!allowLocalEndpoint) throw Error('Local evaluation requires --allow-local-endpoint with the exact URL');
  const allowed = new URL(allowLocalEndpoint);
  if (allowed.origin !== url.origin || allowed.pathname.replace(/\/$/, '') !== '/v1' || allowed.username || allowed.password || allowed.search || allowed.hash)
   throw Error('Local endpoint differs from explicit opt-in');
 } else if (allowLocalEndpoint || url.protocol !== 'https:' || !url.hostname.endsWith('.proxy.runpod.net')) {
  throw Error('Only RunPod HTTPS or an explicitly selected loopback endpoint is permitted');
 }
 return {url, kind: local ? 'local' : 'runpod'};
}
