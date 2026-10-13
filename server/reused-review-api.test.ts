import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {createServer, type Server} from 'node:http';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {handleAgentBuilder} from './agent-builder';
import {Store} from './store';

const python = process.env.A4B_AGENT_PYTHON || resolve('.venv-agent/bin/python');
const candidate = '검수 의견을 반영한 최신 응대문입니다.';

// Real checkpointed child graphs and SQLite; only the provider responses are mocked.
const fixtureCode = `
import copy,json,sys
from pathlib import Path
sys.path.insert(0,'python')
from agent_builder.test_reply_review import seeded,output,REQUEST
from agent_builder.test_node_connections import save
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow_runtime import run_workflow
import agent_builder.reply_review as review_module
import agent_builder.workflow_runtime as runtime

review_module.run_review=lambda *args:output()
answer={'text':'${candidate}','citations':[{'evidence_id':'e1'}]}
class Business:
    def invoke(self,*args,**kwargs):return {'structured_response':copy.deepcopy(answer)}
class Model:
    def with_structured_output(self,*args,**kwargs):return self
    def invoke(self,*args,**kwargs):return copy.deepcopy(answer)
runtime.create_deep_agent=lambda **kwargs:Business()
runtime.ChatOpenAI=lambda **kwargs:(_ for _ in ()).throw(AssertionError('real provider construction is forbidden'))

st,seed=seeded(Path(sys.argv[1]))
base=copy.deepcopy(st.agent(seed)['config'])
bottom=copy.deepcopy(base)
bottom.update(tool_ids=['read_document','reply_review','compose_answer','condition','finish'],workflow={
    'entry':'docs',
    'nodes':[node('docs','read_document'),
             node('review','reply_review',[bind('documents','docs','documents')]),
             node('rewrite','compose_answer',[bind('documents','docs','documents'),bind('reviews','review','reviews')]),
             node('decision','condition',[bind('reviews','review','reviews')],condition_operator='any_revision'),
             node('done','finish',[bind('text','rewrite','text')]),
             node('passed','finish',[bind('text','rewrite','text')])],
    'edges':[edge('docs','review'),edge('review','rewrite'),edge('rewrite','decision'),edge('decision','done','yes'),edge('decision','passed','no')]})
bottom_id=save(st,bottom)
middle=copy.deepcopy(base)
middle.update(tool_ids=['saved_workflow','finish'],workflow={
    'entry':'inner',
    'nodes':[node('inner','saved_workflow',agent_id=bottom_id,agent_version=1,output_node='rewrite',output_port='text'),
             node('done','finish',[bind('text','inner','text')])],
    'edges':[edge('inner','done')]})
middle_id=save(st,middle)
outer=copy.deepcopy(base)
outer.update(tool_ids=['saved_workflow','finish'],workflow={
    'entry':'first',
    'nodes':[node('first','saved_workflow',agent_id=middle_id,agent_version=1,output_node='done',output_port='text'),
             node('second','saved_workflow',agent_id=middle_id,agent_version=1,output_node='done',output_port='text'),
             node('done','finish',[bind('text','second','text')])],
    'edges':[edge('first','second'),edge('second','done')]})
agent_id=save(st,outer)
run=run_workflow(st,agent_id,REQUEST.customer_message,'unused',reply_draft=REQUEST.reply_draft,llm=Model())
assert run['status']=='succeeded',run.get('error')
assert run['artifact'] is None and not run.get('report_path')
print(json.dumps({'agent':agent_id,'run':run['id'],'original_candidate':output()['report']['revised_reply']}))
st.close()
`;

function apiServer(store: Store): Server {
  return createServer((req, res) => {
    void (async () => {
      const json = (response: any, status: number, value: any) => {
        response.writeHead(status, {'Content-Type': 'application/json'});
        response.end(JSON.stringify(value));
      };
      try {
        const body = async () => {
          let raw = '';
          for await (const chunk of req) raw += chunk;
          return JSON.parse(raw);
        };
        if (!await handleAgentBuilder(req, res, new URL(req.url!, 'http://127.0.0.1'), store, body, json)) json(res, 404, {});
      } catch (error) {
        json(res, 400, {error: (error as Error).message});
      }
    })();
  });
}

async function listen(server: Server): Promise<string> {
  await new Promise<void>((done, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', done);
  });
  const address = server.address();
  assert.ok(address && typeof address !== 'string');
  return `http://127.0.0.1:${address.port}/api/agent-builder/`;
}

async function close(server: Server): Promise<void> {
  await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done()));
}

test('nested reused reviews retain the latest candidate and stable approval IDs across the API and restart', {skip: !existsSync(python)}, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'a4b-reused-review-api-'));
  const previous = process.env.EASYFLOW_DATA_DIR;
  process.env.EASYFLOW_DATA_DIR = directory;
  let store = new Store(directory);
  let server = apiServer(store);
  try {
    const setup = spawnSync(python, ['-c', fixtureCode, directory], {encoding: 'utf8'});
    assert.equal(setup.status, 0, setup.stderr);
    const fixture = JSON.parse(setup.stdout.trim());
    let base = await listen(server);
    async function call(action: string, data?: unknown): Promise<Response> {
      return fetch(base + action, {
        method: data ? 'POST' : 'GET',
        headers: data ? {'Content-Type': 'application/json'} : {},
        body: data ? JSON.stringify(data) : undefined,
      });
    }

    const pendingResponse = await call('run?id=' + fixture.run);
    assert.equal(pendingResponse.status, 200);
    const pending = (await pendingResponse.json() as any).run;
    assert.equal(pending.approval_status, 'pending');
    assert.deepEqual(pending.reviews.map((r: any) => r.id), ['first:0', 'second:0']);
    for (const review of pending.reviews) {
      assert.match(review.id, /^[-\w]+:\d+$/);
      assert.equal(review.candidate_reply, candidate);
      assert.equal(review.candidate_revision, 1);
      assert.equal(review.report.revised_reply, fixture.original_candidate);
      assert.equal(review.source_review_id, 'review:0');
      assert.equal(review.decision, null);
    }
    assert.equal(pending.artifact, null);
    const emptyCsv = await (await call('approved_replies?id=' + fixture.agent)).text();
    assert.equal(emptyCsv.trim().split(/\r?\n/).length, 1);

    const payload = {id: fixture.run, reviewId: 'first:0', decision: 'approve', editedReply: '운영자가 최종 확인한 수정 응대'};
    const approvalResponse = await call('review_decision', payload);
    assert.equal(approvalResponse.status, 200);
    const approval = await approvalResponse.json() as any;
    assert.equal(approval.reviews[0].decision.edited_reply, payload.editedReply);
    const duplicatedResponse = await call('review_decision', payload);
    assert.equal(duplicatedResponse.status, 200);
    const duplicated = await duplicatedResponse.json() as any;
    assert.deepEqual(duplicated.reviews[0].decision, approval.reviews[0].decision);
    assert.equal((await call('review_decision', {...payload, editedReply: '다른 문장'})).status, 400);
    assert.equal((await call('review_decision', {...payload, reviewId: 'second:0', decision: 'reject', editedReply: ''})).status, 200);

    const csvResponse = await call('approved_replies?id=' + fixture.agent);
    assert.equal(csvResponse.status, 200);
    const csv = await csvResponse.text();
    assert.equal(csv.trim().split(/\r?\n/).length, 2);
    assert.ok(csv.includes('first:0') && csv.includes(payload.editedReply));
    assert.ok(!csv.includes('second:0') && !csv.includes(candidate) && !csv.includes(fixture.original_candidate));

    const reportResponse = await call('review_report?id=' + fixture.run);
    assert.equal(reportResponse.status, 200);
    assert.match(reportResponse.headers.get('content-disposition') || '', /attachment/);
    const report = await reportResponse.json() as any;
    assert.equal(report.reviews.length, 2);
    assert.equal(report.reviews[0].candidate_reply, candidate);
    assert.equal(report.reviews[0].report.revised_reply, fixture.original_candidate);
    assert.equal(report.reviews[0].decision.edited_reply, payload.editedReply);
    assert.equal(report.reviews[1].decision.decision, 'reject');

    await close(server);
    store.close();
    store = new Store(directory);
    server = apiServer(store);
    base = await listen(server);
    const restored = (await (await call('run?id=' + fixture.run)).json() as any).run;
    assert.equal(restored.approval_status, 'decided');
    assert.deepEqual(restored.reviews, report.reviews);
    const history = await (await call('agent?id=' + fixture.agent)).json() as any;
    assert.deepEqual(history.tests[0].reviews, restored.reviews);
    assert.equal(await (await call('approved_replies?id=' + fixture.agent)).text(), csv);
  } finally {
    if (server.listening) await close(server);
    store.close();
    if (previous === undefined) delete process.env.EASYFLOW_DATA_DIR;
    else process.env.EASYFLOW_DATA_DIR = previous;
    await rm(directory, {recursive: true, force: true});
  }
});
