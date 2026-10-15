import {dataDirectory,examplePath} from '../server/paths';
/** Read-only verification of the actual local demo. Never print credential values. */
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,join} from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {Store} from '../server/store';
import {parseCsv} from '../server/binary';
const directory=dataDirectory();
const db=new DatabaseSync(join(directory,'agent-builder.sqlite'),{readOnly:true});
const row=db.prepare("SELECT json FROM agent_tests WHERE json_extract(json,'$.status')='succeeded' ORDER BY rowid DESC LIMIT 1").get();
assert.ok(row,'finish one actual agent test before verification');
const run=JSON.parse(String(row.json));const plan=run.config_snapshot.execution_plan;
assert.ok(plan,'successful test must use a stored execution plan');
const read=run.steps.find((step:any)=>step.node==='read_table');assert.ok(read);
const source=JSON.parse(String(db.prepare('SELECT rows_json FROM builder_assets WHERE id=?').get(read.source_file_id)?.rows_json));
// Independent reference calculation for the shipped inventory scenario.
const seen=new Set<string>();const unique=source.filter((r:any)=>{if(seen.has(r.product_code))return false;seen.add(r.product_code);return true;});
const expected=unique.filter((r:any)=>r.stock!==''&&r.stock!==null&&Number(r.stock)<Number(r.reorder_level)).sort((a:any,b:any)=>Number(a.stock)-Number(b.stock));
assert.deepEqual(run.rows,expected);assert.deepEqual(run.rows.map((r:any)=>[r.product_code,Number(r.stock)]),[['B',3],['C',5],['A',20]]);
assert.equal(parseCsv(run.artifact.content).length,3);
assert.equal(plan.duplicate_policy,'first');assert.equal(plan.missing_policy,'exclude');assert.equal(plan.output_format,'csv');
assert.equal(run.steps.at(-1).node,'write_result');assert.equal(source.length,6);
const original=readFileSync(examplePath('agent-builder/재고_시험.csv'));assert.deepEqual(source,parseCsv(original.toString('utf8')));
const asset=JSON.parse(String(db.prepare('SELECT json FROM builder_assets WHERE id=?').get(read.source_file_id)?.json));assert.equal(asset.hash,createHash('sha256').update(original).digest('hex'));
const versions=db.prepare('SELECT version FROM agent_versions WHERE agent_id=? ORDER BY version').all(run.agent_id);
assert.ok(versions.length>=2,'test the settings edit/save flow to confirm immutable versions');
const credentials=new Store(directory);let checked=0;
for(const c of credentials.listCredentials().filter(c=>c.provider==='openai')){const secret=credentials.getCredential(c.id).data.apiKey;if(typeof secret!=='string'||!secret)continue;for(const suffix of ['','-wal']){const file=join(directory,'agent-builder.sqlite'+suffix);if(existsSync(file))assert.ok(!readFileSync(file).includes(Buffer.from(secret)),'raw API key must not be checkpointed');}checked++;}
credentials.close();db.close();
console.log(JSON.stringify({actualAgentVersion:run.agent_version,expectedRows:3,resultRows:run.rows.length,orderedStocks:run.rows.map((r:any)=>Number(r.stock)),actualTools:run.steps.filter((s:any)=>s.status==='completed').map((s:any)=>s.node),immutableVersions:versions.map(v=>v.version),rawApiKeyAbsent:checked>0,sourceUnchanged:true},null,2));
