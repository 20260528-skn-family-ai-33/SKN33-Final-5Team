import test from 'node:test';
import assert from 'node:assert/strict';
import {nodeSelectionPool} from '../app/node-selection';

const v1={id:'role-id',version:1,ref:'role-id:1',name:'처음 선택한 역할'};
const v2={id:'role-id',version:2,ref:'role-id:2',name:'새 이름과 지시사항'};
const builtin={id:'builtin-id',version:1,ref:'builtin-id:1',name:'문서 읽기'};

test('selected previous node version stays visible when the library returns only its newer version',()=>{
 const latest=[builtin,v2];
 const choices=nodeSelectionPool(latest,[v1],[v1.ref]);
 assert.deepEqual(choices,[builtin,v2,v1]);
 const selected=choices.filter(node=>node.ref===v1.ref);
 assert.equal(selected[0].version,1);
 assert.equal(selected[0].name,'처음 선택한 역할');
 assert.deepEqual(latest,[builtin,v2]);
});

test('unselecting an older version removes it from the selection pool without removing the latest node',()=>{
 assert.deepEqual(nodeSelectionPool([builtin,v2],[v1],[v2.ref]),[builtin,v2]);
 assert.deepEqual(nodeSelectionPool([builtin,v2],[v1],[]),[builtin,v2]);
});

test('selecting a current version does not create duplicate choices and keeps its selected snapshot',()=>{
 const refetched={...v1,name:'refetched display value'};
 const choices=nodeSelectionPool([builtin,refetched],[v1],[v1.ref]);
 assert.equal(choices.length,2);
 assert.deepEqual(choices,[builtin,v1]);
});
