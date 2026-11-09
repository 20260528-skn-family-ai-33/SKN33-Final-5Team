import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const require = createRequire(new URL('../../package.json', import.meta.url));
const compiled = buildSync({entryPoints: [resolve(frontendRoot, 'app/ai-status.tsx')], absWorkingDir: frontendRoot,
  bundle: true, platform: 'node', format: 'cjs', packages: 'external', loader: {'.css': 'empty'}, write: false, logLevel: 'silent'}).outputFiles[0].text;
const globals = ['window', 'document', 'navigator', 'location', 'HTMLElement', 'HTMLInputElement', 'Element', 'Node',
  'NodeFilter', 'SVGElement', 'HTMLButtonElement', 'DocumentFragment', 'MutationObserver', 'Event', 'CustomEvent',
  'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT'];
const previous = new Map(globals.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const originalFetch = globalThis.fetch;
let dom: JSDOM;
function installDom() {
  dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {url: 'http://localhost/?screen=workflows', pretendToBeVisual: true});
  for (const key of globals) {
    if (key === 'IS_REACT_ACT_ENVIRONMENT') Object.defineProperty(globalThis, key, {value: true, writable: true, configurable: true});
    else if (key in dom.window) Object.defineProperty(globalThis, key, {value: (dom.window as any)[key], writable: true, configurable: true});
  }
}
installDom();
const {createElement, act, StrictMode} = require('react');
const {createRoot} = require('react-dom/client');
const module = {exports: {} as any};
new Function('require', 'module', 'exports', compiled)(require, module, module.exports);
const {AiStatus} = module.exports;
const tick = () => new Promise(resolve => setImmediate(resolve));
const status = () => document.querySelector('.ai-status-badge')?.textContent;
const button = (label: string) => {
  const found = Array.from(document.querySelectorAll('button')).find(value => value.textContent === label);
  assert.ok(found, 'Missing button: ' + label);
  return found;
};
const response = (value: string, target = 'runpod') => new Response(JSON.stringify({health: {status: value, target}}), {headers: {'Content-Type': 'application/json'}});
async function mount(fetcher: typeof fetch, strict = false, configureWindow?: () => void) {
  dom.window.close(); installDom();
  configureWindow?.();
  globalThis.fetch = fetcher;
  const root = createRoot(document.getElementById('root'));
  await act(async () => {root.render(strict ? createElement(StrictMode, null, createElement(AiStatus)) : createElement(AiStatus)); await tick();});
  return {
    close: async () => {
      // Radix dispatches its unmount autofocus event from a zero-delay timer.
      // Drain it before replacing this DOM or restoring Node's CustomEvent.
      await act(async () => {root.unmount(); await new Promise(done => setTimeout(done, 0));});
      globalThis.fetch = originalFetch;
    },
  };
}
async function click(value: HTMLElement) {await act(async () => {value.click(); await tick();});}

test('AI status explains which model is missing and clears that detail after recovery',async()=>{
  let calls=0;
  const message='RunPod에서 지원 판정 모델 (previous105)을 찾지 못했습니다.';
  const h=await mount((async()=>++calls===1
    ? new Response(JSON.stringify({health:{status:'unavailable',target:'runpod',message}}))
    : response('ready')) as typeof fetch);
  try{
    assert.equal(status(),'AI 일시 중단');await click(button('AI 일시 중단'));
    assert.ok(document.body.textContent?.includes(message));
    await click(button('다시 확인'));assert.equal(status(),'AI 사용 가능');
    assert.ok(!document.body.textContent?.includes('previous105'));
  }finally{await h.close();}
});

test('AI status shows a failure, retries without overlap, and recovers using the live connection endpoint', async () => {
  const calls: RequestInit[] = [];
  let resolveRetry!: (value: Response) => void;
  const h = await mount((async (path: any, init: RequestInit) => {
    assert.equal(path, '/api/agent-builder/connection');
    assert.equal(init.credentials, 'same-origin'); assert.equal(init.cache, 'no-store');
    calls.push(init);
    return calls.length === 1 ? new Response('{}', {status: 503}) : await new Promise<Response>(resolve => {resolveRetry = resolve;});
  }) as typeof fetch);
  try {
    assert.equal(status(), 'AI 일시 중단');
    await click(button('AI 일시 중단'));
    assert.match(document.body.textContent || '', /공용 RunPod AI 서버에 연결하지 못했어요/);
    await click(button('다시 확인'));
    assert.equal(status(), 'AI 연결 확인 중');
    assert.equal(button('다시 확인').disabled, true);
    await click(button('다시 확인'));
    assert.equal(calls.length, 2);
    await act(async () => {resolveRetry(response('ready')); await tick();});
    assert.equal(status(), 'AI 사용 가능');
    assert.equal(button('다시 확인').disabled, false);
    assert.match(document.body.textContent || '', /공용 RunPod AI 서버에 연결되어 AI 작업을 실행할 수 있어요/);
    assert.match(document.body.textContent || '', /마지막 확인/);
  } finally {await h.close();}
});

test('AI status ignores a stale request after StrictMode restarts the effect and aborts on unmount', async () => {
  const signals: AbortSignal[] = [];
  let resolveStale!: (value: Response) => void;
  const h = await mount((async (_path: any, init: RequestInit) => {
    signals.push(init.signal as AbortSignal);
    return signals.length === 1 ? await new Promise<Response>(resolve => {resolveStale = resolve;}) : response('ready');
  }) as typeof fetch, true);
  try {
    assert.equal(signals.length, 2);
    assert.equal(signals[0].aborted, true);
    assert.equal(status(), 'AI 사용 가능');
    await act(async () => {resolveStale(response('unavailable')); await tick();});
    assert.equal(status(), 'AI 사용 가능');
  } finally {await h.close();}

  let signal!: AbortSignal;
  const pending = await mount((async (_path: any, init: RequestInit) => {
    signal = init.signal as AbortSignal;
    return await new Promise<Response>((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true}));
  }) as typeof fetch);
  assert.equal(status(), 'AI 연결 확인 중');
  await pending.close();
  assert.equal(signal.aborted, true);
});

test('AI status only turns green for a ready RunPod target', async () => {
  for (const value of [{}, {health: {status: 'unconfigured'}}, {health: {status: 'ready'}},
    {health: {status: 'ready', target: 'local'}}, {health: {status: 'ready', target: 'remote'}},
    {health: {status: 'ready', target: 'unconfigured'}}, {health: {status: 'unavailable', target: 'runpod'}},
    {health: {status: 'ready', target: 'RUNPOD'}}, {health: {status: 'ready', target: ['runpod']}}]) {
    const h = await mount((async () => new Response(JSON.stringify(value))) as typeof fetch);
    try {assert.equal(status(), 'AI 일시 중단');}
    finally {await h.close();}
  }
});

test('AI status rechecks on visible return and focus without overlapping requests', async () => {
  const signals: AbortSignal[] = [];
  const pending: ((value: Response) => void)[] = [];
  let visibility = 'visible';
  const h = await mount((async (_path: any, init: RequestInit) => {
    signals.push(init.signal as AbortSignal);
    return signals.length === 1 ? response('ready') : await new Promise<Response>(resolve => pending.push(resolve));
  }) as typeof fetch, false, () => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => visibility});
  });
  try {
    assert.equal(status(), 'AI 사용 가능');
    await act(async () => {
      visibility = 'hidden'; document.dispatchEvent(new dom.window.Event('visibilitychange'));
      window.dispatchEvent(new dom.window.Event('focus')); await tick();
    });
    assert.equal(signals.length, 1);
    await act(async () => {
      visibility = 'visible'; document.dispatchEvent(new dom.window.Event('visibilitychange'));
      window.dispatchEvent(new dom.window.Event('focus')); await tick();
    });
    assert.equal(signals.length, 2); assert.equal(status(), 'AI 연결 확인 중');
    await act(async () => {pending.shift()!(response('ready', 'local')); await tick();});
    assert.equal(status(), 'AI 일시 중단');
    await act(async () => {window.dispatchEvent(new dom.window.Event('focus')); await tick();});
    assert.equal(signals.length, 3); assert.equal(status(), 'AI 연결 확인 중');
    await act(async () => {pending.shift()!(response('ready')); await tick();});
    assert.equal(status(), 'AI 사용 가능');
  } finally {await h.close();}
  let lateCalls = 0;
  globalThis.fetch = (async () => {lateCalls++; return response('ready');}) as typeof fetch;
  try {
    await act(async () => {
      window.dispatchEvent(new dom.window.Event('focus'));
      document.dispatchEvent(new dom.window.Event('visibilitychange')); await tick();
    });
    assert.equal(lateCalls, 0, 'unmount removes focus and visibility listeners');
  } finally {globalThis.fetch = originalFetch;}
});

test('AI status fails closed for malformed responses and network failures', async () => {
  for (const fetcher of [
    async () => {throw new Error('network unavailable');},
    async () => new Response('invalid JSON'),
    async () => new Response('null'),
    async () => new Response(JSON.stringify({health: {status: ['ready'], target: 'runpod'}})),
  ]) {
    const h = await mount(fetcher as typeof fetch);
    try {assert.equal(status(), 'AI 일시 중단'); assert.equal(document.querySelector('.ai-status-badge')?.getAttribute('data-status'), 'unavailable');}
    finally {await h.close();}
  }
});

test('AI status times out a pending health request and allows another check', async () => {
  let timeout!: () => void;
  let signal!: AbortSignal;
  const h = await mount((async (_path: any, init: RequestInit) => {
    signal = init.signal as AbortSignal;
    return await new Promise<Response>((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('timeout')), {once: true}));
  }) as typeof fetch, false, () => {
    const schedule = dom.window.setTimeout.bind(dom.window);
    dom.window.setTimeout = ((callback: TimerHandler, delay?: number, ...args: any[]) => {
      if (delay === 6500 && typeof callback === 'function') timeout = () => callback(...args);
      return schedule(callback, delay, ...args);
    }) as typeof dom.window.setTimeout;
  });
  try {
    assert.equal(status(), 'AI 연결 확인 중');
    await act(async () => {timeout(); await tick();});
    assert.equal(signal.aborted, true);
    assert.equal(status(), 'AI 일시 중단');
    await click(button('AI 일시 중단'));
    assert.equal(button('다시 확인').disabled, false);
  } finally {await h.close();}
});

test.after(() => {
  dom.window.close(); globalThis.fetch = originalFetch;
  for (const [key, descriptor] of previous) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete (globalThis as any)[key];
  }
});
