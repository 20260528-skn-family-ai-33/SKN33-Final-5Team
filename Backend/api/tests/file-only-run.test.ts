import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import type {IncomingMessage, ServerResponse} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {handleAgentBuilder} from '../src/agent-builder';
import {agentPython, BACKEND_ROOT, withDataDirectory} from '../src/paths';
import type {Store} from '../src/store';

test('a saved file workflow accepts an upload without a request via the API', {skip:!existsSync(agentPython())}, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'a4b-file-only-run-'));
  try {
    const setup = spawnSync(agentPython(), ['-c', `
import sys
from pathlib import Path
from tests.workflow_engine.test_workflow import bind, edge, node, saved
w = {'entry':'read', 'nodes':[node('read','read_table'),
     node('export','write_result',[bind('table','read','table')],format='csv')],
     'edges':[edge('read','export')]}
storage, agent_id, _ = saved(Path(sys.argv[1]), w)
storage.close()
print(agent_id)
`, directory], {cwd:BACKEND_ROOT, encoding:'utf8'});
    assert.equal(setup.status, 0, setup.stderr);
    const id = setup.stdout.trim();
    async function call(action:string, data:Record<string,unknown>) {
      let result:any;
      await withDataDirectory(directory, () => handleAgentBuilder(
        {method:'POST'} as IncomingMessage, {setHeader(){}} as unknown as ServerResponse,
        new URL('http://localhost/api/agent-builder/'+action), {} as Store,
        async () => ({id, ...data}), (_res, status, value) => { assert.equal(status, 200); result=value; }
      ));
      return result;
    }
    const {asset} = await call('run_upload', {fileName:'current.csv', data:Buffer.from('item\ncurrent\n').toString('base64')});
    const run = await call('test', {fileIds:[asset.id]});
    assert.equal(run.status, 'succeeded', run.error);
    assert.equal(run.request, '');
    assert.deepEqual(run.file_ids, [asset.id]);
    assert.match(run.artifact.content, /current/);
    for (const content of [null, false, '가'.repeat(4001)]) {
      await assert.rejects(call('test', {content, fileIds:[asset.id]}), /4,000/);
    }
    await assert.rejects(call('resume_run', {content:'   '}), /1~4,000/);
  } finally {
    await rm(directory, {recursive:true, force:true});
  }
});
