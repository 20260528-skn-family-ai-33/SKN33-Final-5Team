import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { createConnection, createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { deflateSync, gzipSync } from 'node:zlib';
import { validateFlow, type Execution, type Flow, type FlowNode, type Json } from '../lib/workflow';
import { MAX_BINARY_BYTES } from './binary';
import { WEBHOOK_JSON_BYTES, WEBHOOK_MULTIPART_BYTES } from './webhook-request';

const project = resolve(fileURLToPath(new URL('..', import.meta.url)));
const delay = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds));
const finished = (execution: Execution) => ['success', 'error', 'canceled'].includes(execution.status);

async function freePort(): Promise<number> {
  const socket = createServer();
  await new Promise<void>((resolve, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve); });
  const address = socket.address();
  assert.ok(address && typeof address !== 'string');
  await new Promise<void>((resolve, reject) => socket.close(error => error ? reject(error) : resolve()));
  return address.port;
}

class Server {
  child?: ChildProcess;
  output = '';
  constructor(readonly port: number, readonly directory: string) {}
  get base() { return `http://127.0.0.1:${this.port}`; }
  async start() {
    const environment: NodeJS.ProcessEnv = { ...process.env, PORT: String(this.port), HOST: '127.0.0.1', EASYFLOW_DATA_DIR: this.directory, EASYFLOW_WEBHOOK_RESPONSE_TIMEOUT_MS: '15000' };
    for (const key of Object.keys(environment)) if (/^N8N_/i.test(key)) delete environment[key];
    const child = this.child = spawn(process.execPath, ['--import', 'tsx', 'server/index.ts'], { cwd: project, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    child.stderr?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    let launchError: Error | undefined;
    child.once('error', error => { launchError = error; });
    const deadline = Date.now() + 12_000;
    while (Date.now() < deadline) {
      if (launchError) throw launchError;
      if (child.exitCode !== null || child.signalCode) throw new Error(`File Webhook server exited: ${this.output}`);
      try { const response = await fetch(this.base + '/api/health', { signal: AbortSignal.timeout(500) }); if (response.ok) return; } catch { /* Isolated server is starting. */ }
      await delay(40);
    }
    throw new Error(`File Webhook server did not start: ${this.output}`);
  }
  async stop() {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode) return;
    const exit = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
    child.kill('SIGTERM');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([exit, new Promise<never>((_, reject) => { timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(`File Webhook server did not shut down: ${this.output}`)); }, 4_000); })]);
      assert.equal(result.code, 0, this.output);
      assert.equal(result.signal, null);
    } finally { if (timer) clearTimeout(timer); }
  }
  async api(path: string, method = 'GET', data?: unknown): Promise<Json> {
    const response = await fetch(this.base + path, { method, headers: { 'Content-Type': 'application/json' }, ...(data !== undefined ? { body: JSON.stringify(data) } : {}), signal: AbortSignal.timeout(10_000) });
    const result = await response.json() as Json;
    assert.equal(response.status, 200, JSON.stringify(result));
    return result;
  }
  async activate(flow: Flow): Promise<{url: string; token: string}> {
    await this.api('/api/workflows', 'POST', flow);
    const active = await this.api('/api/activation', 'POST', { workflowId: flow.id, active: true });
    const details = await this.api('/api/webhook?workflowId=' + encodeURIComponent(flow.id));
    return { url: details.url, token: active.workflow.hookToken };
  }
  async execution(id: string): Promise<Execution> {
    const deadline = Date.now() + 12_000;
    let last: Execution | undefined;
    while (Date.now() < deadline) {
      last = (await this.api('/api/executions?id=' + encodeURIComponent(id))).execution;
      assert.ok(last);
      if (finished(last)) { assert.equal(last.status, 'success', last.error); return last; }
      await delay(30);
    }
    throw new Error(`File execution did not finish: ${JSON.stringify(last)}`);
  }
  // Read only this test's isolated database: large accepted files never inflate /api/executions list responses.
  count(flow: Flow): number {
    const database = new DatabaseSync(join(this.directory, 'easyflow.sqlite'), { readOnly: true });
    try { return Number(database.prepare('SELECT count(*) AS count FROM executions WHERE workflow_id=?').get(flow.id)?.count); }
    finally { database.close(); }
  }
  request(url: string, bytes: Buffer | string | FormData | ReadableStream<Uint8Array>, headers: Record<string, string> = {}) {
    const body = Buffer.isBuffer(bytes) ? new Uint8Array(bytes) : bytes;
    return fetch(url, { method: 'POST', headers, body, ...(bytes instanceof ReadableStream ? { duplex: 'half' } : {}), signal: AbortSignal.timeout(25_000), redirect: 'manual' });
  }
  async reject(flow: Flow, url: string, bytes: Buffer | string | FormData | ReadableStream<Uint8Array>, headers: Record<string, string>, status: number) {
    const before = this.count(flow);
    const response = await this.request(url, bytes, headers);
    const message = await response.text();
    assert.equal(response.status, status, message);
    const rejected = JSON.parse(message) as Json;
    assert.equal(typeof rejected.error, 'string', 'an error response must arrive as a complete readable JSON body');
    assert.ok(rejected.error.length > 0);
    assert.equal(response.headers.get('x-easyflow-execution-id'), null);
    assert.equal(this.count(flow), before, 'invalid input must fail before an execution is reserved');
  }
}

function stage(type: string, name: string, parameters: Json = {}): FlowNode {
  return { id: randomUUID(), name, type: `n8n-nodes-base.${type}`, typeVersion: type === 'webhook' ? 2.1 : type === 'respondToWebhook' ? 1.5 : 1, position: [0, 0], parameters };
}
function workflow(name: string, nodes: FlowNode[]): Flow {
  return { id: randomUUID(), name, nodes, connections: Object.fromEntries(nodes.slice(0, -1).map((node, index) => [node.name, { main: [[{ node: nodes[index + 1].name, type: 'main', index: 0 }]] }])), settings: { timezone: 'Asia/Seoul', executionOrder: 'v1' }, active: false };
}
function hook(options: Json = {}) { return stage('webhook', '파일 요청', { httpMethod: 'POST', path: 'file-' + randomUUID(), authentication: 'easyflowToken', responseMode: 'responseNode', options }); }
function metadataResponse() { return stage('respondToWebhook', '받은 정보 응답', { respondWith: 'json', responseBody: '={{ { body: $json.body, files: $binary, query: $json.query } }}', options: {} }); }
function fileResponse(property = 'data') { return stage('respondToWebhook', '파일 원문 응답', { respondWith: 'binary', responseDataSource: 'set', inputFieldName: property, options: {} }); }
function executionId(response: Response) { const id = response.headers.get('x-easyflow-execution-id'); assert.ok(id, 'accepted requests must expose a persisted execution id'); return id; }
function fileOf(execution: Execution, property = 'data'): Json { const file = execution.runs[0].input[0].__easyflowBinary?.[property]; assert.ok(file); return file; }
function assertFile(execution: Execution, property: string, bytes: Buffer, fileName?: string) {
  const file = fileOf(execution, property);
  assert.equal(file.fileSize, bytes.length);
  assert.deepEqual(Buffer.from(file.data, 'base64'), bytes);
  if (fileName) assert.equal(file.fileName, fileName);
}
function formFile(bytes: Buffer, field = 'data', fileName = '보낸 파일.bin', mimeType = 'application/octet-stream'): FormData {
  const data = new FormData();
  data.append(field, new Blob([new Uint8Array(bytes)], { type: mimeType }), fileName);
  return data;
}
type Part = { name: string; value: Buffer | string; fileName?: string; mimeType?: string; encoding?: string };
function multipart(parts: Part[], boundary = 'easyflow-' + randomUUID()) {
  const chunks: Buffer[] = [];
  for (const part of parts) {
    const disposition = `form-data; name="${part.name}"` + (part.fileName === undefined ? '' : `; filename="${part.fileName}"`);
    chunks.push(Buffer.from(`--${boundary}\r\nContent-Disposition: ${disposition}\r\n` + (part.mimeType ? `Content-Type: ${part.mimeType}\r\n` : '') + (part.encoding ? `Content-Transfer-Encoding: ${part.encoding}\r\n` : '') + '\r\n'));
    chunks.push(Buffer.from(part.value), Buffer.from('\r\n'));
  }
  chunks.push(Buffer.from(`--${boundary}--\r\n`));
  return { bytes: Buffer.concat(chunks), headers: { 'Content-Type': 'multipart/form-data; boundary=' + boundary } };
}

function headersOnlyRequest(url: string, headers: Record<string, string>): Promise<{status: number; headers: Json; body: string}> {
  return new Promise((resolve, reject) => {
    const request = httpRequest(url, { method: 'POST', headers, agent: false }, response => {
      const chunks: Buffer[] = [];
      response.on('data', chunk => chunks.push(Buffer.from(chunk)));
      response.once('error', reject);
      response.once('end', () => { resolve({ status: response.statusCode!, headers: response.headers, body: Buffer.concat(chunks).toString('utf8') }); request.destroy(); });
    });
    request.once('error', reject);
    request.setTimeout(2_000, () => request.destroy(new Error('request was parsed before early rejection')));
    request.flushHeaders();
  });
}

function stalledRequest(url: string): Promise<{bytes: Buffer; milliseconds: number}> {
  return new Promise((resolve, reject) => {
    const target = new URL(url), chunks: Buffer[] = [];
    const started = performance.now();
    const socket = createConnection({ host: target.hostname, port: Number(target.port), allowHalfOpen: true });
    const timer = setTimeout(() => { socket.destroy(); reject(new Error('an oversized stalled upload kept its socket open')); }, 2_000);
    socket.once('error', error => { clearTimeout(timer); reject(error); });
    socket.once('connect', () => socket.write(`POST ${target.pathname}${target.search} HTTP/1.1\r\nHost: ${target.host}\r\nContent-Type: application/octet-stream\r\nContent-Length: 100000000\r\nConnection: keep-alive\r\n\r\n`));
    socket.on('data', chunk => chunks.push(Buffer.from(chunk)));
    socket.once('end', () => { clearTimeout(timer); const milliseconds = performance.now() - started; socket.destroy(); resolve({ bytes: Buffer.concat(chunks), milliseconds }); });
  });
}

test('independent Webhook receives files through real HTTP, parses native bodies and keeps exact bytes after restart', { timeout: 150_000 }, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'easyflow-webhook-request-api-'));
  const server = new Server(await freePort(), directory);
  const durable: { id: string; property: string; bytes: Buffer; responseBytes?: Buffer }[] = [];
  try {
    await server.start();

    await t.test('v2.1 raw octet-stream and missing MIME automatically become data files with UTF-8 names', async () => {
      for (const mime of ['application/octet-stream', undefined]) {
        const bytes = Buffer.from([0, 128, 255, 13, 10, 65]);
        const flow = workflow('원시 파일 자동 수신', [hook(), fileResponse()]);
        const { url } = await server.activate(flow);
        const headers: Record<string, string> = { 'Content-Disposition': "attachment; filename=plain.bin; filename*=UTF-8''" + encodeURIComponent('요청 원문.bin') };
        if (mime) headers['Content-Type'] = mime;
        const response = await server.request(url, bytes, headers);
        assert.equal(response.status, 200, await response.clone().text());
        assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
        assert.equal(response.headers.get('content-type'), 'application/octet-stream');
        const id = executionId(response), execution = await server.execution(id);
        assert.deepEqual(execution.runs[0].input[0].body, {});
        assertFile(execution, 'data', bytes, '요청 원문.bin');
        assert.equal(fileOf(execution).mimeType, 'application/octet-stream');
        durable.push({ id, property: 'data', bytes, responseBytes: bytes });
      }
    });

    await t.test('multipart fields preserve business tokens and repeated values, while rawBody leaves files under their field names', async () => {
      const bytes = Buffer.from([0, 255, 13, 10, 128]);
      const data = formFile(bytes, 'attachment', '한글 문서.bin');
      data.append('title', '한글 제목'); data.append('tag', 'a'); data.append('tag', 'b'); data.append('token', 'ordinary-business-token'); data.append('nested[x]', 'flat');
      const flow = workflow('파일과 양식 수신', [hook({ rawBody: true }), metadataResponse()]);
      const { url } = await server.activate(flow);
      const response = await server.request(url + '&source=upload', data);
      assert.equal(response.status, 200);
      const body = await response.json() as Json;
      assert.deepEqual(body.body, { title: '한글 제목', tag: ['a', 'b'], token: 'ordinary-business-token', 'nested[x]': 'flat' });
      assert.deepEqual(body.query, { source: 'upload' });
      assert.deepEqual(Object.keys(body.files), ['attachment']);
      assert.equal(body.files.attachment.fileName, '한글 문서.bin');
      assert.equal(body.files.attachment.fileSize, bytes.length);
      assert.equal(body.files.attachment.data, undefined, 'metadata expressions cannot expose base64 file contents');
      const id = executionId(response), execution = await server.execution(id);
      assertFile(execution, 'attachment', bytes, '한글 문서.bin');
      assert.deepEqual(Object.keys(execution.runs[0].input[0].__easyflowBinary), ['attachment']);
      durable.push({ id, property: 'attachment', bytes });
    });

    await t.test('duplicate multipart file names lose [] and custom prefixes number all received files', async () => {
      const parts: Part[] = [
        { name: 'files[]', fileName: 'first.bin', mimeType: 'application/octet-stream', value: Buffer.from([0, 128]) },
        { name: 'files[]', fileName: 'second.bin', mimeType: 'application/octet-stream', value: Buffer.from([255, 13, 10]) },
        { name: 'other', fileName: 'empty.txt', mimeType: 'text/plain', value: Buffer.alloc(0) },
      ];
      for (const options of [{}, { binaryPropertyName: 'upload' }]) {
        const input = multipart(parts), flow = workflow('여러 첨부 이름', [hook(options), metadataResponse()]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, input.bytes, input.headers);
        assert.equal(response.status, 200);
        const result = await response.json() as Json;
        const keys = options.binaryPropertyName ? ['upload0', 'upload1', 'upload2'] : ['files0', 'files1', 'other'];
        assert.deepEqual(Object.keys(result.files), keys);
        const execution = await server.execution(executionId(response));
        for (const [index, part] of parts.entries()) assertFile(execution, keys[index], Buffer.from(part.value), part.fileName);
        assert.equal(result.files[keys[2]].fileSize, 0);
      }
    });

    await t.test('portable CSV example computes real rows for raw octet-stream, text/csv and multipart data uploads', async () => {
      const value = JSON.parse(await readFile(join(project, 'examples/요청으로_파일_받기.workflow.json'), 'utf8'));
      const flow = validateFlow({ ...value, id: randomUUID() });
      const { url } = await server.activate(flow);
      const bytes = Buffer.from('name,score\r\n"예시 고객, A",92\r\n예시 고객 B,68\r\n', 'utf8');
      const rows = [{ name: '예시 고객, A', score: '92' }, { name: '예시 고객 B', score: '68' }];
      for (const mime of ['application/octet-stream', 'text/csv', 'multipart/form-data']) {
        const response = await server.request(url, mime === 'multipart/form-data' ? formFile(bytes, 'data', '요청 표.csv', 'text/csv') : bytes, mime === 'multipart/form-data' ? {} : { 'Content-Type': mime, 'Content-Disposition': "attachment; filename*=UTF-8''" + encodeURIComponent('요청 표.csv') });
        assert.equal(response.status, 201, await response.clone().text());
        assert.equal(response.headers.get('x-easyflow-example'), 'csv-upload');
        assert.deepEqual(await response.json(), rows);
        const id = executionId(response), execution = await server.execution(id);
        assert.equal(execution.runs.length, 5);
        assert.ok(execution.runs.every(run => run.status === 'success'));
        assertFile(execution, 'data', bytes, '요청 표.csv');
        const responder = execution.runs.find(run => run.name === '읽은 표 돌려주기');
        assert.ok(responder);
        assert.deepEqual(responder.output[0], rows);
        assert.equal(responder.output[1][0].response.statusCode, 201);
        assert.deepEqual(responder.output[1][0].response.body, rows);
        durable.push({ id, property: 'data', bytes });
      }
    });

    await t.test('binaryData forces JSON wire bytes into a custom file and takes precedence over rawBody', async () => {
      const bytes = Buffer.from('{\r\n  "customer": "민지", "token": "ordinary"\r\n}\n', 'utf8');
      const flow = workflow('JSON도 파일로 수신', [hook({ binaryData: true, rawBody: true, binaryPropertyName: 'payload' }), fileResponse('payload')]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, bytes, { 'Content-Type': 'application/json', 'Content-Disposition': 'attachment; filename=payload.json' });
      assert.equal(response.status, 200);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
      const id = executionId(response), execution = await server.execution(id);
      assert.deepEqual(execution.runs[0].input[0].body, {});
      assert.deepEqual(Object.keys(execution.runs[0].input[0].__easyflowBinary), ['payload']);
      assertFile(execution, 'payload', bytes, 'payload.json');
      durable.push({ id, property: 'payload', bytes, responseBytes: bytes });
    });

    await t.test('rawBody keeps precise JSON and text originals alongside their parsed values under fixed data', async () => {
      for (const entry of [
        { mime: 'application/json', bytes: Buffer.from(' {\r\n "name": "민지", "token": "business"\n } \r\n'), body: { name: '민지', token: 'business' } },
        { mime: 'text/plain; charset=utf-8', bytes: Buffer.from(' 첫 줄\r\n둘째 줄  \n'), body: ' 첫 줄\r\n둘째 줄  \n' },
      ]) {
        const flow = workflow('파싱과 원문 함께', [hook({ rawBody: true, binaryPropertyName: 'unused' }), metadataResponse()]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, entry.bytes, { 'Content-Type': entry.mime });
        assert.equal(response.status, 200);
        const result = await response.json() as Json;
        assert.deepEqual(result.body, entry.body);
        assert.deepEqual(Object.keys(result.files), ['data']);
        const id = executionId(response), execution = await server.execution(id);
        assertFile(execution, 'data', entry.bytes);
        durable.push({ id, property: 'data', bytes: entry.bytes });
      }
    });

    await t.test('legacy v1 keeps unknown MIME out of binary unless explicitly configured as a file', async () => {
      const bytes = Buffer.from([0, 128, 255]);
      for (const forced of [false, true]) {
        const trigger = hook(forced ? { binaryData: true } : {}); trigger.typeVersion = 1;
        const flow = workflow('구형 Webhook 수신 호환', [trigger, forced ? fileResponse() : metadataResponse()]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, bytes, { 'Content-Type': 'application/octet-stream' });
        assert.equal(response.status, 200);
        if (forced) assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
        else { const result = await response.json() as Json; assert.deepEqual(result.body, {}); assert.deepEqual(result.files, {}); }
        const execution = await server.execution(executionId(response));
        if (forced) assertFile(execution, 'data', bytes);
        else assert.equal(execution.runs[0].input[0].__easyflowBinary, undefined);
      }
    });

    await t.test('multipart base64 transfer encoding is decoded into the actual file before replying', async () => {
      const bytes = Buffer.from([0, 1, 128, 255, 13, 10]);
      const input = multipart([{ name: 'data', fileName: 'encoded.bin', mimeType: 'application/octet-stream', encoding: 'base64', value: bytes.toString('base64') }]);
      const flow = workflow('전송 인코딩 파일', [hook(), fileResponse()]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, input.bytes, input.headers);
      assert.equal(response.status, 200);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
      assertFile(await server.execution(executionId(response)), 'data', bytes, 'encoded.bin');
    });

    await t.test('XML matches native repeated tags, attributes and normalized CDATA while retaining its original', async () => {
      const bytes = Buffer.from('<Order Customer="A"><Item>  one  </Item><Item><![CDATA[two   value]]></Item><Empty/></Order>\r\n');
      const expected = { order: { $: { Customer: 'A' }, item: ['one', 'two value'], empty: '' } };
      const flow = workflow('XML 원문과 구조', [hook({ rawBody: true }), metadataResponse()]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, bytes, { 'Content-Type': 'application/xml; charset=utf-8' });
      assert.equal(response.status, 200);
      assert.deepEqual((await response.json() as Json).body, expected);
      const id = executionId(response), execution = await server.execution(id);
      assertFile(execution, 'data', bytes);
      durable.push({ id, property: 'data', bytes });
    });

    await t.test('parsed gzip and deflate JSON retain the decompressed original; raw-file modes retain compressed wire bytes', async () => {
      const bytes = Buffer.from(' {\r\n "name": "민지", "count": 2\n } \r\n');
      for (const encoding of ['gzip', 'deflate']) {
        const compressed = encoding === 'gzip' ? gzipSync(bytes) : deflateSync(bytes);
        const flow = workflow('압축된 JSON 파싱', [hook({ rawBody: true }), metadataResponse()]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, compressed, { 'Content-Type': 'application/json', 'Content-Encoding': encoding });
        assert.equal(response.status, 200);
        assert.deepEqual((await response.json() as Json).body, { name: '민지', count: 2 });
        const id = executionId(response), execution = await server.execution(id);
        assertFile(execution, 'data', bytes);
        durable.push({ id, property: 'data', bytes });
      }
      const compressed = gzipSync(bytes);
      for (const forced of [false, true]) {
        const flow = workflow('압축 파일 그대로 수신', [hook(forced ? { binaryData: true } : {}), fileResponse()]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, compressed, { 'Content-Type': forced ? 'application/json' : 'application/gzip', 'Content-Encoding': 'gzip', 'Content-Disposition': 'attachment; filename=original.gz' });
        assert.equal(response.status, 200);
        assert.deepEqual(Buffer.from(await response.arrayBuffer()), compressed);
        assertFile(await server.execution(executionId(response)), 'data', compressed, 'original.gz');
      }
    });

    await t.test('malformed multipart boundaries, truncation, unsafe field names and malformed JSON fail before execution', async () => {
      const flow = workflow('잘못된 본문 거절', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      const valid = multipart([{ name: 'data', fileName: 'small.bin', mimeType: 'application/octet-stream', value: Buffer.from([0, 128]) }]);
      await server.reject(flow, url, valid.bytes, { 'Content-Type': 'multipart/form-data' }, 400);
      await server.reject(flow, url, valid.bytes, { 'Content-Type': 'multipart/form-data; boundary=wrong' }, 400);
      await server.reject(flow, url, valid.bytes.subarray(0, valid.bytes.length - 10), valid.headers, 400);
      for (const name of ['__proto__', 'constructor', '__easyflowBinary']) {
        const bad = multipart([{ name, fileName: 'bad.bin', mimeType: 'application/octet-stream', value: 'small' }]);
        await server.reject(flow, url, bad.bytes, bad.headers, 400);
        const badField = multipart([{ name, value: 'small' }]);
        await server.reject(flow, url, badField.bytes, badField.headers, 400);
      }
      await server.reject(flow, url, '{"unfinished":', { 'Content-Type': 'application/json' }, 400);
      const collision = multipart([{ name: 'same[]', fileName: 'one.bin', value: 'a' }, { name: 'same', fileName: 'two.bin', value: 'b' }]);
      await server.reject(flow, url, collision.bytes, collision.headers, 400);
      assert.equal(server.count(flow), 0);
    });

    await t.test('invalid file option types and unsafe property settings are refused before activation or execution', async () => {
      for (const options of [{ rawBody: 'true' }, { binaryData: 'true' }, { binaryPropertyName: '__proto__' }, { binaryPropertyName: '' }]) {
        const flow = workflow('잘못된 수신 옵션', [hook(options), metadataResponse()]);
        await server.api('/api/workflows', 'POST', flow);
        const response = await fetch(server.base + '/api/activation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflowId: flow.id, active: true }), signal: AbortSignal.timeout(5_000) });
        assert.equal(response.status, 400, await response.text());
        assert.equal(server.count(flow), 0);
      }
    });

    await t.test('authentication and declared transport limits reject before reading an unfinished file body', async () => {
      const flow = workflow('본문 이전 인증', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      const unauthenticated = new URL(url); unauthenticated.searchParams.delete('token');
      const denied = await headersOnlyRequest(unauthenticated.href, { 'Content-Type': 'multipart/form-data', 'Content-Length': String(WEBHOOK_MULTIPART_BYTES + 1) });
      assert.equal(denied.status, 401, denied.body);
      assert.equal(denied.headers['x-easyflow-execution-id'], undefined);
      const oversized = await headersOnlyRequest(url, { 'Content-Type': 'multipart/form-data; boundary=declared', 'Content-Length': String(WEBHOOK_MULTIPART_BYTES + 1) });
      assert.equal(oversized.status, 413, oversized.body);
      assert.equal(oversized.headers['x-easyflow-execution-id'], undefined);
      assert.equal(server.count(flow), 0);
    });

    await t.test('a headers-only 100MB stalled upload receives its complete 413 body and the server ends its socket promptly', async () => {
      const flow = workflow('멈춘 큰 업로드 종료', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      const rejected = await stalledRequest(url);
      const headerEnd = rejected.bytes.indexOf('\r\n\r\n');
      assert.ok(headerEnd > 0);
      const headers = rejected.bytes.subarray(0, headerEnd).toString('latin1');
      assert.match(headers, /^HTTP\/1\.1 413 /);
      const rawBody = rejected.bytes.subarray(headerEnd + 4);
      let body = rawBody;
      if (/\r\nTransfer-Encoding: chunked\r?$/im.test(headers)) {
        const chunks: Buffer[] = [];
        let offset = 0;
        while (true) {
          const lineEnd = rawBody.indexOf('\r\n', offset); assert.ok(lineEnd >= offset);
          const line = rawBody.subarray(offset, lineEnd).toString('ascii'); assert.match(line, /^[0-9a-f]+$/i);
          const size = Number.parseInt(line, 16); offset = lineEnd + 2;
          if (!size) { assert.equal(rawBody.subarray(offset).toString('ascii'), '\r\n'); break; }
          chunks.push(rawBody.subarray(offset, offset + size));
          assert.equal(rawBody.subarray(offset + size, offset + size + 2).toString('ascii'), '\r\n'); offset += size + 2;
        }
        body = Buffer.concat(chunks);
      }
      const contentLength = /\r\nContent-Length: (\d+)/i.exec(headers);
      if (contentLength) assert.equal(body.length, Number(contentLength[1]), 'socket closure must follow the complete error body');
      assert.equal(typeof JSON.parse(body.toString('utf8')).error, 'string');
      assert.ok(rejected.milliseconds < 1_500, 'the one-second fallback must bound an upload which never sends its body');
      assert.equal(server.count(flow), 0);
    });

    await t.test('parsed JSON, multipart fields and aggregate file limits reject without reserving executions', async () => {
      const flow = workflow('요청 크기 제한', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      await server.reject(flow, url, JSON.stringify({ large: 'a'.repeat(WEBHOOK_JSON_BYTES) }), { 'Content-Type': 'application/json' }, 413);
      const field = multipart([{ name: 'large', value: 'a'.repeat(WEBHOOK_JSON_BYTES + 1) }]);
      await server.reject(flow, url, field.bytes, field.headers, 413);
      await server.reject(flow, url, Buffer.alloc(MAX_BINARY_BYTES + 1, 7), { 'Content-Type': 'application/octet-stream' }, 413);
      const aggregate = multipart([
        { name: 'file', fileName: 'first.bin', mimeType: 'application/octet-stream', value: Buffer.alloc(MAX_BINARY_BYTES / 2, 7) },
        { name: 'file', fileName: 'second.bin', mimeType: 'application/octet-stream', value: Buffer.alloc(MAX_BINARY_BYTES / 2 + 1, 9) },
      ]);
      await server.reject(flow, url, aggregate.bytes, aggregate.headers, 413);
      assert.equal(server.count(flow), 0);
    });

    await t.test('repeated oversized JSON and raw file writes always expose a readable 413 response', async () => {
      const flow = workflow('큰 요청에도 안내 응답', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      const json = JSON.stringify({ large: 'a'.repeat(WEBHOOK_JSON_BYTES) });
      const file = Buffer.alloc(MAX_BINARY_BYTES + 1, 7);
      for (let attempt = 0; attempt < 8; attempt++) {
        for (const [mime, body] of [['application/json', json], ['application/octet-stream', file]] as const) {
          try { await server.reject(flow, url, body, { 'Content-Type': mime }, 413); }
          catch (error) { throw new Error(`${mime} early rejection attempt ${attempt + 1} must return a complete 413 JSON response`, { cause: error }); }
        }
      }
      assert.equal(server.count(flow), 0);
    });

    await t.test('chunked multipart preambles cannot bypass the actual transport limit with a tiny file', async () => {
      const flow = workflow('전송 총 크기 제한', [hook(), metadataResponse()]);
      const { url } = await server.activate(flow);
      const input = multipart([{ name: 'data', fileName: 'tiny.bin', mimeType: 'application/octet-stream', value: 'a' }]);
      const chunk = new Uint8Array(64 * 1024).fill(97);
      let remaining = WEBHOOK_MULTIPART_BYTES + 1;
      let finished = false;
      const body = new ReadableStream<Uint8Array>({ pull(controller) {
        if (remaining) { const size = Math.min(remaining, chunk.length); remaining -= size; controller.enqueue(chunk.subarray(0, size)); }
        else if (!finished) { finished = true; controller.enqueue(new Uint8Array(input.bytes)); }
        else controller.close();
      } });
      await server.reject(flow, url, body, input.headers, 413);
      assert.equal(server.count(flow), 0);
    });

    await t.test('DOCTYPE, invalid compression, unsupported encodings and decompression bombs create no execution', async () => {
      const flow = workflow('파싱 안전 한도', [hook({ rawBody: true }), metadataResponse()]);
      const { url } = await server.activate(flow);
      await server.reject(flow, url, '<!DOCTYPE Order [<!ENTITY label "local">]><Order>&label;</Order>', { 'Content-Type': 'application/xml' }, 400);
      await server.reject(flow, url, '<Order><constructor>unsafe</constructor></Order>', { 'Content-Type': 'application/xml' }, 400);
      await server.reject(flow, url, '<Order><Item></Order>', { 'Content-Type': 'application/xml' }, 400);
      await server.reject(flow, url, Buffer.from('not gzip'), { 'Content-Type': 'application/json', 'Content-Encoding': 'gzip' }, 400);
      await server.reject(flow, url, '{}', { 'Content-Type': 'application/json', 'Content-Encoding': 'compress' }, 415);
      await server.reject(flow, url, '{}', { 'Content-Type': 'application/json; charset=unsupported' }, 415);
      const bomb = gzipSync(Buffer.from(JSON.stringify({ data: 'a'.repeat(WEBHOOK_JSON_BYTES + 1) })));
      assert.ok(bomb.length < 10_000);
      await server.reject(flow, url, bomb, { 'Content-Type': 'application/json', 'Content-Encoding': 'gzip' }, 413);
      assert.equal(server.count(flow), 0);
      const health = await server.api('/api/health'); assert.equal(health.ok, true, 'parser errors must leave the independent server usable');
    });

    await t.test('a raw file exactly at the 8MiB boundary passes through the engine and returns every byte', async () => {
      const bytes = Buffer.alloc(MAX_BINARY_BYTES, 7); bytes[0] = 0; bytes[bytes.length - 1] = 255;
      const flow = workflow('8MiB 파일 경계', [hook(), fileResponse()]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, bytes, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename=max.bin' });
      assert.equal(response.status, 200, await response.clone().text());
      assert.equal(Number(response.headers.get('content-length')), MAX_BINARY_BYTES);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
      const id = executionId(response);
      assert.equal(server.count(flow), 1);
      // Downloading the saved input proves a real persisted binary, without transferring the full execution JSON twice.
      const download = await fetch(server.base + '/api/files/download?' + new URLSearchParams({ executionId: id, runIndex: '0', side: 'input', item: '0', property: 'data' }), { signal: AbortSignal.timeout(15_000) });
      assert.equal(download.status, 200);
      assert.deepEqual(Buffer.from(await download.arrayBuffer()), bytes);
    });

    await t.test('input originals and actual response files remain exact after restarting only this isolated SQLite server', async () => {
      assert.ok(durable.length >= 10);
      await server.stop();
      await server.start();
      for (const entry of durable) {
        const response = await fetch(server.base + '/api/files/download?' + new URLSearchParams({ executionId: entry.id, runIndex: '0', side: 'input', item: '0', property: entry.property }), { signal: AbortSignal.timeout(10_000) });
        assert.equal(response.status, 200, entry.id);
        assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
        assert.deepEqual(Buffer.from(await response.arrayBuffer()), entry.bytes, entry.id);
        if (entry.responseBytes) {
          const saved = await fetch(server.base + '/api/webhook/response?' + new URLSearchParams({ executionId: entry.id }), { signal: AbortSignal.timeout(10_000) });
          assert.equal(saved.status, 200);
          assert.deepEqual(Buffer.from(await saved.arrayBuffer()), entry.responseBytes);
        }
      }
      assert.equal((await server.api('/api/health')).standalone, true);
    });
  } finally {
    await server.stop();
    await rm(directory, { recursive: true, force: true });
  }
});
