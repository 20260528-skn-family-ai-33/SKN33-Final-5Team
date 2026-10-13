import test from 'node:test';
import assert from 'node:assert/strict';
import {DISPLAY_LIMITS, prepareDisplayData, sanitizeDisplayData, printableDisplayData, filterDisplayRows, paginateData, displayColumns, inferDisplaySchema, fileDownloadHref, displayCell, fileSizeLabel} from '../lib/data-display';

test('reserved binary payload stays out of JSON/search/schema while ordinary user columns remain unchanged', () => {
  const item = {base64: 'YWJjZA==', data: '일반 데이터', pairedItem: {item: 2}, source: {title: '사용자 원본'}, nested: {data: '그대로', __easyflowBinary: {hidden: {data: 'nested-payload'}}}, __easyflowBinary: {upload: {data: 'secret-file-payload', fileName: '업로드.pdf', mimeType: 'application/pdf', fileSize: 2048}}};
  const display = prepareDisplayData([item]);
  assert.deepEqual(display.rows[0].value, {base64: 'YWJjZA==', data: '일반 데이터', pairedItem: {item: 2}, source: {title: '사용자 원본'}, nested: {data: '그대로'}});
  for (const text of [JSON.stringify(display), printableDisplayData([item]), JSON.stringify(inferDisplaySchema(display.rows))]) {
    assert.ok(!text.includes('__easyflowBinary')); assert.ok(!text.includes('secret-file-payload')); assert.ok(!text.includes('nested-payload'));
  }
  assert.equal(item.__easyflowBinary.upload.data, 'secret-file-payload');
  assert.deepEqual(display.files, [{itemIndex: 0, property: 'upload', fileName: '업로드.pdf', mimeType: 'application/pdf', fileSize: 2048}]);
});

test('case-insensitive value and filename search retains original item indexes for input and output downloads', () => {
  const items = Array.from({length: 60}, (_, index) => ({name: index === 47 ? 'Selected customer' : '다른 고객', __easyflowBinary: {['첨부 & ?/' + index]: {data: 'never-show-body', fileName: index === 47 ? 'REPORT.PDF' : '다른 파일.txt', mimeType: 'application/pdf', fileSize: 1024}}}));
  const display = prepareDisplayData(items), selected = filterDisplayRows(display.rows, ' report.pdf ');
  assert.deepEqual(selected.map(row => row.index), [47]);
  assert.deepEqual(filterDisplayRows(display.rows, 'CUSTOMER').map(row => row.index), [47]);
  assert.equal(filterDisplayRows(display.rows, 'never-show-body').length, 0);
  const file = display.files.find(value => value.itemIndex === 47)!;
  for (const side of ['input', 'output'] as const) {
    const url = new URL(fileDownloadHref(file, {executionId: 'execution & x', runIndex: 6, side, output: 2}), 'http://127.0.0.1');
    assert.equal(url.pathname, '/api/files/download');
    assert.equal(url.searchParams.get('executionId'), 'execution & x'); assert.equal(url.searchParams.get('runIndex'), '6');
    assert.equal(url.searchParams.get('side'), side); assert.equal(url.searchParams.get('output'), '2'); assert.equal(url.searchParams.get('item'), '47');
    assert.equal(url.searchParams.get('property'), '첨부 & ?/47');
  }
});

test('pagination clamps oversized/negative pages and limits rendering to at most 100 items', () => {
  const values = Array.from({length: 105}, (_, index) => index);
  assert.deepEqual(paginateData(values, 0, 20).values, values.slice(0, 20));
  const last = paginateData(values, 99, 20);
  assert.deepEqual(last.values, [100, 101, 102, 103, 104]); assert.equal(last.page, 5); assert.equal(last.start, 101); assert.equal(last.end, 105);
  assert.equal(paginateData(values, -10, -1).size, 1); assert.equal(paginateData(values, NaN, Infinity).size, 20);
  assert.equal(paginateData(values, 0, 1000).values.length, 100);
  assert.deepEqual(paginateData([], 900, 50), {values: [], page: 0, pages: 1, size: 50, total: 0, start: 0, end: 0});
});

test('schema joins actual value types, tracks missing fields and samples array members without counting a row twice', () => {
  const rows = prepareDisplayData([{customer: {name: '김민지'}, score: 12, optional: null, orders: [{id: 1}, {id: '둘'}]}, {customer: {name: '박서준'}, score: '13', orders: [{id: false}]}, {customer: {}, score: 14}]).rows;
  const schema = inferDisplaySchema(rows), field = (path: string) => schema.fields.find(value => value.path === path)!;
  assert.deepEqual(field('score').types, ['숫자', '글자']); assert.equal(field('score').present, 3);
  assert.equal(field('customer.name').present, 2); assert.deepEqual(field('optional').types, ['빈 값']); assert.equal(field('optional').present, 1);
  assert.deepEqual(field('orders[].id').types, ['숫자', '글자', '참/거짓']); assert.equal(field('orders[].id').present, 2);
  const escaped = inferDisplaySchema(prepareDisplayData([{'x.y': 1, x: {y: 2}}]).rows);
  assert.ok(escaped.fields.some(value => value.path === '["x.y"]')); assert.ok(escaped.fields.some(value => value.path === 'x.y'));
});

test('row, column, schema field and depth limits report a bounded view', () => {
  const large = Array.from({length: DISPLAY_LIMITS.rows + 1}, (_, index) => ({value: index}));
  const rows = prepareDisplayData(large); assert.equal(rows.rows.length, 5000); assert.equal(rows.truncated, true);
  const wide = prepareDisplayData([{...Object.fromEntries(Array.from({length: 300}, (_, index) => ['column' + index, index]))}]).rows;
  assert.equal(displayColumns(wide).columns.length, 50); assert.equal(displayColumns(wide).truncated, true);
  assert.equal(inferDisplaySchema(wide).fields.length, 200); assert.equal(inferDisplaySchema(wide).truncated, true);
  assert.equal(inferDisplaySchema(rows.rows).sampled, 100);
  const deep: Record<string, any> = {}; let next = deep;
  for (let index = 0; index < 20; index++) {next.child = {}; next = next.child;}
  const structure = inferDisplaySchema(prepareDisplayData([deep]).rows);
  assert.equal(structure.truncated, true); assert.ok(structure.fields.every(value => value.path.split('.').length <= 6));
});

test('file payload getters are never read and prototype-shaped user columns do not change object prototypes', () => {
  let reads = 0;
  const file = {fileName: 'safe.txt', mimeType: 'text/plain', fileSize: 0};
  Object.defineProperty(file, 'data', {enumerable: true, get: () => {reads++; throw new Error('must not run');}});
  const item = JSON.parse('{"__proto__":{"value":"normal JSON"},"constructor":"user value","prototype":1}');
  item.__easyflowBinary = {data: file};
  Object.defineProperty(item, 'getter', {enumerable: true, get: () => {reads++; throw new Error('must not run');}});
  const display = prepareDisplayData([item]);
  assert.equal(reads, 0); assert.equal(Object.getPrototypeOf(display.rows[0].value), Object.prototype);
  assert.equal(display.rows[0].value.__proto__.value, 'normal JSON'); assert.equal(({} as any).value, undefined);
  assert.equal(display.files[0].fileName, 'safe.txt'); assert.ok(!JSON.stringify(display).includes('"data":'));
});

test('cycles, very long values and file lists remain bounded without mutating execution data', () => {
  const circular: Record<string, any> = {name: '원본'}; circular.self = circular;
  const safe = sanitizeDisplayData(circular); assert.equal(safe.truncated, true); assert.equal(safe.value.self, '[순환 구조]'); assert.strictEqual(circular.self, circular);
  const text = '가'.repeat(30000), long = sanitizeDisplayData({text}); assert.equal(long.truncated, true); assert.ok(long.value.text.length < 20100); assert.equal(text.length, 30000);
  const binary = Object.fromEntries(Array.from({length: 5001}, (_, index) => ['file' + index, {data: 'hidden', fileName: 'file.txt', mimeType: 'text/plain', fileSize: 0}]));
  const files = prepareDisplayData([{__easyflowBinary: binary}]); assert.equal(files.files.length, 5000); assert.equal(files.filesTruncated, true);
  assert.equal(displayCell(false), '거짓 (false)'); assert.equal(displayCell(''), '""'); assert.equal(displayCell(undefined), '—'); assert.equal(fileSizeLabel(1024 * 1024), '1MB');
});
