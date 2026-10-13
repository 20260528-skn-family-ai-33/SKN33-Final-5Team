import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { request as httpRequest } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHmac, randomUUID } from 'node:crypto';
import type { Execution, Flow, FlowNode, Json } from '../lib/workflow';

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
    const environment: NodeJS.ProcessEnv = { ...process.env, PORT: String(this.port), HOST: '127.0.0.1', EASYFLOW_DATA_DIR: this.directory, EASYFLOW_WEBHOOK_RESPONSE_TIMEOUT_MS: '2000' };
    for (const key of Object.keys(environment)) if (/^N8N_/i.test(key)) delete environment[key];
    const child = this.child = spawn(process.execPath, ['--import', 'tsx', 'server/index.ts'], { cwd: project, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    child.stderr?.on('data', chunk => { this.output = (this.output + String(chunk)).slice(-16_000); });
    let launchError: Error | undefined;
    child.once('error', error => { launchError = error; });
    const deadline = Date.now() + 12_000;
    while (Date.now() < deadline) {
      if (launchError) throw launchError;
      if (child.exitCode !== null || child.signalCode) throw new Error(`Webhook test server exited: ${this.output}`);
      try { const response = await fetch(this.base + '/api/health', { signal: AbortSignal.timeout(500) }); if (response.ok) return; } catch { /* Start-up has not completed yet. */ }
      await delay(40);
    }
    throw new Error(`Webhook test server did not start: ${this.output}`);
  }

  async stop() {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode) return;
    const exit = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
    child.kill('SIGTERM');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([exit, new Promise<never>((_, reject) => { timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(`Webhook server did not shut down: ${this.output}`)); }, 4_000); })]);
      assert.equal(result.code, 0, this.output);
      assert.equal(result.signal, null);
    } finally { if (timer) clearTimeout(timer); }
  }

  async api(path: string, method = 'GET', data?: unknown): Promise<Json> {
    const response = await fetch(this.base + path, { method, headers: { 'Content-Type': 'application/json' }, ...(data !== undefined ? { body: JSON.stringify(data) } : {}), signal: AbortSignal.timeout(5_000) });
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

  async execution(id: string, predicate: (execution: Execution) => boolean): Promise<Execution> {
    const deadline = Date.now() + 8_000;
    let last: Execution | undefined;
    while (Date.now() < deadline) {
      last = (await this.api('/api/executions?id=' + encodeURIComponent(id))).execution;
      assert.ok(last);
      if (predicate(last)) return last;
      await delay(30);
    }
    throw new Error(`Execution did not reach expected state: ${JSON.stringify(last)}`);
  }

  async flowExecution(flow: Flow, predicate: (execution: Execution) => boolean): Promise<Execution> {
    const deadline = Date.now() + 8_000;
    let last: Execution | undefined;
    while (Date.now() < deadline) {
      const executions = (await this.api('/api/executions')).executions as Execution[];
      last = executions.find(execution => execution.workflowId === flow.id);
      if (last && predicate(last)) return last;
      await delay(30);
    }
    throw new Error(`Flow execution did not reach expected state: ${JSON.stringify(last)}`);
  }

  request(url: string, method = 'POST', data?: unknown, options: RequestInit = {}) {
    return fetch(url, { method, headers: { 'Content-Type': 'application/json', ...options.headers }, ...(data !== undefined ? { body: JSON.stringify(data) } : {}), redirect: 'manual', signal: AbortSignal.timeout(6_000), ...options });
  }
}

function stage(type: string, name: string, parameters: Json = {}): FlowNode {
  return { id: randomUUID(), name, type: `n8n-nodes-base.${type}`, typeVersion: type === 'set' ? 3.4 : type === 'webhook' ? 2.1 : type === 'respondToWebhook' ? 1.4 : 1, position: [0, 0], parameters };
}

function workflow(name: string, nodes: FlowNode[]): Flow {
  return { id: randomUUID(), name, nodes, connections: Object.fromEntries(nodes.slice(0, -1).map((node, index) => [node.name, { main: [[{ node: nodes[index + 1].name, type: 'main', index: 0 }]] }])), settings: { timezone: 'Asia/Seoul', executionOrder: 'v1' }, active: false };
}

function hook(responseMode: string, more: Json = {}) { return stage('webhook', '외부 요청', { httpMethod: 'POST', path: 'hook-' + randomUUID(), responseMode, options: {}, ...more }); }
function responseNode(respondWith: string, more: Json = {}) { return stage('respondToWebhook', '요청에 응답', { respondWith, options: {}, ...more }); }
function code(source: string) { return stage('code', '응답 데이터 준비', { language: 'javaScript', mode: 'runOnceForAllItems', jsCode: source }); }
function waiting(amount = 30) { return stage('wait', '처리 대기', { resume: 'timeInterval', unit: 'seconds', amount }); }
function fail(name = '응답 전 실패') { return stage('stopAndError', name, { errorType: 'errorMessage', errorMessage: 'HTTP 통합 검증용 실패' }); }
function executionId(response: Response) { const id = response.headers.get('x-easyflow-execution-id'); assert.ok(id, 'every accepted request must expose its own persisted execution id'); return id; }

test('independent Webhook HTTP responses honor native modes, bytes, credentials and durable waits', { timeout: 90_000 }, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'easyflow-webhook-api-'));
  const server = new Server(await freePort(), directory);
  const sentResponses: { id: string; mimeType: string; bytes: Buffer }[] = [];
  const downloadResponse = (id?: string, headers: Record<string, string> = {}) => fetch(server.base + '/api/webhook/response' + (id === undefined ? '' : '?' + new URLSearchParams({ executionId: id })), { headers, signal: AbortSignal.timeout(5_000) });
  try {
    await server.start();

    await t.test('onReceived responds with default 200 before the workflow leaves a durable wait', async () => {
      const flow = workflow('수신 즉시 응답', [hook('onReceived'), waiting()]);
      const { url, token } = await server.activate(flow);
      const accepted = await server.request(url + '&label=example', 'POST', { message: '자체 요청' }, { headers: { 'Content-Type': 'application/json', 'x-easyflow-token': token, 'x-example': 'visible', Authorization: 'Bearer private-request-example', Cookie: 'private-session=example' } });
      assert.equal(accepted.status, 200);
      const id = executionId(accepted);
      const body = await accepted.json() as Json;
      assert.equal(body.executionId, id);
      const result = await server.execution(id, execution => execution.status === 'waiting');
      assert.equal(result.trigger, 'webhook');
      assert.equal(result.runs[0].input[0].body.message, '자체 요청');
      assert.equal(result.runs[0].input[0].query.label, 'example');
      assert.equal(result.runs[0].input[0].headers['x-example'], 'visible');
      assert.equal(result.runs[0].input[0].query.token, undefined);
      assert.equal(result.runs[0].input[0].headers['x-easyflow-token'], undefined);
      assert.equal(result.runs[0].input[0].headers.authorization, undefined);
      assert.equal(result.runs[0].input[0].headers.cookie, undefined);
      assert.ok(!JSON.stringify(result).includes(token));
      await server.api('/api/executions/cancel', 'POST', { id });
    });

    await t.test('onReceived respects native nested status, headers and custom text', async () => {
      const flow = workflow('수신 안내문 설정', [hook('onReceived', { options: { responseCode: { values: { responseCode: 201 } }, responseData: '접수가 완료되었습니다.', responseHeaders: { entries: [{ name: 'X-Workflow-Notice', value: 'received' }, { name: 'Content-Type', value: 'text/plain; charset=utf-8' }] } } }), stage('noOp', '요청 그대로')]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', { example: true });
      assert.equal(response.status, 201);
      assert.equal(response.headers.get('x-workflow-notice'), 'received');
      assert.match(response.headers.get('content-type') || '', /text\/plain/);
      assert.equal(await response.text(), '접수가 완료되었습니다.');
      assert.equal((await server.execution(executionId(response), finished)).status, 'success');
      const withoutBody = workflow('즉시 응답 본문 없이', [hook('onReceived', { options: { responseCode: { values: { responseCode: 200, customCode: 207 } }, noResponseBody: true } }), stage('noOp', '요청 그대로')]);
      const { url: emptyUrl } = await server.activate(withoutBody);
      const emptyResponse = await server.request(emptyUrl, 'POST', {});
      assert.equal(emptyResponse.status, 207);
      assert.equal(await emptyResponse.text(), '');
      assert.equal((await server.execution(executionId(emptyResponse), finished)).status, 'success');
    });

    await t.test('immediate response expressions share the real execution id and production request context', async () => {
      const trigger = hook('onReceived', {
        path: 'receive/:customerId',
        options: {
          responseData: '={{ { id: $execution.id, mode: $execution.mode, ticket: $input.first().json.body.ticket, customerId: $json.params.customerId, label: $json.query.label, path: $parameter.path, itemIndex: $itemIndex, runIndex: $runIndex } }}',
          responseCode: { values: { customCode: '={{ Number($json.body.status) }}' } },
          responseHeaders: { entries: [{ name: 'X-Actual-Execution', value: '={{ $execution.id }}' }, { name: 'X-Execution-Mode', value: '={{ $execution.mode }}' }] },
        },
      });
      const flow = workflow('접수 응답에서 실행 정보 사용', [trigger, stage('noOp', '요청 그대로')]);
      const { token } = await server.activate(flow);
      const url = server.base + '/hooks/' + flow.id + '/receive/customer-A?token=' + token + '&label=received';
      const responses = await Promise.all(['A', 'B'].map(ticket => server.request(url, 'POST', { ticket, status: 201 })));
      const ids = responses.map(executionId);
      assert.equal(new Set(ids).size, 2);
      for (const [index, response] of responses.entries()) {
        assert.equal(response.status, 201);
        assert.equal(response.headers.get('x-actual-execution'), ids[index]);
        assert.equal(response.headers.get('x-execution-mode'), 'production');
        assert.deepEqual(await response.json(), { id: ids[index], mode: 'production', ticket: ['A', 'B'][index], customerId: 'customer-A', label: 'received', path: 'receive/:customerId', itemIndex: 0, runIndex: 0 });
        const result = await server.execution(ids[index], finished);
        assert.equal(result.status, 'success', result.error);
        assert.equal(result.runs[0].input[0].body.ticket, ['A', 'B'][index]);
      }
      const invalid = await server.request(url, 'POST', { ticket: 'invalid', status: 999 });
      assert.equal(invalid.status, 400);
      assert.equal(invalid.headers.get('x-easyflow-execution-id'), null);
      const executions = (await server.api('/api/executions')).executions as Execution[];
      assert.equal(executions.filter(execution => execution.workflowId === flow.id).length, 2, 'an invalid immediate response must fail before reserving or executing workflow work');
    });

    await t.test('a provisional status is refused before reserving an immediate Webhook execution', async () => {
      const flow = workflow('최종 응답에는 완료 상태 코드 사용', [hook('onReceived', { responseCode: 100 }), stage('noOp', '실행되지 않음')]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', {});
      assert.equal(response.status, 400);
      assert.equal(response.headers.get('x-easyflow-execution-id'), null);
      const executions = (await server.api('/api/executions')).executions as Execution[];
      assert.equal(executions.filter(execution => execution.workflowId === flow.id).length, 0);
    });

    await t.test('all six native request methods preserve bodies while HEAD sends no response bytes', async () => {
      for (const method of ['DELETE', 'GET', 'HEAD', 'PATCH', 'POST', 'PUT']) {
        const flow = workflow(method + ' 요청', [hook('lastNode', { httpMethod: method }), stage('set', '받은 요청', { mode: 'raw', jsonOutput: '={{ $json }}', includeOtherFields: false })]);
        const { url } = await server.activate(flow);
        const input = ['GET', 'HEAD'].includes(method) ? undefined : { method };
        const response = await server.request(url + '&marker=' + method, method, input);
        assert.equal(response.status, 200, method);
        const raw = await response.text();
        const result = await server.execution(executionId(response), finished);
        assert.equal(result.status, 'success', result.error);
        assert.equal(result.runs[0].input[0].query.marker, method);
        assert.deepEqual(result.runs[0].input[0].body, input || {});
        if (method === 'HEAD') assert.equal(raw, '');
        else assert.equal(JSON.parse(raw).query.marker, method);
      }
    });

    await t.test('nested Webhook paths decode route parameters and retain production metadata without the request token', async () => {
      const trigger = hook('lastNode', { path: 'customers/:customerId/orders/:orderId' });
      const flow = workflow('고객별 주문 요청 주소', [trigger, stage('noOp', '주소와 요청 데이터')]);
      const { token } = await server.activate(flow);
      const url = server.base + '/hooks/' + encodeURIComponent(flow.id) + '/customers/customer%20A/orders/order-42?source=local';
      const response = await server.request(url, 'POST', { total: 30000 }, { headers: { 'Content-Type': 'application/json', 'x-easyflow-token': token } });
      assert.equal(response.status, 200);
      const body = await response.json() as Json;
      assert.deepEqual(body.params, { customerId: 'customer A', orderId: 'order-42' });
      assert.equal(body.query.source, 'local');
      assert.equal(body.executionMode, 'production');
      assert.equal(new URL(body.webhookUrl).origin, server.base);
      assert.ok(!JSON.stringify(body).includes(token));
      const result = await server.execution(executionId(response), finished);
      assert.equal(result.status, 'success', result.error);
      assert.deepEqual(result.runs[0].input[0].params, body.params);
      assert.equal(result.runs[0].input[0].executionMode, 'production');
      assert.ok(!JSON.stringify(result).includes(token));
      const unmatched = await server.request(server.base + '/hooks/' + flow.id + '/customers/customer-A', 'POST', {}, { headers: { 'Content-Type': 'application/json', 'x-easyflow-token': token } });
      assert.equal(unmatched.status, 404);
      assert.equal(unmatched.headers.get('x-easyflow-execution-id'), null);
    });

    await t.test('URL form body fields named token survive while only the authentication query token is consumed', async () => {
      const flow = workflow('양식 데이터의 token 항목 보존', [hook('lastNode'), stage('noOp', '받은 양식 그대로')]);
      const { url, token } = await server.activate(flow);
      const response = await server.request(url + '&label=one&label=two', 'POST', undefined, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams([['token', 'application-value'], ['token', 'second-value'], ['name', '고객 A'], ['name', '고객 B']]).toString(),
      });
      assert.equal(response.status, 200);
      const payload = await response.json() as Json;
      assert.deepEqual(payload.body, { token: ['application-value', 'second-value'], name: ['고객 A', '고객 B'] });
      assert.deepEqual(payload.query, { label: ['one', 'two'] });
      const result = await server.execution(executionId(response), finished);
      assert.equal(result.status, 'success', result.error);
      assert.deepEqual(result.runs[0].input[0].body, payload.body);
      assert.equal(result.runs[0].input[0].query.token, undefined);
      assert.ok(!JSON.stringify(result).includes(token));
    });

    await t.test('lastNode returns actual first or all JSON values without provenance metadata', async () => {
      const values = [{ name: '고객 A', score: 92 }, { name: '고객 C', score: 84 }];
      for (const mode of ['firstEntryJson', 'allEntries']) {
        const flow = workflow('마지막 JSON ' + mode, [hook('lastNode', { responseData: mode, options: { responseHeaders: { entries: [{ name: 'X-Result-Mode', value: mode }] } } }), code('return ' + JSON.stringify(values) + ';')]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, 'POST', { request: mode });
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('x-result-mode'), mode);
        assert.deepEqual(await response.json(), mode === 'allEntries' ? values : values[0]);
        const result = await server.execution(executionId(response), finished);
        assert.equal(result.status, 'success', result.error);
        assert.equal(result.runs.at(-1)?.output[0].length, 2);
      }
    });

    await t.test('lastNode responds using only output zero of the actual last executed node', async () => {
      const accepted = { name: '고객 A', score: 92 };
      const rejected = { name: '고객 B', score: 68 };
      for (const input of [[accepted, rejected], [rejected]]) {
        const filter = stage('if', '점수 조건 분기', { conditions: { options: { caseSensitive: true, typeValidation: 'strict' }, conditions: [{ id: randomUUID(), leftValue: '={{ $json.score }}', rightValue: 80, operator: { type: 'number', operation: 'gt' } }], combinator: 'and' } }); filter.typeVersion = 2.2;
        const flow = workflow('마지막 조건의 첫 출력만 응답 ' + input.length, [hook('lastNode', { responseData: 'allEntries' }), code('return ' + JSON.stringify(input) + ';'), filter]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, 'POST', {});
        assert.equal(response.status, 200);
        const output = input.length === 2 ? [accepted] : [];
        assert.deepEqual(await response.json(), output);
        const result = await server.execution(executionId(response), finished);
        assert.equal(result.status, 'success', result.error);
        const last = result.runs.at(-1);
        assert.ok(last);
        assert.equal(last.nodeId, filter.id);
        assert.deepEqual(last.output[0], output);
        assert.deepEqual(last.output[1], [rejected]);
      }
    });

    await t.test('final response expressions distinguish the linked original item from the explicit first item', async () => {
      const source = code('return [{ json: { name: "A", score: 92 } }, { json: { name: "B", score: 68 } }, { json: { name: "C", score: 84 } }];');
      const trigger = hook('lastNode', {
        responseData: 'firstEntryJson',
        options: {
          responseCode: { values: { customCode: '={{ $json.score > 80 ? 201 : 202 }}' } },
          responseHeaders: { entries: [
            { name: 'X-Linked-Original', value: "={{ $('응답 데이터 준비').item.json.name }}" },
            { name: 'X-Explicit-First', value: "={{ $('응답 데이터 준비').first().json.name }}" },
            { name: 'X-Actual-Execution', value: '={{ $execution.id }}' },
          ] },
        },
      });
      const flow = workflow('최종 응답에서 원래 항목 찾기', [trigger, source, stage('sort', '낮은 점수부터', { type: 'simple', sortFieldsUi: { sortField: [{ fieldName: 'score', order: 'ascending' }] } }), stage('limit', '한 고객 선택', { maxItems: 1, keep: 'firstItems' }), stage('set', '점수만 남기기', { mode: 'raw', jsonOutput: '={{ { score: $json.score } }}', includeOtherFields: false })]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', {});
      assert.equal(response.status, 202);
      assert.equal(response.headers.get('x-linked-original'), 'B');
      assert.equal(response.headers.get('x-explicit-first'), 'A');
      assert.equal(response.headers.get('x-actual-execution'), executionId(response));
      assert.deepEqual(await response.json(), { score: 68 });
      const result = await server.execution(executionId(response), finished);
      assert.equal(result.status, 'success', result.error);
      assert.deepEqual(result.runs.at(-1)?.output[0], [{ score: 68 }]);
    });

    await t.test('lastNode projects native responsePropertyName and sends noData with no bytes', async () => {
      const selected = workflow('마지막 결과 항목 선택', [hook('lastNode', { responseData: 'firstEntryJson', options: { responsePropertyName: 'nested.reply' } }), code('return [{ json: { nested: { reply: { ticket: "one" } }, internal: true } }];')]);
      const { url } = await server.activate(selected);
      const projection = await server.request(url, 'POST', {});
      assert.deepEqual(await projection.json(), { ticket: 'one' });
      const empty = workflow('마지막 응답 본문 없이', [hook('lastNode', { responseData: 'noData', responseCode: 204 }), stage('noOp', '입력 그대로')]);
      const { url: emptyUrl } = await server.activate(empty);
      const response = await server.request(emptyUrl, 'POST', { privateResult: true });
      assert.equal(response.status, 204);
      assert.equal(await response.text(), '');
      assert.equal((await server.execution(executionId(response), finished)).status, 'success');
    });

    await t.test('lastNode and Respond to Webhook stream exact binary file bytes from the selected field', async () => {
      const bytes = Buffer.from([0, 1, 2, 3, 255]);
      const source = 'return [{ json: { marker: "binary" }, binary: { download: { data: "AAECA/8=", mimeType: "application/octet-stream", fileName: "검증 결과.bin", fileSize: 5 } } }];';
      for (const mode of ['lastNode', 'responseNode']) {
        const responseHeaders = { entries: [{ name: 'Content-Disposition', value: 'attachment; filename="result.bin"' }] };
        const nodes = [hook(mode, { responseData: 'firstEntryBinary', responseBinaryPropertyName: 'download', options: { responseHeaders } }), code(source)];
        if (mode === 'responseNode') nodes.push(responseNode('binary', { responseDataSource: 'set', inputFieldName: 'download', options: { responseHeaders } }));
        const flow = workflow('실제 파일 응답 ' + mode, nodes);
        const { url } = await server.activate(flow);
        const response = await server.request(url, 'POST', {});
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('content-type'), 'application/octet-stream');
        assert.equal(response.headers.get('content-length'), String(bytes.length));
        assert.equal(response.headers.get('content-disposition'), 'attachment; filename="result.bin"');
        assert.equal(Buffer.compare(Buffer.from(await response.arrayBuffer()), bytes), 0);
        assert.equal((await server.execution(executionId(response), finished)).status, 'success');
      }
    });

    await t.test('Respond to Webhook selects first or all items, wraps responseKey and evaluates custom JSON on the first input', async () => {
      const values = [{ ticket: 'first', score: 92 }, { ticket: 'second', score: 84 }];
      for (const mode of ['firstIncomingItem', 'allIncomingItems']) {
        const flow = workflow('응답 입력 선택 ' + mode, [hook('responseNode'), code('return ' + JSON.stringify(values) + ';'), responseNode(mode, { options: { responseKey: 'result.data', responseCode: 206 } })]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, 'POST', {});
        assert.equal(response.status, 206);
        assert.deepEqual(await response.json(), { result: { data: mode === 'allIncomingItems' ? values : values[0] } });
        assert.equal((await server.execution(executionId(response), finished)).status, 'success');
      }
      const flow = workflow('처음 입력으로 응답 구성', [hook('responseNode'), code('return ' + JSON.stringify(values) + ';'), responseNode('json', { responseBody: '={{ $json }}', options: { responseCode: 201, responseHeaders: { entries: [{ name: 'X-Ticket', value: '={{ $json.ticket }}' }] } } })]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', {});
      assert.equal(response.status, 201);
      assert.equal(response.headers.get('x-ticket'), 'first');
      assert.deepEqual(await response.json(), values[0]);
    });

    await t.test('Respond to Webhook sends text, redirect and noData as HTTP responses', async () => {
      for (const mode of ['text', 'redirect', 'noData']) {
        const parameters: Json = mode === 'text' ? { responseBody: '완료: {{ $json.body.ticket }}', options: { responseCode: 202, responseHeaders: { entries: [{ name: 'Content-Type', value: 'text/plain; charset=utf-8' }] } } } : mode === 'redirect' ? { redirectURL: server.base + '/local-result' } : { options: { responseCode: 204 } };
        const flow = workflow('직접 응답 ' + mode, [hook('responseNode'), responseNode(mode, parameters)]);
        const { url } = await server.activate(flow);
        const response = await server.request(url, 'POST', { ticket: 'ABC' });
        if (mode === 'text') { assert.equal(response.status, 202); assert.equal(await response.text(), '완료: ABC'); }
        else if (mode === 'redirect') { assert.equal(response.status, 307); assert.equal(response.headers.get('location'), server.base + '/local-result'); assert.equal(await response.text(), ''); }
        else { assert.equal(response.status, 204); assert.equal(await response.text(), ''); }
        assert.equal((await server.execution(executionId(response), finished)).status, 'success');
      }
    });

    await t.test('the first response wins, later work still executes and later failure cannot overwrite sent bytes', async () => {
      const first = responseNode('text', { responseBody: 'first', options: { responseCode: 201, responseHeaders: { entries: [{ name: 'X-Winner', value: 'first' }, { name: 'Content-Type', value: 'text/plain; charset=utf-8' }] } } });
      const second = responseNode('text', { responseBody: 'second', options: { responseCode: 409 } }); second.name = '두 번째 응답';
      const flow = workflow('한 요청에 한 응답', [hook('responseNode'), first, second, fail('응답 뒤 실패')]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', {});
      assert.equal(response.status, 201);
      assert.equal(response.headers.get('x-winner'), 'first');
      assert.equal(await response.text(), 'first');
      const result = await server.execution(executionId(response), execution => execution.status === 'error');
      assert.ok(result.runs.some(run => run.nodeId === second.id && run.status === 'success'));
      assert.ok(result.runs.some(run => run.nodeId === flow.nodes.at(-1)?.id && run.status === 'error'));
      assert.match(result.error || '', /HTTP 통합 검증용 실패/);
      assert.doesNotMatch(server.output, /ERR_HTTP_HEADERS_SENT|write after end|uncaughtException/);
    });

    await t.test('the optional response output branch records the response while the normal branch retains the request', async () => {
      const trigger = hook('responseNode');
      const reply = responseNode('json', { responseBody: '={{ $json.body }}', enableResponseOutput: true, options: { responseCode: 201, responseHeaders: { entries: [{ name: 'X-Response-Branch', value: 'enabled' }] } } });
      const request = stage('noOp', '원래 요청 보존');
      const response = stage('set', '보낸 응답 보존', { mode: 'raw', jsonOutput: '={{ $json.response }}', includeOtherFields: false });
      const flow = workflow('응답 결과도 다음 단계에서 사용', [trigger, reply, request, response]);
      flow.connections = {
        [trigger.name]: { main: [[{ node: reply.name, type: 'main', index: 0 }]] },
        [reply.name]: { main: [[{ node: request.name, type: 'main', index: 0 }], [{ node: response.name, type: 'main', index: 0 }]] },
      };
      const { url } = await server.activate(flow);
      const accepted = await server.request(url, 'POST', { ticket: 'branch' });
      assert.equal(accepted.status, 201);
      assert.equal(accepted.headers.get('x-response-branch'), 'enabled');
      assert.deepEqual(await accepted.json(), { ticket: 'branch' });
      const result = await server.execution(executionId(accepted), finished);
      assert.equal(result.status, 'success', result.error);
      assert.deepEqual(result.runs.find(run => run.nodeId === request.id)?.output[0][0].body, { ticket: 'branch' });
      const recorded = result.runs.find(run => run.nodeId === response.id)?.output[0][0];
      assert.equal(recorded?.statusCode, 201);
      assert.equal(recorded?.headers['x-response-branch'], 'enabled');
      assert.deepEqual(recorded?.body, { ticket: 'branch' });
    });

    await t.test('failure before a response returns 500, and successful completion without a response returns 200', async () => {
      const failure = workflow('응답 전 실패 처리', [hook('responseNode'), fail(), responseNode('text', { responseBody: '도달하지 않음' })]);
      const { url } = await server.activate(failure);
      const response = await server.request(url, 'POST', {});
      assert.equal(response.status, 500);
      assert.equal((await server.execution(executionId(response), finished)).status, 'error');
      const fallback = workflow('응답 단계에 도달하지 않고 완료', [hook('responseNode'), code('return [];'), responseNode('text', { responseBody: '실행되지 않은 응답' })]);
      const { url: fallbackUrl } = await server.activate(fallback);
      const completed = await server.request(fallbackUrl, 'POST', { ticket: 'fallback' });
      assert.equal(completed.status, 200);
      assert.equal((await server.execution(executionId(completed), finished)).status, 'success');
    });

    await t.test('durable Wait keeps a response request open through resume and cancellation settles its response', async () => {
      const flow = workflow('기다린 뒤 응답', [hook('responseNode'), waiting(0.2), responseNode('json', { responseBody: '={{ $json.body }}' })]);
      const { url } = await server.activate(flow);
      let settled = false;
      const pending = server.request(url, 'POST', { ticket: 'after-wait' }).then(response => { settled = true; return response; });
      const waitingExecution = await server.flowExecution(flow, execution => execution.status === 'waiting');
      assert.equal(settled, false, 'Wait must not send a premature response');
      const response = await pending;
      assert.equal(response.status, 200);
      assert.equal(executionId(response), waitingExecution.id);
      assert.deepEqual(await response.json(), { ticket: 'after-wait' });
      assert.equal((await server.execution(waitingExecution.id, finished)).status, 'success');
      const canceledFlow = workflow('응답 대기 중 실행 취소', [hook('responseNode'), waiting(), responseNode('text', { responseBody: '취소 후 실행되면 안 됨' })]);
      const { url: canceledUrl } = await server.activate(canceledFlow);
      const canceledRequest = server.request(canceledUrl, 'POST', {});
      const queued = await server.flowExecution(canceledFlow, execution => execution.status === 'waiting');
      await server.api('/api/executions/cancel', 'POST', { id: queued.id });
      const canceledResponse = await canceledRequest;
      assert.ok(canceledResponse.status >= 500 && canceledResponse.status <= 599);
      const canceled = await server.execution(queued.id, finished);
      assert.equal(canceled.status, 'canceled');
      assert.ok(!canceled.runs.some(run => run.nodeId === canceledFlow.nodes[2].id));
    });

    await t.test('response timeout returns 504 without canceling the durable workflow', async () => {
      const flow = workflow('응답 시간 제한', [hook('lastNode'), waiting(), stage('noOp', '나중에 계속')]);
      const { url } = await server.activate(flow);
      const response = await server.request(url, 'POST', { ticket: 'timeout' });
      assert.equal(response.status, 504);
      const id = executionId(response);
      const result = await server.execution(id, execution => execution.status === 'waiting');
      assert.equal(result.status, 'waiting');
      await server.api('/api/executions/cancel', 'POST', { id });
    });

    await t.test('a disconnected client releases its response channel while the workflow resumes normally', async () => {
      const flow = workflow('클라이언트 연결 종료', [hook('responseNode'), waiting(0.2), responseNode('text', { responseBody: '계속 처리' })]);
      const { url } = await server.activate(flow);
      const controller = new AbortController();
      const pending = server.request(url, 'POST', { ticket: 'disconnect' }, { signal: controller.signal }).then(response => ({ response }), error => ({ error }));
      const waitingExecution = await server.flowExecution(flow, execution => execution.status === 'waiting');
      controller.abort();
      const disconnected = await pending;
      assert.ok('error' in disconnected);
      const result = await server.execution(waitingExecution.id, finished);
      assert.equal(result.status, 'success', result.error);
      assert.ok(result.runs.some(run => run.nodeId === flow.nodes[2].id && run.status === 'success'));
      assert.doesNotMatch(server.output, /ERR_HTTP_HEADERS_SENT|write after end|uncaughtException/);
    });

    await t.test('concurrent requests receive their own response and execution records', async () => {
      const flow = workflow('요청별 응답 분리', [hook('responseNode'), responseNode('json', { responseBody: '={{ $json.body }}' })]);
      const { url } = await server.activate(flow);
      const responses = await Promise.all(['A', 'B', 'C', 'D'].map(ticket => server.request(url, 'POST', { ticket })));
      const ids = responses.map(executionId);
      assert.equal(new Set(ids).size, 4);
      for (let index = 0; index < responses.length; index++) {
        assert.equal(responses[index].status, 200);
        assert.deepEqual(await responses[index].json(), { ticket: ['A', 'B', 'C', 'D'][index] });
        const result = await server.execution(ids[index], finished);
        assert.equal(result.status, 'success', result.error);
        assert.equal(result.runs[0].input[0].body.ticket, ['A', 'B', 'C', 'D'][index]);
      }
    });

    await t.test('manual runs execute Respond to Webhook without sending or retaining a live HTTP channel', async () => {
      const flow = workflow('수동 응답 연습', [hook('responseNode'), responseNode('json', { responseBody: '={{ $json.body }}' }), stage('noOp', '계속 실행')]);
      await server.api('/api/workflows', 'POST', flow);
      const started = await fetch(server.base + '/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflowId: flow.id, mode: 'live', input: [{ body: { ticket: 'manual' } }] }) });
      assert.equal(started.status, 202);
      const id = (await started.json() as Json).executionId;
      const result = await server.execution(id, finished);
      assert.equal(result.status, 'success', result.error);
      assert.equal(result.runs.length, 3);
      assert.deepEqual(result.runs.at(-1)?.output[0][0].body, { ticket: 'manual' });
    });

    await t.test('explicit none, Basic and Header authentication are enforced without persisting their private values', async () => {
      const publicFlow = workflow('인증 없이 받기', [hook('lastNode', { authentication: 'none' }), stage('noOp', '요청 그대로')]);
      const { url: publicUrl } = await server.activate(publicFlow);
      const plainUrl = new URL(publicUrl); plainUrl.searchParams.delete('token');
      const publicResponse = await server.request(plainUrl.toString(), 'POST', { public: true });
      assert.equal(publicResponse.status, 200);
      for (const mode of ['basicAuth', 'headerAuth']) {
        const secret = 'private-auth-example-' + randomUUID();
        const provider = mode === 'basicAuth' ? 'basic' : 'header';
        const credential = await server.api('/api/credentials', 'POST', { name: '요청 인증 ' + mode, provider, data: provider === 'basic' ? { username: 'example-user', password: secret } : { name: 'x-example-private', value: secret } });
        const trigger = hook('lastNode', { authentication: mode }); trigger.credentialId = credential.credential.id;
        const flow = workflow('수신 인증 ' + mode, [trigger, stage('noOp', '받은 내용')]);
        const { url } = await server.activate(flow);
        const withoutToken = new URL(url); withoutToken.searchParams.delete('token');
        const rejected = await server.request(withoutToken.toString(), 'POST', {});
        assert.equal(rejected.status, provider === 'basic' ? 401 : 403);
        assert.equal(rejected.headers.get('x-easyflow-execution-id'), null);
        if (provider === 'basic') assert.match(rejected.headers.get('www-authenticate') || '', /Basic/i);
        const header: Record<string, string> = provider === 'basic' ? { Authorization: 'Basic ' + Buffer.from('example-user:' + secret).toString('base64') } : { 'x-example-private': secret };
        const response = await server.request(withoutToken.toString(), 'POST', { accepted: mode }, { headers: { 'Content-Type': 'application/json', ...header } });
        assert.equal(response.status, 200);
        const result = await server.execution(executionId(response), finished);
        assert.equal(result.status, 'success', result.error);
        assert.ok(!JSON.stringify(result).includes(secret));
        assert.equal(result.runs[0].input[0].headers[provider === 'basic' ? 'authorization' : 'x-example-private'], undefined);
        assert.ok(!JSON.stringify(await server.api('/api/credentials')).includes(secret));
      }
    });

    await t.test('native Bearer authentication rejects absent or incorrect tokens and consumes the successful Authorization header', async () => {
      const secret = 'private-bearer-request-' + randomUUID();
      const saved = await server.api('/api/credentials', 'POST', { name: 'Bearer 요청 검증', provider: 'bearer', data: { token: secret } });
      const trigger = hook('lastNode', { authentication: 'bearerAuth' }); trigger.credentialId = saved.credential.id;
      const flow = workflow('Bearer 인증한 요청', [trigger, stage('noOp', '요청 그대로')]);
      const { url } = await server.activate(flow);
      for (const authorization of ['', 'Bearer incorrect-example']) {
        const rejected = await server.request(url, 'POST', {}, { headers: { 'Content-Type': 'application/json', ...(authorization ? { Authorization: authorization } : {}) } });
        assert.equal(rejected.status, 403);
        assert.equal(rejected.headers.get('x-easyflow-execution-id'), null);
      }
      const accepted = await server.request(url, 'POST', { message: '자체 인증 요청' }, { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + secret } });
      assert.equal(accepted.status, 200);
      const body = await accepted.json() as Json;
      assert.equal(body.headers.authorization, undefined);
      assert.equal(body.body.message, '자체 인증 요청');
      assert.ok(!JSON.stringify(body).includes(secret));
      const result = await server.execution(executionId(accepted), finished);
      assert.equal(result.status, 'success', result.error);
      assert.equal(result.runs[0].input[0].headers.authorization, undefined);
      assert.ok(!JSON.stringify(result).includes(secret));
      const executions = (await server.api('/api/executions')).executions as Execution[];
      assert.equal(executions.filter(execution => execution.workflowId === flow.id).length, 1);
    });

    await t.test('JWT authentication validates signatures and time claims before starting executions and JWT responses verify independently', async () => {
      const secret = 'private-jwt-test-' + randomUUID();
      const saved = await server.api('/api/credentials', 'POST', { name: 'JWT 요청 검증', provider: 'jwt', data: { keyType: 'passphrase', secret, algorithm: 'HS256' } });
      const credentialId = saved.credential.id;
      const jwt = (payload: Json, key = secret) => {
        const encoded = [JSON.stringify({ alg: 'HS256', typ: 'JWT' }), JSON.stringify(payload)].map(value => Buffer.from(value).toString('base64url')).join('.');
        return encoded + '.' + createHmac('sha256', key).update(encoded).digest('base64url');
      };
      const trigger = hook('lastNode', { authentication: 'jwtAuth' }); trigger.credentialId = credentialId;
      const authenticated = workflow('JWT 서명으로 받기', [trigger, stage('noOp', '인증한 요청 보존')]);
      const { url } = await server.activate(authenticated);
      const cleanUrl = new URL(url); cleanUrl.searchParams.delete('token');
      const missing = await server.request(cleanUrl.toString(), 'POST', {});
      assert.equal(missing.status, 401);
      const wrongAlgorithmContent = [JSON.stringify({ alg: 'HS512', typ: 'JWT' }), JSON.stringify({ sub: 'example' })].map(value => Buffer.from(value).toString('base64url')).join('.');
      const wrongAlgorithm = wrongAlgorithmContent + '.' + createHmac('sha512', secret).update(wrongAlgorithmContent).digest('base64url');
      for (const invalid of [jwt({ sub: 'example' }, 'different-secret'), jwt({ sub: 'example', exp: Math.floor(Date.now() / 1000) - 10 }), jwt({ sub: 'example', nbf: Math.floor(Date.now() / 1000) + 300 }), wrongAlgorithm]) {
        const response = await server.request(cleanUrl.toString(), 'POST', {}, { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + invalid } });
        assert.equal(response.status, 403);
        assert.equal(response.headers.get('x-easyflow-execution-id'), null);
      }
      const claims = { sub: 'example-user', role: 'test', exp: Math.floor(Date.now() / 1000) + 300 };
      const validToken = jwt(claims);
      const accepted = await server.request(cleanUrl.toString(), 'POST', { ticket: 'JWT' }, { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + validToken } });
      assert.equal(accepted.status, 200);
      const result = await server.execution(executionId(accepted), finished);
      assert.equal(result.status, 'success', result.error);
      assert.deepEqual(result.runs[0].input[0].jwtPayload, claims);
      assert.equal(result.runs[0].input[0].headers.authorization, undefined);
      assert.ok(!JSON.stringify(result).includes(secret));
      assert.ok(!JSON.stringify(result).includes(validToken));
      assert.equal(((await server.api('/api/executions')).executions as Execution[]).filter(execution => execution.workflowId === authenticated.id).length, 1);
      const response = responseNode('jwt', { payload: '={{ $json.body }}' }); response.credentialId = credentialId;
      const signed = workflow('JWT 응답 서명', [hook('responseNode'), response]);
      const { url: signedUrl } = await server.activate(signed);
      const signedResponse = await server.request(signedUrl, 'POST', { sub: 'response-user', purpose: 'local-integration' });
      assert.equal(signedResponse.status, 200);
      const body = await signedResponse.json() as Json;
      const parts = String(body.token).split('.');
      assert.equal(parts.length, 3);
      assert.deepEqual(JSON.parse(Buffer.from(parts[0], 'base64url').toString()), { alg: 'HS256', typ: 'JWT' });
      const signedPayload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
      assert.equal(signedPayload.sub, 'response-user');
      assert.equal(signedPayload.purpose, 'local-integration');
      assert.equal(typeof signedPayload.iat, 'number');
      assert.equal(parts[2], createHmac('sha256', secret).update(parts[0] + '.' + parts[1]).digest('base64url'));
      const signedResult = await server.execution(executionId(signedResponse), finished);
      assert.equal(signedResult.status, 'success', signedResult.error);
      assert.ok(!JSON.stringify(signedResult).includes(secret));
    });

    await t.test('response downloads contain the exact text, JSON, binary and sandboxed HTML bytes sent over HTTP', async () => {
      const fileBytes = Buffer.from([0, 1, 2, 3, 10, 13, 255]);
      const cases: { name: string; response: FlowNode; source?: FlowNode; input: Json; method?: string; expectedBytes?: Buffer; html?: boolean }[] = [
        { name: '텍스트', response: responseNode('text', { responseBody: '  한국어 응답\r\n"quoted" 🎉\n', options: { responseCode: 201, responseHeaders: { entries: [{ name: 'Content-Type', value: 'text/plain; charset=utf-8' }] } } }), input: {}, expectedBytes: Buffer.from('  한국어 응답\r\n"quoted" 🎉\n') },
        { name: 'JSON', response: responseNode('json', { responseBody: '={{ $json.body }}', options: { responseCode: 201 } }), input: { message: '고객 "A"\n다음 줄', count: 0, enabled: false, values: [null, '한글'] } },
        { name: '파일', source: code('return [{ json: { marker: "download" }, binary: { data: { data: "AAECAwoN/w==", mimeType: "application/octet-stream", fileName: "검증 결과.bin", fileSize: 7 } } }];'), response: responseNode('binary', { responseDataSource: 'set', inputFieldName: 'data' }), input: {}, expectedBytes: fileBytes },
        { name: 'HTML', response: responseNode('text', { responseBody: '<!doctype html><h1>응답 & 확인</h1><script>document.title="local"</script>' }), input: {}, html: true },
        { name: 'HEAD', response: responseNode('json', { responseBody: '{"head":true}' }), input: {}, method: 'HEAD', expectedBytes: Buffer.alloc(0) },
      ];
      for (const entry of cases) {
        const nodes = [hook('responseNode', { httpMethod: entry.method || 'POST' })]; if (entry.source) nodes.push(entry.source); nodes.push(entry.response);
        const flow = workflow('실제 보낸 응답 내려받기 ' + entry.name, nodes);
        const { url } = await server.activate(flow);
        const original = await server.request(url, entry.method || 'POST', entry.method === 'HEAD' ? undefined : entry.input);
        assert.equal(original.status, entry.name === '텍스트' || entry.name === 'JSON' ? 201 : 200);
        const id = executionId(original), mimeType = original.headers.get('content-type');
        assert.ok(mimeType);
        const bytes = Buffer.from(await original.arrayBuffer());
        if (entry.expectedBytes) assert.deepEqual(bytes, entry.expectedBytes);
        if (entry.name === 'JSON') assert.deepEqual(JSON.parse(bytes.toString('utf8')), entry.input);
        if (entry.html) { assert.match(bytes.toString('utf8'), /<iframe\b/); assert.match(bytes.toString('utf8'), /sandbox=/); assert.equal((bytes.toString('utf8').match(/<iframe\b/g) || []).length, 1); }
        const download = await downloadResponse(id);
        assert.equal(download.status, 200, entry.name);
        assert.equal(download.headers.get('content-type'), mimeType);
        assert.equal(download.headers.get('content-length'), String(bytes.length));
        assert.match(download.headers.get('content-disposition') || '', /^attachment;.*filename\*=UTF-8''/i);
        assert.equal(download.headers.get('x-content-type-options'), 'nosniff');
        assert.deepEqual(Buffer.from(await download.arrayBuffer()), bytes, entry.name + ' must be downloaded without serializing, decoding or sandboxing it a second time');
        assert.equal((await server.execution(id, finished)).status, 'success');
        sentResponses.push({ id, mimeType, bytes });
      }
    });

    await t.test('response lookups reject unknown executions and foreign management origins without creating executions', async () => {
      assert.equal(sentResponses.length, 5);
      const before = ((await server.api('/api/executions')).executions as Execution[]).map(execution => execution.id).sort();
      assert.equal((await downloadResponse()).status, 404);
      assert.equal((await downloadResponse(randomUUID())).status, 404);
      assert.equal((await downloadResponse(sentResponses[0].id, { Origin: 'https://unrelated.example' })).status, 403);
      const foreignHostStatus = await new Promise<number>((resolve, reject) => {
        const request = httpRequest(server.base + '/api/webhook/response?' + new URLSearchParams({ executionId: sentResponses[0].id }), { headers: { Host: 'unrelated.example' }, timeout: 5_000 }, response => { response.resume(); response.once('end', () => resolve(response.statusCode || 0)); response.once('error', reject); });
        request.once('error', reject); request.once('timeout', () => request.destroy(new Error('Foreign Host check timed out'))); request.end();
      });
      assert.equal(foreignHostStatus, 403);
      assert.deepEqual(((await server.api('/api/executions')).executions as Execution[]).map(execution => execution.id).sort(), before);
    });

    await t.test('prepared response downloads survive a full process restart with the same exact bytes', async () => {
      assert.equal(sentResponses.length, 5);
      await server.stop();
      await server.start();
      const before = ((await server.api('/api/executions')).executions as Execution[]).map(execution => execution.id).sort();
      for (const entry of sentResponses) {
        const response = await downloadResponse(entry.id);
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('content-type'), entry.mimeType);
        assert.equal(response.headers.get('content-length'), String(entry.bytes.length));
        assert.match(response.headers.get('content-disposition') || '', /^attachment;.*filename\*=UTF-8''/i);
        assert.deepEqual(Buffer.from(await response.arrayBuffer()), entry.bytes);
        assert.equal((await server.execution(entry.id, finished)).status, 'success');
      }
      assert.deepEqual(((await server.api('/api/executions')).executions as Execution[]).map(execution => execution.id).sort(), before);
    });
  } finally {
    try { await server.stop(); } finally { await rm(directory, { recursive: true, force: true }); }
  }
});
