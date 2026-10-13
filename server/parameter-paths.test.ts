import test from 'node:test';
import assert from 'node:assert/strict';
import {getParameter,setParameter,parseJsonParameter} from '../lib/parameter-paths';
test('Nested catalog settings preserve unrelated options and remove legacy dotted fields',()=>{
 const original={mode:'combine',options:{fuzzyCompare:false,clashHandling:{values:{mergeMode:'deepMerge'}}},'options.clashHandling.values.resolveClash':'preferInput1'};
 const result=setParameter(original,'options.clashHandling.values.resolveClash','preferInput2');
 assert.equal(getParameter(result,'options.clashHandling.values.resolveClash'),'preferInput2');
 assert.equal(result.options.clashHandling.values.mergeMode,'deepMerge');assert.equal(result.options.fuzzyCompare,false);
 assert.ok(!Object.hasOwn(result,'options.clashHandling.values.resolveClash'));assert.equal(original['options.clashHandling.values.resolveClash'],'preferInput1');
 assert.deepEqual(parseJsonParameter('={{ $json.customer }}'),'={{ $json.customer }}');assert.deepEqual(parseJsonParameter('{"value":1}'),{value:1});
 assert.throws(()=>setParameter({},'options.__proto__.polluted',true),/경로/);assert.throws(()=>getParameter({},'constructor.prototype'),/경로/);
});
