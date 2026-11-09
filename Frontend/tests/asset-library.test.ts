import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {JSDOM} from 'jsdom';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const require = createRequire(new URL('../../package.json', import.meta.url));
const compiled = buildSync({stdin: {contents: "export {UploadLibrary,AttachUploadDialog} from './app/upload-library'; export {AuthProvider} from './app/auth';", resolveDir: frontendRoot, sourcefile: 'asset-library-tests.ts'}, absWorkingDir: frontendRoot,
  bundle: true, platform: 'node', format: 'cjs', packages: 'external', loader: {'.css': 'empty'}, write: false, logLevel: 'silent'}).outputFiles[0].text;
const globals = ['window', 'document', 'navigator', 'location', 'localStorage', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'Element', 'Node',
  'NodeFilter', 'SVGElement', 'HTMLButtonElement', 'DocumentFragment', 'MutationObserver', 'Event', 'CustomEvent', 'File', 'FileReader',
  'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT'];
const previous = new Map(globals.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const originalFetch = globalThis.fetch;
let dom: JSDOM;
function installDom() {
  dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {url: 'http://localhost/', pretendToBeVisual: true});
  for (const key of globals) {
    if (key === 'IS_REACT_ACT_ENVIRONMENT') Object.defineProperty(globalThis, key, {value: true, writable: true, configurable: true});
    else if (key in dom.window) Object.defineProperty(globalThis, key, {value: (dom.window as any)[key], writable: true, configurable: true});
  }
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
}
installDom();
const {createElement, act, StrictMode} = require('react');
const {createRoot} = require('react-dom/client');
const module = {exports: {} as any};
new Function('require', 'module', 'exports', compiled)(require, module, module.exports);
const {UploadLibrary, AttachUploadDialog, AuthProvider} = module.exports;
const tick = () => new Promise(resolve => setImmediate(resolve));
const page = (assets: any[] = [], folders: any[] = [], folder_id: string | null = null, next: string | null = null) =>
  ({space: {id: 'personal', kind: 'personal', name: '내 자료'}, folder_id, folders, assets, next});
const asset = (id: string, folder_id: string | null = null) => ({id, name: id + '.txt', format: 'txt', folder_id,
  size_bytes: 8, has_original: true, pending_delete: false, processing_status: 'ready', usages: []});
function deferred() {
  let resolve!: (value: any) => void;
  const promise = new Promise<any>(done => {resolve = done;});
  return {promise, resolve};
}
type TestMember = {id: string; email: string; name: string; role: 'member'};
async function mount(handler: (url: URL, body: any, method: string) => any, component?: any, authenticated: boolean | (() => TestMember) = false) {
  dom.window.close(); installDom();
  const calls: {url: URL; body: any; method: string}[] = [];
  globalThis.fetch = (async (path: any, init: RequestInit = {}) => {
    const url = new URL(String(path), location.href), body = init.body ? JSON.parse(String(init.body)) : undefined;
    const method = init.method || 'GET'; calls.push({url, body, method});
    assert.equal(init.credentials, 'same-origin');
    const result = authenticated && url.pathname === '/api/auth/me'
      ? {enabled: true, authenticated: true, user: typeof authenticated === 'function' ? authenticated() : {id: 'me', email: 'me@example.com', name: '사용자', role: 'member'}}
      : await handler(url, body, method);
    return result instanceof Response ? result : new Response(JSON.stringify(result), {headers: {'Content-Type': 'application/json'}});
  }) as typeof fetch;
  const root = createRoot(document.getElementById('root'));
  const content = component || createElement(UploadLibrary);
  await act(async () => {root.render(authenticated ? createElement(AuthProvider, null, content) : content); await tick();});
  return {calls, close: async () => {await act(async () => {root.unmount(); await new Promise(done => setTimeout(done, 0));}); globalThis.fetch = originalFetch;}};
}
function button(text: string) {
  const value = Array.from(document.querySelectorAll('button')).find(item => item.textContent?.trim() === text);
  assert.ok(value, 'Missing button: ' + text); return value;
}
function labelled(label: string) {
  const value = document.querySelector<HTMLElement>('[aria-label="' + label + '"]');
  assert.ok(value, 'Missing element: ' + label); return value;
}
async function click(value: HTMLElement) {await act(async () => {value.click(); await tick();});}
async function change(value: HTMLInputElement | HTMLSelectElement, text: string) {
  await act(async () => {
    const prototype = value.tagName === 'SELECT' ? dom.window.HTMLSelectElement.prototype : dom.window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(value, text);
    value.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    value.dispatchEvent(new dom.window.Event('change', {bubbles: true})); await tick();
  });
}
async function submit(form: HTMLFormElement) {
  await act(async () => {form.dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true})); await tick();});
}
async function waitFor(check: () => boolean) {
  for (let i = 0; i < 100 && !check(); i++) await act(async () => {await new Promise(done => setTimeout(done, 5));});
  assert.ok(check(), 'Timed out waiting for DOM update');
}
async function chooseFiles(files: File[]) {
  const value = labelled('자료함에 올릴 파일');
  Object.defineProperty(value, 'files', {value: files, configurable: true});
  await act(async () => {value.dispatchEvent(new dom.window.Event('change', {bubbles: true})); await tick();});
}

test('library folders support nesting, rename, safe moves and empty deletion', async () => {
  let folders = [{id: 'a', name: '업무', parent_id: null as string | null}, {id: 'child', name: '하위', parent_id: 'a'}, {id: 'b', name: '보관', parent_id: null}];
  let current: string | null = null;
  const h = await mount((url, body, method) => {
    if (url.pathname.endsWith('/library')) {current = url.searchParams.get('folder_id'); return page([], folders, current);}
    assert.ok(url.pathname.endsWith('/folders'));
    if (method === 'POST') {assert.deepEqual(body, {name: '월간', parent_id: current}); folders = [...folders, {id: 'new', name: body.name, parent_id: body.parent_id}];}
    else if (method === 'PATCH') folders = folders.map(folder => folder.id === body.id ? {...folder, ...body} : folder);
    else {assert.equal(method, 'DELETE'); folders = folders.filter(folder => folder.id !== url.searchParams.get('id'));}
    return {deleted: true};
  });
  try {
    assert.equal(document.querySelector('h1')?.textContent, '자료함');
    await click(labelled('업무 폴더 이름 변경'));
    await change(document.querySelector('[role=dialog] input')!, '업무 자료'); await click(button('이름 변경'));
    assert.ok(labelled('업무 자료 폴더 열기'));
    await click(labelled('업무 자료 폴더 이동'));
    const select = labelled('이동할 위치') as HTMLSelectElement;
    assert.equal(select.querySelector('option[value=a]'), null); assert.equal(select.querySelector('option[value=child]'), null);
    assert.equal(button('여기로 이동').disabled, true);
    await change(select, 'b'); await click(button('여기로 이동'));
    assert.equal(document.querySelector('[aria-label="업무 자료 폴더 열기"]'), null);
    await click(labelled('보관 폴더 열기')); await click(labelled('업무 자료 폴더 열기'));
    assert.match(labelled('자료함 위치').textContent!, /내 자료보관업무 자료/);
    await click(button('새 폴더')); await change(document.querySelector('[role=dialog] input')!, '  월간  '); await click(button('폴더 만들기'));
    assert.ok(labelled('월간 폴더 열기'));
    await click(labelled('월간 폴더 삭제')); await click(button('폴더 삭제'));
    assert.equal(document.querySelector('[aria-label="월간 폴더 열기"]'), null);
    assert.deepEqual(h.calls.find(call => call.method === 'POST')?.body, {name: '월간', parent_id: 'a'});
  } finally {await h.close();}
});

test('moving a file keeps its id, preview and workflow usage', async () => {
  const folders = [{id: 'target', name: '보관', parent_id: null}];
  let item = {...asset('report'), usages: [{kind: 'workflow', id: 'workflow', name: '월말 정산'}]};
  const h = await mount((url, body) => {
    if (url.pathname.endsWith('/library')) {const folder = url.searchParams.get('folder_id'); return page(item.folder_id === folder ? [item] : [], folders, folder);}
    if (url.pathname.endsWith('/asset_move')) {assert.deepEqual(body, {id: 'report', folder_id: 'target'}); item = {...item, folder_id: body.folder_id}; return {asset: item};}
    assert.equal(url.pathname, '/api/agent-builder/asset_preview'); assert.equal(url.searchParams.get('id'), 'report');
    return {text: '보고서 원문', columns: [], rows: []};
  });
  try {
    await click(labelled('report.txt 이동')); await change(labelled('이동할 위치') as HTMLSelectElement, 'target'); await click(button('여기로 이동'));
    assert.equal(document.querySelector('.asset-file-list article'), null);
    await click(labelled('보관 폴더 열기'));
    assert.match(labelled('자료 사용처').textContent!, /월말 정산.*저장된 워크플로우/);
    await click(button('내용 보기'));
    assert.match(document.querySelector('[role=dialog]')!.textContent!, /보고서 원문/);
    assert.equal(document.querySelector('[role=dialog] a')?.getAttribute('href'), '/api/agent-builder/original?id=report');
    assert.equal(h.calls.some(call => /upload/.test(call.url.pathname)), false);
  } finally {await h.close();}
});

test('multi-upload is sequential, fixes the folder destination, and retries only failed files', async () => {
  const pending = deferred(), folders = [{id: 'target', name: '업무', parent_id: null}];
  const uploaded: any[] = [], attempts: string[] = [];
  let active = 0, maxActive = 0;
  const h = await mount(async (url, body) => {
    if (url.pathname.endsWith('/library')) return page(uploaded, folders, url.searchParams.get('folder_id'));
    assert.equal(url.pathname, '/api/agent-builder/library_upload'); assert.equal(body.folder_id, 'target');
    active++; maxActive = Math.max(active, maxActive); attempts.push(body.fileName);
    try {
      if (attempts.length === 1) await pending.promise;
      if (body.fileName === 'second.txt' && attempts.filter(name => name === body.fileName).length === 1)
        return new Response(JSON.stringify({error: '두 번째 파일 연결 실패'}), {status: 503});
      uploaded.push({...asset(body.fileName), folder_id: 'target'}); return {asset: uploaded.at(-1)};
    } finally {active--;}
  });
  try {
    await click(labelled('업무 폴더 열기'));
    await chooseFiles([new File(['first'], 'first.txt'), new File(['second'], 'second.txt'), new File(['third'], 'third.txt')]);
    await waitFor(() => attempts.length === 1);
    assert.equal(button('내 자료').disabled, true); assert.equal(button('파일 올리기').disabled, true);
    assert.equal((labelled('현재 폴더에서 검색') as HTMLInputElement).disabled, true);
    await click(button('내 자료')); assert.match(labelled('자료함 위치').textContent!, /업무/);
    await act(async () => {pending.resolve({}); await tick();});
    await waitFor(() => !!document.querySelector('[aria-label="second.txt 업로드 다시 시도"]') && !button('내 자료').disabled);
    assert.deepEqual(attempts, ['first.txt', 'second.txt', 'third.txt']); assert.equal(maxActive, 1);
    assert.equal(document.querySelectorAll('.asset-upload-queue li[data-state=done]').length, 2);
    await click(button('실패한 파일 다시 시도'));
    await waitFor(() => document.querySelectorAll('.asset-upload-queue li[data-state=done]').length === 3 && !button('내 자료').disabled);
    assert.deepEqual(attempts, ['first.txt', 'second.txt', 'third.txt', 'second.txt']);
    assert.equal(h.calls.filter(call => call.url.pathname.endsWith('/library_upload')).length, 4);
  } finally {await h.close();}
});

test('upload navigation guard protects all queued files immediately and releases after completion in StrictMode', async () => {
  const pending = deferred(), attempts: string[] = [];
  let canLeave = () => true;
  const h = await mount(async (url, body) => {
    if (url.pathname.endsWith('/library')) return page();
    assert.equal(url.pathname, '/api/agent-builder/library_upload'); attempts.push(body.fileName);
    if (attempts.length === 1) await pending.promise;
    return {asset: asset(body.fileName)};
  }, createElement(StrictMode, null, createElement(UploadLibrary, {onCanLeaveChange(value: () => boolean) {canLeave = value;}})));
  try {
    assert.equal(canLeave(), true);
    const idleUnload = new dom.window.Event('beforeunload', {cancelable: true}); window.dispatchEvent(idleUnload);
    assert.equal(idleUnload.defaultPrevented, false);
    const picker = labelled('자료함에 올릴 파일');
    Object.defineProperty(picker, 'files', {value: [new File(['1'], 'first.txt'), new File(['2'], 'second.txt'), new File(['3'], 'third.txt')], configurable: true});
    await act(async () => {
      picker.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
      assert.equal(canLeave(), false, 'enqueue blocks navigation before React publishes the uploading state');
      await tick();
    });
    await waitFor(() => attempts.length === 1);
    assert.match(document.body.textContent!, /모든 파일의 업로드가 끝난 뒤 이동/);
    const activeUnload = new dom.window.Event('beforeunload', {cancelable: true}); window.dispatchEvent(activeUnload);
    assert.equal(activeUnload.defaultPrevented, true);
    await act(async () => {pending.resolve({}); await tick();});
    await waitFor(() => !button('파일 올리기').disabled);
    assert.deepEqual(attempts, ['first.txt', 'second.txt', 'third.txt']); assert.equal(canLeave(), true);
    const finishedUnload = new dom.window.Event('beforeunload', {cancelable: true}); window.dispatchEvent(finishedUnload);
    assert.equal(finishedUnload.defaultPrevented, false);
  } finally {await h.close();}
  assert.equal(canLeave(), true, 'unmount clears the parent guard');
});

test('completed uploads release navigation while the follow-up library read is still pending', async () => {
  const refresh = deferred(); let reads = 0, canLeave = () => true;
  const h = await mount((url, body) => {
    if (url.pathname.endsWith('/library')) return ++reads === 1 ? page() : refresh.promise;
    assert.equal(url.pathname, '/api/agent-builder/library_upload'); return {asset: asset(body.fileName)};
  }, createElement(UploadLibrary, {onCanLeaveChange(value: () => boolean) {canLeave = value;}}));
  try {
    await chooseFiles([new File(['done'], 'complete.txt')]);
    await waitFor(() => reads === 2 && document.querySelectorAll('.asset-upload-queue li[data-state=done]').length === 1);
    assert.match(document.body.textContent!, /자료를 불러오는 중/);
    assert.equal(canLeave(), true, 'a delayed GET must not keep the write guard locked');
    const event = new dom.window.Event('beforeunload', {cancelable: true}); window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
  } finally {
    await h.close();
    await act(async () => {refresh.resolve(page([asset('complete')])); await tick();});
  }
});

test('team space changes cannot unmount an active upload queue even before a disabled control stops the event', async () => {
  const pending = deferred(), attempts: string[] = [];
  const h = await mount(async (url, body) => {
    if (url.pathname === '/api/teams') return {teams: [{id: 'team', name: '운영팀', role: 'editor'}]};
    if (url.pathname === '/api/team-invitations') return {invitations: []};
    if (url.pathname.endsWith('/library')) return page();
    assert.equal(url.pathname, '/api/agent-builder/library_upload'); attempts.push(body.fileName);
    if (attempts.length === 1) await pending.promise;
    return {asset: asset(body.fileName)};
  }, undefined, true);
  try {
    await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
    await chooseFiles([new File(['1'], 'first.txt'), new File(['2'], 'second.txt'), new File(['3'], 'third.txt')]);
    await waitFor(() => attempts.length === 1);
    const space = labelled('자료 공간 선택') as HTMLSelectElement;
    assert.equal(space.disabled, true);
    await change(space, 'team');
    assert.equal(h.calls.some(call => call.url.pathname === '/api/teams/team/library/library'), false);
    assert.equal(document.querySelectorAll('.asset-upload-queue li').length, 3);
    assert.match(document.body.textContent!, /모든 파일의 업로드가 끝난 뒤 이동/);
    await act(async () => {pending.resolve({}); await tick();}); await waitFor(() => !button('파일 올리기').disabled);
    assert.deepEqual(attempts, ['first.txt', 'second.txt', 'third.txt']);
    await change(labelled('자료 공간 선택') as HTMLSelectElement, 'team');
    assert.ok(h.calls.some(call => call.url.pathname === '/api/teams/team/library/library'));
  } finally {await h.close();}
});

test('account changes never send pending files or a late file read into the next account', async () => {
  for (const phase of ['request', 'file-read']) {
    const pending = deferred(), attempts: {account: string; name: string}[] = [];
    let account = 'first-account', reader: any;
    const h = await mount(async (url, body) => {
      if (url.pathname === '/api/teams') return {teams: []};
      if (url.pathname === '/api/team-invitations') return {invitations: []};
      if (url.pathname.endsWith('/library')) return page();
      assert.equal(url.pathname, '/api/agent-builder/library_upload');
      attempts.push({account, name: body.fileName});
      if (account === 'first-account') await pending.promise;
      return {asset: asset(body.fileName)};
    }, undefined, () => ({id: account, email: account + '@example.com', name: account, role: 'member'}));
    const originalFileReader = globalThis.FileReader;
    try {
      await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
      if (phase === 'file-read') globalThis.FileReader = class {
        result: string | null = null;
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        readAsDataURL() {reader = this;}
      } as any;
      await chooseFiles([new File(['1'], 'old-first.txt'), new File(['2'], 'old-second.txt'), new File(['3'], 'old-third.txt')]);
      await waitFor(() => phase === 'request' ? attempts.length === 1 : !!reader);
      account = 'next-account';
      await act(async () => {window.dispatchEvent(new dom.window.Event('focus')); await tick();});
      await waitFor(() => localStorage.getItem('a4b-auth-member') === 'next-account' && !button('파일 올리기').disabled);
      globalThis.FileReader = originalFileReader;
      await act(async () => {
        if (phase === 'file-read') {reader.result = 'data:text/plain;base64,MQ=='; reader.onload();}
        else pending.resolve({});
        await tick();
      });
      assert.equal(document.querySelector('.asset-upload-queue'), null);
      assert.deepEqual(attempts, phase === 'request' ? [{account: 'first-account', name: 'old-first.txt'}] : []);
      await chooseFiles([new File(['new'], 'next-account.txt')]);
      await waitFor(() => !button('파일 올리기').disabled);
      assert.deepEqual(attempts.at(-1), {account: 'next-account', name: 'next-account.txt'});
      assert.equal(attempts.some(attempt => attempt.account === 'next-account' && attempt.name.startsWith('old-')), false);
    } finally {globalThis.FileReader = originalFileReader; await h.close();}
  }
});

test('drag and drop validates each file and never sends invalid files', async () => {
  const uploads: any[] = [];
  const h = await mount((url, body) => {
    if (url.pathname.endsWith('/library')) return page(uploads.map((_item, index) => asset('uploaded-' + index)));
    assert.equal(url.pathname, '/api/agent-builder/library_upload'); uploads.push(body); return {asset: asset('ok')};
  });
  try {
    const drop = new dom.window.Event('drop', {bubbles: true, cancelable: true});
    const oversized = new File(['large'], 'large.pdf'); Object.defineProperty(oversized, 'size', {value: 8 * 1024 * 1024 + 1});
    Object.defineProperty(drop, 'dataTransfer', {value: {files: [new File(['ok'], 'ok.TXT'), new File(['bad'], 'bad.exe'), oversized]}});
    await act(async () => {labelled('파일 업로드').dispatchEvent(drop); await tick();});
    await waitFor(() => document.querySelectorAll('.asset-upload-queue li[data-state=done]').length === 1 && !button('파일 올리기').disabled);
    assert.equal(uploads.length, 1); assert.equal(uploads[0].fileName, 'ok.TXT'); assert.equal(uploads[0].folder_id, null);
    assert.equal(Buffer.from(uploads[0].data, 'base64').toString(), 'ok');
    assert.match(document.querySelector('.asset-upload-queue')!.textContent!, /지원하는 문서/);
    assert.match(document.querySelector('.asset-upload-queue')!.textContent!, /8MB 이하/);
    assert.equal(document.querySelector('.asset-upload-queue button[aria-label$="업로드 다시 시도"]'), null);
    await click(button('목록 닫기')); assert.equal(document.querySelector('.asset-upload-queue'), null);
  } finally {await h.close();}
});

test('folder navigation and search ignore stale responses, and pagination deduplicates assets', async () => {
  const oldFolder = deferred(), oldSearch = deferred(), folders = [{id: 'a', name: '업무', parent_id: null}];
  const h = await mount(url => {
    assert.ok(url.pathname.endsWith('/library'));
    if (url.searchParams.get('folder_id') === 'a') return oldFolder.promise;
    if (url.searchParams.get('q') === '이전') return oldSearch.promise;
    if (url.searchParams.get('q') === '최신') return page([asset('latest')], folders);
    return url.searchParams.has('before') ? page([asset('root'), asset('next')], folders) : page([asset('root')], folders, null, 'cursor');
  });
  try {
    await click(labelled('업무 폴더 열기')); await click(button('내 자료'));
    await act(async () => {oldFolder.resolve(page([asset('stale')], folders, 'a')); await tick();});
    assert.match(document.querySelector('.asset-file-list')!.textContent!, /root.txt/); assert.doesNotMatch(document.body.textContent!, /stale.txt/);
    await click(button('자료 더 보기')); assert.equal(document.querySelectorAll('.asset-file-list article').length, 2);
    await change(labelled('현재 폴더에서 검색') as HTMLInputElement, '이전'); await submit(document.querySelector('[role=search]')!);
    await change(labelled('현재 폴더에서 검색') as HTMLInputElement, '최신'); await submit(document.querySelector('[role=search]')!);
    await act(async () => {oldSearch.resolve(page([asset('old-search')], folders)); await tick();});
    assert.equal(document.querySelectorAll('.asset-file-list article').length, 1); assert.match(document.querySelector('.asset-file-list')!.textContent!, /latest.txt/);
    assert.doesNotMatch(document.body.textContent!, /old-search.txt/);
  } finally {await h.close();}
});

test('folder mutation locks navigation, preserves validation failures and retries once', async () => {
  const pending = deferred(), folders = [{id: 'a', name: '업무', parent_id: null}]; let attempts = 0;
  const h = await mount(async (url, body) => {
    if (url.pathname.endsWith('/library')) return page([], folders, url.searchParams.get('folder_id'));
    assert.equal(url.pathname, '/api/agent-builder/folders'); attempts++;
    if (attempts === 1) return new Response(JSON.stringify({error: '같은 이름의 폴더가 있습니다.'}), {status: 400});
    await pending.promise; folders.push({id: 'created', name: body.name, parent_id: null}); return {folder: folders.at(-1)};
  });
  try {
    await click(button('새 폴더')); await change(document.querySelector('[role=dialog] input')!, '자료'); await click(button('폴더 만들기'));
    assert.match(document.querySelector('[role=dialog] [role=alert]')!.textContent!, /같은 이름/);
    assert.equal((document.querySelector('[role=dialog] input') as HTMLInputElement).value, '자료');
    await click(button('폴더 만들기'));
    assert.equal(button('내 자료').disabled, true); assert.equal(button('취소').disabled, true);
    await submit(document.querySelector('[role=dialog] form')!); assert.equal(attempts, 2);
    await act(async () => {pending.resolve({}); await tick();});
    assert.equal(document.querySelector('[role=dialog]'), null); assert.ok(labelled('자료 폴더 열기'));
  } finally {await h.close();}
});

test('legacy attachment chooser still selects stable file ids from all folders', async () => {
  let selected = '';
  const h = await mount(url => {
    assert.equal(url.pathname, '/api/agent-builder/assets'); return {assets: [asset('nested', 'folder')], next: null};
  }, createElement(AttachUploadDialog, {mode: 'library', onLibrary() {}, onClose() {}, onNew() {}, onSelect(id: string) {selected = id;}, connected: []}));
  try {
    assert.match(document.querySelector('[role=dialog]')!.textContent!, /자료함에서 선택/);
    await click(document.querySelector('.as-resource')!); assert.equal(selected, 'nested');
  } finally {await h.close();}
});

test('StrictMode discards its first library load after restarting the effect', async () => {
  const stale = deferred(); let calls = 0;
  const h = await mount(() => ++calls === 1 ? stale.promise : page([asset('current')]), createElement(StrictMode, null, createElement(UploadLibrary)));
  try {
    assert.equal(calls, 2);
    await act(async () => {stale.resolve(page([asset('stale')])); await tick();});
    assert.match(document.querySelector('.asset-file-list')!.textContent!, /current.txt/); assert.doesNotMatch(document.body.textContent!, /stale.txt/);
  } finally {await h.close();}
});

test('team features require authentication while personal files remain available', async () => {
  const h = await mount(url => {assert.equal(url.pathname, '/api/agent-builder/library'); return page([asset('personal')]);});
  try {
    assert.match(document.body.textContent!, /팀 자료함은 로그인 후/);
    assert.match(document.querySelector('.asset-file-list')!.textContent!, /personal.txt/);
    assert.equal(document.querySelector('[aria-label="자료 공간 선택"]'), null);
    assert.equal(h.calls.some(call => call.url.pathname.startsWith('/api/teams')), false);
  } finally {await h.close();}
});

test('team creation makes its creator an administrator and prevents duplicate submission', async () => {
  const pending = deferred(); const teams: any[] = [];
  const h = await mount(async (url, body, method) => {
    if (url.pathname === '/api/teams') {
      if (method === 'GET') return {teams};
      assert.deepEqual(body, {name: '운영팀'}); await pending.promise;
      const team = {id: 'new-team', name: body.name, role: 'admin'}; teams.push(team); return {team};
    }
    if (url.pathname === '/api/team-invitations') return {invitations: []};
    assert.ok(url.pathname.endsWith('/library')); return page();
  }, undefined, true);
  try {
    await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
    await click(button('팀 만들기')); await change(document.querySelector('[role=dialog] input')!, '  운영팀  '); await click(button('팀 생성'));
    assert.equal(button('취소').disabled, true); assert.equal((labelled('자료 공간 선택') as HTMLSelectElement).disabled, true);
    await submit(document.querySelector('[role=dialog] form')!);
    assert.equal(h.calls.filter(call => call.url.pathname === '/api/teams' && call.method === 'POST').length, 1);
    await act(async () => {pending.resolve({}); await tick();});
    assert.equal(document.querySelector('[role=dialog]'), null); assert.equal((labelled('자료 공간 선택') as HTMLSelectElement).value, 'new-team');
    assert.ok(button('팀 관리')); assert.match(document.querySelector('.team-library-role')!.textContent!, /관리자/);
    assert.ok(h.calls.some(call => call.url.pathname === '/api/teams/new-team/library/library'));
  } finally {await h.close();}
});

test('viewer team files expose reading and team originals while editor files expose mutations', async () => {
  for (const role of ['viewer', 'editor']) {
    const h = await mount((url, body, method) => {
      if (url.pathname === '/api/teams') return {teams: [{id: 'team', name: '공유팀', role}]};
      if (url.pathname === '/api/team-invitations') return {invitations: []};
      if (url.pathname.endsWith('/asset_preview')) {assert.equal(url.pathname, '/api/teams/team/library/asset_preview'); return {text: '팀 원문', columns: [], rows: []};}
      if (url.pathname.endsWith('/folders')) {assert.equal(role, 'editor'); assert.equal(method, 'POST'); assert.equal(url.pathname, '/api/teams/team/library/folders'); assert.deepEqual(body, {name: '팀 폴더', parent_id: null}); return {folder: {id: 'folder'}};}
      assert.ok(url.pathname.endsWith('/library'));
      return page(url.pathname.startsWith('/api/teams/') ? [asset('shared')] : [], [{id: 'folder', name: '폴더', parent_id: null}]);
    }, undefined, true);
    try {
      await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
      await change(labelled('자료 공간 선택') as HTMLSelectElement, 'team');
      assert.equal(document.querySelector('.team-library-space-actions')!.textContent!.includes('팀 관리'), false);
      if (role === 'viewer') {
        assert.equal(document.querySelector('[aria-label="자료함에 올릴 파일"]'), null);
        assert.equal(document.querySelector('[aria-label="shared.txt 이동"]'), null);
        assert.equal(document.querySelector('[aria-label="shared.txt 삭제"]'), null);
        assert.equal(document.querySelector('.asset-folder-actions'), null);
        assert.ok(labelled('폴더 폴더 열기'));
      } else {
        assert.ok(button('파일 올리기')); assert.ok(labelled('shared.txt 이동')); assert.ok(labelled('shared.txt 삭제'));
        await click(button('새 폴더')); await change(document.querySelector('[role=dialog] input')!, '팀 폴더'); await click(button('폴더 만들기'));
      }
      await click(button('내용 보기'));
      assert.match(document.querySelector('[role=dialog]')!.textContent!, /팀 원문/);
      assert.equal(document.querySelector('[role=dialog] a')?.getAttribute('href'), '/api/teams/team/library/original?id=shared');
      assert.equal(h.calls.some(call => call.url.pathname.endsWith('/members')), false);
    } finally {await h.close();}
  }
});

test('received email invitations can fail safely, then be accepted or declined', async () => {
  const teams: any[] = [], pending = deferred(); let attempts = 0;
  let invitations = [{id: 'yes', team_id: 'team', team_name: '운영팀', email: 'me@example.com', role: 'editor'},
    {id: 'no', team_id: 'other', team_name: '다른팀', email: 'me@example.com', role: 'viewer'}];
  const h = await mount(async url => {
    if (url.pathname === '/api/teams') return {teams};
    if (url.pathname === '/api/team-invitations') return {invitations};
    if (url.pathname.endsWith('/accept')) {
      attempts++; if (attempts === 1) return new Response(JSON.stringify({error: '초대 수락 실패'}), {status: 503});
      await pending.promise; const team = {id: 'team', name: '운영팀', role: 'editor'}; teams.push(team);
      invitations = invitations.filter(invitation => invitation.id !== 'yes'); return {team};
    }
    if (url.pathname.endsWith('/decline')) {invitations = []; return {declined: true};}
    assert.ok(url.pathname.endsWith('/library')); return page();
  }, undefined, true);
  try {
    await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
    await click(button('받은 초대 2')); await click(labelled('운영팀 초대 수락'));
    assert.match(document.querySelector('[role=dialog] [role=alert]')!.textContent!, /초대 수락 실패/);
    assert.equal((labelled('자료 공간 선택') as HTMLSelectElement).value, '');
    await click(labelled('운영팀 초대 수락')); await click(labelled('운영팀 초대 수락')); assert.equal(attempts, 2);
    await act(async () => {pending.resolve({}); await tick();});
    assert.equal((labelled('자료 공간 선택') as HTMLSelectElement).value, 'team'); assert.equal(document.querySelector('[role=dialog]'), null);
    await click(button('받은 초대 1')); await click(labelled('다른팀 초대 거절'));
    assert.match(document.querySelector('[role=dialog]')!.textContent!, /받은 초대가 없습니다/);
    assert.ok(h.calls.some(call => call.url.pathname === '/api/team-invitations/no/decline' && call.method === 'POST'));
  } finally {await h.close();}
});

test('team administrators invite emails, change roles, remove members and cancel invitations', async () => {
  let members = [{id: 'me', user_id: 'me', email: 'me@example.com', name: '관리자', role: 'admin'},
    {id: 'other', user_id: 'other', email: 'other@example.com', name: '동료', role: 'editor'}];
  let invitations: any[] = [];
  const h = await mount((url, body, method) => {
    if (url.pathname === '/api/teams') return {teams: [{id: 'team', name: '운영팀', role: 'admin'}]};
    if (url.pathname === '/api/team-invitations') return {invitations: []};
    if (url.pathname.endsWith('/library')) return page();
    if (url.pathname === '/api/teams/team/members') return {members, invitations};
    if (url.pathname === '/api/teams/team/invitations') {
      assert.equal(method, 'POST'); assert.deepEqual(body, {email: 'new@example.com', role: 'viewer'});
      invitations = [{id: 'invite', email: body.email, role: body.role}]; return {invitation: invitations[0]};
    }
    if (url.pathname.endsWith('/invitations/invite')) {assert.equal(method, 'DELETE'); invitations = []; return {deleted: true};}
    if (url.pathname.endsWith('/members/me')) return new Response(JSON.stringify({error: '마지막 관리자는 권한을 변경할 수 없습니다.'}), {status: 400});
    assert.equal(url.pathname, '/api/teams/team/members/other');
    if (method === 'PATCH') members = members.map(member => member.id === 'other' ? {...member, role: body.role} : member);
    else {assert.equal(method, 'DELETE'); members = members.filter(member => member.id !== 'other');}
    return {updated: true};
  }, undefined, true);
  try {
    await waitFor(() => !!document.querySelector('[aria-label="자료 공간 선택"]') && !button('팀 만들기').disabled);
    await change(labelled('자료 공간 선택') as HTMLSelectElement, 'team'); await click(button('팀 관리'));
    assert.match(document.querySelector('[role=dialog]')!.textContent!, /이메일은 발송하지 않습니다/);
    await change(document.querySelector('input[type=email]')!, 'new@example.com');
    const invitationRole = Array.from(document.querySelectorAll('label')).find(label => label.textContent?.startsWith('초대 권한'))!.querySelector('select')!;
    await change(invitationRole, 'viewer'); await click(button('팀 초대 만들기'));
    assert.ok(labelled('new@example.com 초대 취소'));
    await change(labelled('other@example.com 권한') as HTMLSelectElement, 'viewer');
    assert.equal((labelled('other@example.com 권한') as HTMLSelectElement).value, 'viewer');
    await change(labelled('me@example.com 권한') as HTMLSelectElement, 'viewer');
    assert.match(document.querySelector('[role=dialog] [role=alert]')!.textContent!, /마지막 관리자/);
    assert.equal((labelled('me@example.com 권한') as HTMLSelectElement).value, 'admin');
    await click(labelled('other@example.com 팀에서 제외'));
    assert.equal(document.querySelector('[aria-label="other@example.com 권한"]'), null);
    await click(labelled('new@example.com 초대 취소'));
    assert.equal(document.querySelector('[aria-label="보낸 팀 초대"]'), null);
    assert.equal(h.calls.some(call => /gmail|send-email|auth\/invite/.test(call.url.pathname)), false);
  } finally {await h.close();}
});

test('team attachment selection copies an independent snapshot and retains the dialog on failure', async () => {
  const pending = deferred(); let attempts = 0, selected = '';
  const h = await mount(async (url, body) => {
    if (url.pathname === '/api/teams') return {teams: [{id: 'team', name: '운영팀', role: 'viewer'}]};
    if (url.pathname === '/api/team-invitations') return {invitations: []};
    if (url.pathname === '/api/agent-builder/assets') return {assets: [], next: null};
    if (url.pathname === '/api/teams/team/library/assets') return {assets: [asset('shared')], next: null};
    assert.equal(url.pathname, '/api/teams/team/library/import'); assert.deepEqual(body, {asset_id: 'shared'});
    attempts++; if (attempts === 1) return new Response(JSON.stringify({error: '복사를 완료하지 못했습니다.'}), {status: 503});
    await pending.promise; return {asset: asset('personal-copy')};
  }, createElement(AttachUploadDialog, {mode: 'library', onLibrary() {}, onClose() {}, onNew() {}, onSelect(id: string) {selected = id;}, connected: []}), true);
  try {
    await waitFor(() => !!document.querySelector('[aria-label="연결할 자료 공간"] option[value=team]'));
    await change(labelled('연결할 자료 공간') as HTMLSelectElement, 'team');
    assert.match(document.querySelector('[role=dialog]')!.textContent!, /사본은 팀의 변경과 별개/);
    await click(document.querySelector('.as-resource')!);
    assert.equal(selected, ''); assert.match(document.querySelector('[role=dialog] [role=alert]')!.textContent!, /복사를 완료하지 못했습니다/);
    await click(document.querySelector('.as-resource')!); await click(document.querySelector('.as-resource')!);
    assert.equal(attempts, 2); assert.equal((labelled('연결할 자료 공간') as HTMLSelectElement).disabled, true);
    await act(async () => {pending.resolve({}); await tick();});
    assert.equal(selected, 'personal-copy'); assert.equal(attempts, 2);
  } finally {await h.close();}
});

test.after(() => {
  dom.window.close(); globalThis.fetch = originalFetch;
  for (const [key, descriptor] of previous) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete (globalThis as any)[key];
  }
});
