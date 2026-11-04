import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {existsSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {DeleteObjectCommand, GetObjectCommand, PutObjectCommand, S3Client} from '@aws-sdk/client-s3';
import {agentPython, withDataDirectory} from '../src/paths';

const testDatabase = process.env.A4B_UPLOAD_TEST_DATABASE_URL;
test('workspace upload library opens the original, isolates members and retries failed S3 deletion (' +
  (testDatabase ? 'PostgreSQL' : 'SQLite') + ')',
  {skip: !existsSync(agentPython()), timeout: 150000}, async t => {
    if (testDatabase) {
      const target = new URL(testDatabase);
      assert.equal(target.hostname, '127.0.0.1');
      assert.match(target.pathname, /^\/[a-z0-9_]*test[a-z0-9_]*$/);
      assert.equal(target.username, 'a4b_test_app');
      process.env.A4B_DATABASE_URL = testDatabase;
    }
    const root = await mkdtemp(join(tmpdir(), 'a4b-upload-library-'));
    const member = randomUUID(), other = randomUUID();
    const folder = (id: string) => join(root, 'users', id);
    Object.assign(process.env, {EASYFLOW_DATA_DIR: root, A4B_STORAGE: testDatabase ? 'postgres' : 'sqlite', A4B_S3_BUCKET: 'test-uploads',
      AWS_REGION: 'ap-northeast-2', A4B_DOCUMENT_ROUTING: '0', EASYFLOW_PUBLIC_URL: 'http://localhost'});
    const {builderWorker} = await import('../src/agent-builder');
    const {createWorkspaceRuntime} = await import('../src/workspace-runtime');
    const objects = new Map<string, Buffer>();
    let failDelete = false;
    t.mock.method(S3Client.prototype, 'send', async (command: PutObjectCommand | GetObjectCommand | DeleteObjectCommand) => {
      const key = command.input.Key!;
      assert.ok(key.startsWith('app/v1/users/' + member + '/builder-originals/'));
      if (command instanceof PutObjectCommand) objects.set(key, Buffer.from(command.input.Body as Buffer));
      if (command instanceof GetObjectCommand) return {Body: {transformToByteArray: () => objects.get(key)}};
      if (command instanceof DeleteObjectCommand) {
        if (failDelete) throw Error('S3 unavailable');
        objects.delete(key);
      }
      return {};
    });
    const server = createServer();
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const address = server.address();
    assert.ok(address && typeof address !== 'string');
    const runtime = withDataDirectory(folder(member), () => createWorkspaceRuntime(address.port, member));
    server.on('request', (req, res) => withDataDirectory(folder(member), () => runtime.handle(req, res)));
    t.after(async () => {
      await new Promise<void>(resolve => server.close(() => resolve()));
      await runtime.close();
      await rm(root, {force: true, recursive: true});
    });
    const api = 'http://127.0.0.1:' + address.port + '/api/agent-builder/';
    const session = await withDataDirectory(folder(member), () => builderWorker({action: 'new_session', model: 'test', credential_id: randomUUID()}));
    const attach = () => fetch(api + 'upload', {method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({id: session.id, revision: 0, fileName: 'receipt.txt', data: Buffer.from('receipt').toString('base64')})});
    const upload = await attach();
    const result = await upload.json();
    assert.equal(upload.status, 200, JSON.stringify(result));
    const asset = result.resources[0];
    assert.equal((await attach()).ok, false); // The stale revision must not leave another original.
    assert.equal(objects.size, 1);
    const another = await withDataDirectory(folder(member), () => builderWorker({action: 'new_session', model: 'test', credential_id: randomUUID()}));
    const reuse = await fetch(api + 'attach_asset', {method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({id: another.id, revision: 0, assetId: asset.id})});
    const linked = await reuse.json();
    assert.equal(reuse.status, 200, JSON.stringify(linked));
    assert.deepEqual(linked.file_ids, [asset.id]);
    const list = (await (await fetch(api + 'assets')).json()).assets;
    assert.equal(list.length, 1);
    assert.equal(list[0].name, 'receipt.txt');
    assert.deepEqual(list[0].usages.map((use: {id: string}) => use.id).sort(), [session.id, another.id].sort());
    assert.equal(objects.size, 1);
    assert.equal(await (await fetch(api + 'original?id=' + asset.id)).text(), 'receipt');
    assert.equal((await (await fetch(api + 'asset_preview?id=' + asset.id)).json()).text, 'receipt');
    await withDataDirectory(folder(other), async () => {
      assert.deepEqual((await builderWorker({action: 'assets'})).assets, []);
      await assert.rejects(builderWorker({action: 'asset', id: asset.id}), /찾을 수 없습니다/);
      assert.deepEqual(await builderWorker({action: 'prepare_asset_delete', id: asset.id}), {});
    });
    const remove = () => fetch(api + 'assets?id=' + asset.id, {method: 'DELETE'});
    failDelete = true;
    const failed = await remove();
    assert.equal(failed.status, 503);
    assert.equal((await failed.json()).pending_delete, true);
    await withDataDirectory(folder(other), () => builderWorker({action: 'finish_asset_delete', id: asset.id}));
    assert.equal((await (await fetch(api + 'assets')).json()).assets[0].pending_delete, true);
    assert.equal(objects.size, 1);
    failDelete = false;
    assert.equal((await remove()).ok, true);
    assert.equal(objects.size, 0);
    assert.ok(!existsSync(join(folder(member), asset.original_path)));
    assert.deepEqual((await (await fetch(api + 'assets')).json()).assets, []);
    assert.deepEqual((await (await fetch(api + 'session?id=' + session.id)).json()).resources, []);
  });
