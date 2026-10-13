import test from 'node:test';
import assert from 'node:assert/strict';
import {allItemsExpression, buildValuePaths, valueExpression, type ValueSource} from '../lib/value-paths';
import {resolveExpression} from './expressions';

const input = (items: ValueSource['items']): ValueSource => ({kind: 'input', outputIndex: 0, items});

test('current input selections use the running item while preview samples only the first item', () => {
  const source = input([{customer: {name: '첫 고객'}, totals: [11, 22]}, {customer: {name: '둘째 고객'}, laterOnly: true}]);
  const data = buildValuePaths([source]);
  const name = data.paths.find(row => row.label === 'customer.name')!;
  assert.equal(name.preview, '"첫 고객"');
  assert.equal(resolveExpression(name.expression, source.items[1], {input: source.items, itemIndex: 1}), '둘째 고객');
  const total = data.paths.find(row => row.label === 'totals[1]')!;
  assert.equal(resolveExpression(total.expression, source.items[0]), 22);
  assert.ok(!data.paths.some(row => row.label === 'laterOnly'));
});

test('node names and nested keys escape quotes, slash, newlines and Unicode without changing branch or run', () => {
  const name = '고객 "검색" \' 단계\\\n\u2028';
  const key = '고객 "주소" \'\\\n}}\u2029';
  const source: ValueSource = {kind: 'node', nodeId: 'n1', nodeName: name, outputIndex: 2, runIndex: 4, items: [{[key]: {list: [{'배송/상태': '도착'}]}}]};
  const path = [key, 'list', 0, '배송/상태'];
  const expression = valueExpression(source, path, 'first');
  let calls = 0;
  const result = resolveExpression(expression, {}, {node: (selected, output, run) => {
    calls++; assert.equal(selected, name); assert.equal(output, 2); assert.equal(run, 4);
    return {items: source.items, isExecuted: true};
  }});
  assert.equal(result, '도착'); assert.equal(calls, 1);
  assert.ok(expression.includes('.first(2, 4).json'));
  assert.ok(!expression.includes('.item'));
  assert.ok(!expression.includes('\u2028'));
});

test('all selections are item envelopes and explicitly retain node output and run indices', () => {
  const current = input([{x: 1}, {x: 2}]);
  assert.equal(allItemsExpression(current), '={{ $input.all() }}');
  assert.deepEqual(resolveExpression(allItemsExpression(current), current.items[0], {input: current.items}), [{json: {x: 1}}, {json: {x: 2}}]);
  const source: ValueSource = {kind: 'node', nodeName: '분기', outputIndex: 1, runIndex: 0, items: [{yes: true}]};
  assert.equal(allItemsExpression(source), '={{ $("분기").all(1, 0) }}');
  assert.deepEqual(resolveExpression(allItemsExpression(source), {}, {node: (name, output, run) => {assert.equal(name, '분기'); assert.equal(output, 1); assert.equal(run, 0); return {items: source.items, isExecuted: true};}}), [{json: {yes: true}}]);
  assert.equal(valueExpression({...source, runIndex: undefined}, ['yes'], 'first'), '={{ $("분기").first(1).json["yes"] }}');
});

test('secret keys, token-shaped values, binary internals and forbidden keys never appear in examples', () => {
  const item = JSON.parse('{"profile":{"name":"고객","password":"password-secret","clientSecret":"client-secret"},"access_token":"access-secret","headers":{"Authorization":"Bearer abcdefghijklmnop"},"__easyflowBinary":{"data":"binary-secret"},"constructor":{"leak":"prototype-secret"},"__proto__":{"leak":"proto-secret"},"value":"sk-proj-abcdefghijklmnopqrstuvwxyza","jwt":"eyJabcdefghijk.abcdefghijkl.abcdefghijk","normal":"안전한 결과"}');
  const data = buildValuePaths([input([item])]);
  const serialized = JSON.stringify(data);
  for (const secret of ['password-secret', 'client-secret', 'access-secret', 'binary-secret', 'prototype-secret', 'proto-secret', 'sk-proj-abcdefghijklmnopqrstuvwxyza', 'eyJabcdefghijk.abcdefghijkl.abcdefghijk']) assert.ok(!serialized.includes(secret));
  for (const key of ['__easyflowBinary', '__proto__', 'constructor', 'profile.password', 'access_token', 'headers.Authorization']) assert.ok(!data.paths.some(row => row.label.includes(key)));
  assert.ok(data.paths.some(row => row.label === 'profile.name'));
  assert.ok(data.paths.some(row => row.preview.includes('안전한 결과')));
  assert.ok(data.hiddenCount >= 8);
  for (const key of ['__proto__', 'prototype', 'constructor', 'caller', 'callee', 'arguments', '__easyflowBinary', 'password']) assert.throws(() => valueExpression(input([{}]), ['safe', key]));
});

test('enumeration is globally capped at 500 paths and nested structures stop at eight levels', () => {
  const many = Object.fromEntries(Array.from({length: 700}, (_, index) => ['field' + index, index]));
  const capped = buildValuePaths([input([many]), {kind: 'node', nodeName: '다른 단계', outputIndex: 0, items: [{x: 1}]}]);
  assert.equal(capped.paths.length, 500); assert.equal(capped.truncated, true);
  const nested: Record<string, any> = {}; let cursor = nested;
  for (let index = 0; index < 20; index++) {cursor.next = {}; cursor = cursor.next;} cursor.answer = 42;
  const depth = buildValuePaths([input([nested])]);
  assert.equal(Math.max(...depth.paths.map(row => row.path.length)), 8); assert.equal(depth.depthLimited, true);
  assert.ok(!depth.paths.some(row => row.label.endsWith('answer')));
  assert.equal(buildValuePaths([input([many])], {maxPaths: 20}).paths.length, 20);
});

test('empty output asks for execution and invalid metadata creates no executable selections', () => {
  const empty = buildValuePaths([input([]), {kind: 'node', nodeName: '조건 결과', outputIndex: 1, items: []}]);
  assert.equal(empty.paths.length, 0);
  assert.ok(empty.groups.every(group => group.status === 'empty' && group.allExpression === undefined));
  for (const outputIndex of [-1, NaN, Infinity, 1.5, 1_000_001]) {
    const source: ValueSource = {kind: 'node', nodeName: '결과', outputIndex, items: [{x: 1}]};
    assert.equal(buildValuePaths([source]).groups[0].status, 'invalid');
    assert.throws(() => allItemsExpression(source));
  }
  assert.equal(buildValuePaths([{kind: 'node', nodeName: '', outputIndex: 0, items: [{}]}]).paths.length, 0);
  assert.throws(() => valueExpression(input([{}]), [-1]));
});

test('object previews do not invoke getters, and unusual values do not break bounded traversal', () => {
  let reads = 0;
  const item: Record<string, any> = {safe: false, empty: '', nothing: null, bad: Infinity, unsupported: () => 'never'};
  Object.defineProperty(item, 'getter', {enumerable: true, get: () => {reads++; return 'secret';}});
  item.cycle = item;
  const data = buildValuePaths([input([item])]);
  assert.equal(reads, 0); assert.equal(data.depthLimited, true);
  assert.ok(data.paths.some(row => row.label === 'safe' && row.preview === 'false'));
  assert.ok(data.paths.some(row => row.label === 'empty' && row.preview.includes('빈 글자')));
  assert.ok(data.paths.some(row => row.label === 'nothing' && row.type === 'null'));
  assert.ok(!data.paths.some(row => ['getter', 'bad', 'unsupported'].includes(row.label)));
});

test('oversized escaped paths and many empty sources remain bounded without breaking the picker', () => {
  const longKey = '\\'.repeat(512), nested: Record<string, any> = {}; let cursor = nested;
  for (let index = 0; index < 8; index++) {cursor[longKey] = {}; cursor = cursor[longKey];}
  const result = buildValuePaths([input([nested])]);
  assert.equal(result.depthLimited, true);
  assert.ok(result.paths.every(row => row.expression.length < 8000));
  const empty = buildValuePaths(Array.from({length: 700}, () => input([])));
  assert.equal(empty.groups.length, 500); assert.equal(empty.truncated, true);
});

test('proven linked values use item with escaped names and paths while first stays explicit', () => {
  const name='고객 "조회"\\\n\u2028', key='주소 "동"\\\n\u2029';
  const source:ValueSource={kind:'node',nodeName:name,outputIndex:2,runIndex:4,items:[{[key]:{count:1,enabled:false}}],linkedItem:{status:'ready',sample:{[key]:{count:9,enabled:true}},inputCount:2}};
  const data=buildValuePaths([source]);
  const row=data.paths.find(value=>value.path.at(-1)==='count')!;
  assert.equal(row.type,'number');assert.equal(row.preview,'9');assert.equal(data.groups[0].selection,'linked');assert.equal(data.groups[0].linkedAvailable,true);
  assert.ok(row.expression.includes('.item.json'));assert.ok(!row.expression.includes('\u2028'));assert.ok(!row.expression.includes('\u2029'));
  let calls=0;
  assert.equal(resolveExpression(row.expression,{}, {input:[{},{}],itemIndex:1,linkedItem:(selected,index)=>{calls++;assert.equal(selected,name);assert.equal(index,1);return {[key]:{count:15}};}}),15);assert.equal(calls,1);
  assert.equal(data.groups[0].allExpression,allItemsExpression(source));
  const first=buildValuePaths([source],{nodeSelection:'first'}).paths.find(value=>value.path.at(-1)==='count')!;
  assert.equal(first.preview,'1');assert.ok(first.expression.includes('.first(2, 4).json'));
  assert.equal(valueExpression(source,[key,'enabled'],'first'),buildValuePaths([source],{nodeSelection:'first'}).paths.find(value=>value.path.at(-1)==='enabled')!.expression);
});

test('linked previews hide sensitive values and linked-only history cannot generate first/all', () => {
  const source:ValueSource={kind:'node',nodeName:'원본',outputIndex:1,items:[{safe:'first'}],explicitSelection:false,linkedItem:{status:'ready',sample:{safe:'linked',apiKey:'never visible',value:'sk-proj-abcdefghijklmnopqrstuvwxyza',__easyflowBinary:{data:'never visible'},constructor:{leak:'never visible'}},inputCount:2}};
  const data=buildValuePaths([source]);
  assert.ok(data.paths.some(value=>value.label==='safe'&&value.preview==='"linked"'));
  assert.ok(!JSON.stringify(data).includes('never visible'));assert.ok(!JSON.stringify(data).includes('sk-proj-abcdefghijklmnopqrstuvwxyza'));assert.ok(data.hiddenCount>=4);
  assert.equal(data.groups[0].allExpression,undefined);
  assert.throws(()=>allItemsExpression(source),/현재 범위/);assert.throws(()=>valueExpression(source,['safe'],'first'),/현재 범위/);
  const first=buildValuePaths([source],{nodeSelection:'first'});assert.equal(first.paths.length,0);assert.match(first.groups[0].selectionUnavailable!,/이전 반복/);
  const unavailable:ValueSource={...source,explicitSelection:true,linkedItem:{status:'unavailable',reason:'여러 원본 항목이 합쳐졌어요.'}};
  const unavailablePaths=buildValuePaths([unavailable]);assert.equal(unavailablePaths.groups[0].selection,'linked');assert.equal(unavailablePaths.groups[0].linkedReason,'여러 원본 항목이 합쳐졌어요.');assert.equal(unavailablePaths.paths.length,0);assert.ok(unavailablePaths.groups[0].allExpression);assert.match(unavailablePaths.groups[0].selectionUnavailable!,/위에서 첫 항목/);
  assert.throws(()=>valueExpression(unavailable,['safe']),/원본 연결/);
  const explicitFirst=buildValuePaths([unavailable],{nodeSelection:'first'});assert.match(explicitFirst.paths[1].expression,/\.first\(1\)/);
});

test('unknown linking metadata requires an explicit first selection instead of guessing',()=>{
  const source:ValueSource={kind:'node',nodeName:'이전 단계',outputIndex:0,items:[{name:'고객 A'}]};
  assert.throws(()=>valueExpression(source,['name']),/원본 연결/);
  const data=buildValuePaths([source]);assert.equal(data.paths.length,0);assert.match(data.groups[0].selectionUnavailable!,/원본 연결 기록/);assert.ok(data.groups[0].allExpression);
  assert.equal(buildValuePaths([source],{nodeSelection:'first'}).paths[1].expression,'={{ $("이전 단계").first(0).json["name"] }}');
});
