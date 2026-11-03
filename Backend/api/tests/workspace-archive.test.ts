import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {PutObjectCommand, S3Client} from '@aws-sdk/client-s3';
import type {Execution, Flow, FlowNode, Json} from '../../../shared/workflow';
import {withDataDirectory} from '../src/paths';

function node(type: string, typeVersion: number, parameters: Json = {}): FlowNode {
  return {id: randomUUID(), name: type, type: 'n8n-nodes-base.' + type, typeVersion, position: [0, 0], parameters};
}

function workflow(name: string, nodes: FlowNode[]): Flow {
  return {
    id: randomUUID(), name, nodes, settings: {},
    connections: Object.fromEntries(nodes.slice(0, -1).map((source, index) =>
      [source.name, {main: [[{node: nodes[index + 1].name, type: 'main', index: 0}]]}],
    )),
  };
}

test('failed S3 archival preserves the webhook execution error and dispatches its error workflow', {timeout: 15000}, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'a4b-archive-test-'));
  process.env.EASYFLOW_DATA_DIR = directory;
  process.env.A4B_S3_BUCKET = 'test-bucket';
  process.env.AWS_REGION = 'ap-northeast-2';
  const {createWorkspaceRuntime} = await import('../src/workspace-runtime');
  const user = randomUUID();
  const inWorkspace = <T>(action: () => T) => withDataDirectory(join(directory, 'users', user), action);
  let rejectedUploads = 0;
  t.mock.method(S3Client.prototype, 'send', async (command: PutObjectCommand) => {
    assert.ok(command instanceof PutObjectCommand);
    assert.match(command.input.Key ?? '', new RegExp('^app/v1/users/' + user + '/results/'));
    rejectedUploads++;
    throw new Error('simulated S3 outage');
  });

  const server = createServer();
  let runtime: ReturnType<typeof createWorkspaceRuntime> | undefined;
  t.after(async () => {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await runtime?.close();
    await rm(directory, {recursive: true, force: true});
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address !== 'string');
  const workspace = inWorkspace(() => createWorkspaceRuntime(address.port, user));
  runtime = workspace;
  server.on('request', (req, res) => inWorkspace(() => workspace.handle(req, res)));
  const base = 'http://127.0.0.1:' + address.port;
  async function request(path: string, input?: unknown, token?: string) {
    const response = await fetch(base + path, {
      method: input === undefined ? 'GET' : 'POST',
      headers: {'content-type': 'application/json', ...(token ? {'x-easyflow-token': token} : {})},
      body: input === undefined ? undefined : JSON.stringify(input),
      signal: AbortSignal.timeout(3000),
    });
    const data = await response.json();
    assert.equal(response.status, 200, JSON.stringify(data));
    return data;
  }

  const handler = workflow('오류 처리', [node('errorTrigger', 1)]);
  const hook = node('webhook', 2.1, {httpMethod: 'POST', path: 'archive-test'});
  const parent = workflow('결과 파일 생성 후 실패', [
    hook,
    node('convertToFile', 1.1, {operation: 'toJson'}),
    node('stopAndError', 1, {errorType: 'errorMessage', errorMessage: '원래 실행 오류'}),
  ]);
  parent.settings.errorWorkflow = handler.id;
  await request('/api/workflows', handler);
  await request('/api/workflows', parent);
  const {workflow: active} = await request('/api/activation', {workflowId: parent.id, active: true});
  const {executionId} = await request('/hooks/' + user + '/' + parent.id + '/' + hook.id, {example: true}, active.hookToken);
  async function waitForExecution(id: string, ready: (execution: Execution) => boolean): Promise<Execution> {
    for (let attempt = 0; attempt < 100; attempt++) {
      const {execution} = await request('/api/executions?id=' + id);
      if (ready(execution)) return execution;
      await delay(20);
    }
    assert.fail('Execution did not reach the expected state: ' + id);
  }
  const failed = await waitForExecution(executionId, execution => Boolean(execution.errorExecutionId));
  assert.equal(rejectedUploads, 1);
  assert.equal(failed.status, 'error');
  assert.equal(failed.error, '원래 실행 오류');
  const child = await waitForExecution(failed.errorExecutionId!, execution => execution.status === 'success');
  assert.equal(child.parentExecutionId, failed.id);
  assert.equal(child.runs[0].output[0][0].execution.error.message, '원래 실행 오류');
});
