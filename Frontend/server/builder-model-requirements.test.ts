import test from 'node:test';import assert from 'node:assert/strict';import {builderNeedsModel} from './builder-model-requirements';
test('model requirement follows pinned graph dependencies and deterministic reuse remains key-free',()=>{
 const call={workflow:{nodes:[{id:'saved',tool:'saved_workflow',settings:{}}]}};
 const pure={config:{workflow:{nodes:[{tool:'read_table',settings:{}}]}},workflow_dependencies:{}};
 assert.equal(builderNeedsModel(call,{saved:pure}),false);
 const model={config:{workflow:{nodes:[{tool:'compose_answer',settings:{}}]}},workflow_dependencies:{}};
 assert.equal(builderNeedsModel(call,{saved:model}),true);
 assert.equal(builderNeedsModel(call,{saved:{config:call,workflow_dependencies:{saved:model}}}),true);
 assert.equal(builderNeedsModel({workflow:{nodes:[{tool:'ask_user',settings:{instructions:'check meaning'}}]}}),true);
});
