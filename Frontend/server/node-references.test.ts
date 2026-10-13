import test from 'node:test';
import assert from 'node:assert/strict';
import {renameNodeReferences} from '../lib/node-references';
import {resolveExpression} from './expressions';

test('rename updates exact modern, legacy node and items reference literals throughout parameters', () => {
  const parameters = {message: "Hello {{ $('이전 이름').first(1, 2).json.name }}", config: {value: '={{ $node["이전 이름"].json.score }}', fallback: "={{ $items('이전 이름', 1, 3)[0].json }}"}, array: ["={{ $('이전 이름').all() }}", {value: true}], plain: "$('이전 이름')"};
  const renamed = renameNodeReferences(parameters, '이전 이름', '새 이름');
  assert.equal(renamed.message, 'Hello {{ $("새 이름").first(1, 2).json.name }}');
  assert.equal(renamed.config.value, '={{ $node["새 이름"].json.score }}');
  assert.equal(renamed.config.fallback, '={{ $items("새 이름", 1, 3)[0].json }}');
  assert.equal(renamed.array[0], '={{ $("새 이름").all() }}');
  assert.equal(renamed.plain, parameters.plain);
  assert.ok(parameters.message.includes("$('이전 이름')"));
});

test('changed name escapes punctuation, quotes and line breaks and resolves with the actual engine', () => {
  const old = '고객 \' " \\\n 검색', next = '새 고객 \' " \\\n\u2028';
  const original = {value: `={{ $(${JSON.stringify(old)}).first(2, 4).json["name"] }}`};
  const renamed = renameNodeReferences(original, old, next);
  assert.equal(resolveExpression(renamed.value, {}, {node: (name, output, run) => {assert.equal(name, next); assert.equal(output, 2); assert.equal(run, 4); return {items: [{name: '성공'}], isExecuted: true};}}), '성공');
  assert.ok(!renamed.value.includes('\u2028'));
});

test('single and double quoted escaped reference names are decoded without evaluation', () => {
  const values = [String.raw`={{ $('A\'B\\C\n').first().json }}`, String.raw`={{ $("A'B\\C\n").first().json }}`];
  for (const value of values) assert.equal(renameNodeReferences({value}, "A'B\\C\n", '새 이름').value, '={{ $("새 이름").first().json }}');
  for (const value of [String.raw`={{ $('\u0041').first().json }}`, String.raw`={{ $("\x41").first().json }}`]) assert.equal(renameNodeReferences({value}, 'A', '새 이름').value, '={{ $("새 이름").first().json }}');
});

test('longer names, other data literals, property names and comments remain unchanged', () => {
  const parameters = {value: String.raw`={{ $('이전 이름 더 길게').first().json + "이전 이름" + "$('이전 이름')" + $json["이전 이름"] + $json.$('이전 이름') + $('이전 이름').first().json /* $('이전 이름') */ }}`, note: "이전 이름과 $('이전 이름')를 설명합니다."};
  const renamed = renameNodeReferences(parameters, '이전 이름', '새 이름');
  assert.ok(renamed.value.includes("$('이전 이름 더 길게')"));
  assert.ok(renamed.value.includes('"이전 이름" + "$(\'이전 이름\')" + $json["이전 이름"]'));
  assert.ok(renamed.value.includes("$json.$('이전 이름')"));
  assert.ok(renamed.value.includes('$("새 이름").first()'));
  assert.ok(renamed.value.includes("/* $('이전 이름') */"));
  assert.equal(renamed.note, parameters.note);
});

test('multiple expression blocks and quoted closing delimiters retain their surrounding text', () => {
  const name = '이전 }} 이름', value = `앞 {{ $(${JSON.stringify(name)}).first().json }} 중간 {{ {label: "}}", value: $(${JSON.stringify(name)}).first().json} }} 끝`;
  const renamed = renameNodeReferences({value}, name, '새 이름');
  assert.equal(renamed.value, '앞 {{ $("새 이름").first().json }} 중간 {{ {label: "}}", value: $("새 이름").first().json} }} 끝');
  assert.equal(renameNodeReferences({value: "={{ $('이전 이름).first().json }}"}, '이전 이름', '새 이름').value, "={{ $('이전 이름).first().json }}");
});

test('free Code and legacy Function parameters are preserved including apparent template blocks', () => {
  const parameters = {jsCode: "// $('이전 이름')\nconst label = `{{ $('이전 이름') }}`; return $('이전 이름').all();", other: "={{ $('이전 이름').first().json }}"};
  for (const nodeType of ['n8n-nodes-base.code', 'n8n-nodes-base.function', 'n8n-nodes-base.functionItem']) assert.strictEqual(renameNodeReferences(parameters, '이전 이름', '새 이름', {nodeType}), parameters);
  assert.strictEqual(renameNodeReferences(parameters, '이전 이름', '새 이름', {kind: 'code'}), parameters);
});

test('rewriting does not invoke object getters or pollute prototypes', () => {
  const parameters = JSON.parse('{"__proto__":{"safe":"={{ $(\\"이전 이름\\").first().json }}"},"normal":42}');
  let reads = 0; Object.defineProperty(parameters, 'danger', {enumerable: true, get: () => {reads++; throw new Error('must not run');}});
  const renamed = renameNodeReferences(parameters, '이전 이름', '새 이름');
  assert.equal(reads, 0); assert.equal(Object.getPrototypeOf(renamed), Object.prototype);
  assert.equal(renamed.__proto__.safe, '={{ $("새 이름").first().json }}');
  assert.equal(({} as any).safe, undefined);
});
