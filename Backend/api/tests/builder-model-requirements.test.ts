import test from 'node:test';import assert from 'node:assert/strict';import {builderNeedsModel} from '../src/builder-model-requirements';
import {readFileSync,existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {backendPath,agentPython,BACKEND_ROOT} from '../src/paths';
test('model requirement follows pinned graph dependencies and deterministic reuse remains key-free',()=>{
 const call={workflow:{nodes:[{id:'saved',tool:'saved_workflow',settings:{}}]}};
 const pure={config:{workflow:{nodes:[{tool:'read_table',settings:{}}]}},workflow_dependencies:{}};
 assert.equal(builderNeedsModel(call,{saved:pure}),false);
 const model={config:{workflow:{nodes:[{tool:'compose_answer',settings:{}}]}},workflow_dependencies:{}};
 assert.equal(builderNeedsModel(call,{saved:model}),true);
 assert.equal(builderNeedsModel(call,{saved:{config:call,workflow_dependencies:{saved:model}}}),true);
 assert.equal(builderNeedsModel({workflow:{nodes:[{tool:'ask_user',settings:{instructions:'check meaning'}}]}}),true);
});

test('every canonical operation follows its model-connection contract',()=>{
 const catalog=JSON.parse(readFileSync(backendPath('workflow_engine/unified_tools/catalog.json'),'utf8'));
 for(const tool of catalog.tools)for(const operation of tool.operations){
  if(operation.backend_tool)continue;
  assert.equal(builderNeedsModel({workflow:{nodes:[{id:'step',tool:tool.id,settings:{operation:operation.id}}]}}),
   operation.status==='model_connection_required',tool.id+'.'+operation.id);
 }
});

test('Node credential routing agrees with the Python executor for all tool modes',{skip:!existsSync(agentPython())},()=>{
 const script=`import json\nfrom workflow_engine.catalog.registry import NATIVE_IDS\nfrom workflow_engine.catalog.canonical import OPERATIONS\nfrom workflow_engine.library.reusable_workflows import requires_model\nrows=[]\nfor tool in sorted(NATIVE_IDS):\n n={'id':'step','tool':tool,'settings':{'instructions':None,'condition_operator':None}}\n rows.append({'node':n,'requires_model':requires_model({'workflow':{'nodes':[n]}})})\nfor tool,specs in OPERATIONS.items():\n for spec in specs:\n  n={'id':'step','tool':tool,'settings':{'operation':spec['id']}}\n  rows.append({'node':n,'requires_model':requires_model({'workflow':{'nodes':[n]}})})\nprint(json.dumps(rows))`;
 const result=spawnSync(agentPython(),['-c',script],{cwd:BACKEND_ROOT,encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);
 const cases=JSON.parse(result.stdout);
 assert.equal(cases.length,110);
 for(const row of cases)assert.equal(builderNeedsModel({workflow:{nodes:[row.node]}}),row.requires_model,
  row.node.tool+':'+String(row.node.settings.operation||'native'));
});
