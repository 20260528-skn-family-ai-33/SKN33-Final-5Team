import type {Json} from './workflow';

export const DISPLAY_LIMITS = {rows: 5000, columns: 50, schemaRows: 100, schemaFields: 200, schemaDepth: 6, pageSize: 100, files: 5000} as const;
const binaryKey = '__easyflowBinary';
export type DisplayRow = {index: number; value: Json; searchText: string};
export type DisplayFile = {itemIndex: number; property: string; fileName: string; mimeType: string; fileSize: number};
export type DisplayData = {rows: DisplayRow[]; files: DisplayFile[]; truncated: boolean; filesTruncated: boolean; valuesTruncated: boolean};
export type SchemaField = {path: string; types: string[]; present: number; example: string};

function own(value: unknown, key: string) {
  if (!value || typeof value !== 'object') return undefined;
  const descriptor = Object.getOwnPropertyDescriptor(value, key);
  return descriptor && 'value' in descriptor ? descriptor.value : undefined;
}

/** Remove the reserved file container only. User JSON column names remain intact. */
export function sanitizeDisplayData(value: unknown): {value: any; truncated: boolean} {
  let count = 0, truncated = false;
  const active = new WeakSet<object>();
  const walk = (item: unknown, depth: number): any => {
    if (++count > 10000 || depth > 12) {truncated = true; return '[큰 구조 · 표시 범위 초과]';}
    if (item === null || typeof item === 'boolean') return item;
    if (typeof item === 'string') {if (item.length > 20000) {truncated = true; return item.slice(0, 20000) + '… [긴 값 · 표시 생략]';} return item;}
    if (typeof item === 'number') return Number.isFinite(item) ? item : '[유한하지 않은 숫자]';
    if (typeof item !== 'object') return '[JSON으로 표시할 수 없는 값]';
    if (active.has(item)) {truncated = true; return '[순환 구조]';}
    active.add(item);
    const result: any = Array.isArray(item) ? [] : {};
    for (const key of Object.keys(item)) {
      if (key === binaryKey) continue;
      if (count >= 10000) {truncated = true; break;}
      const descriptor = Object.getOwnPropertyDescriptor(item, key);
      if (!descriptor || !('value' in descriptor)) continue;
      Object.defineProperty(result, key, {value: walk(descriptor.value, depth + 1), enumerable: true, configurable: true, writable: true});
    }
    active.delete(item); return result;
  };
  return {value: walk(value, 0), truncated};
}
export function printableDisplayData(value: unknown) {return JSON.stringify(sanitizeDisplayData(value).value, null, 2);}

export function prepareDisplayData(items: Json[]): DisplayData {
  const result: DisplayData = {rows: [], files: [], truncated: items.length > DISPLAY_LIMITS.rows, filesTruncated: false, valuesTruncated: false};
  for (let index = 0; index < Math.min(items.length, DISPLAY_LIMITS.rows); index++) {
    const item = items[index], safe = sanitizeDisplayData(item), rowFiles: DisplayFile[] = [];
    result.valuesTruncated ||= safe.truncated;
    const binary = own(item, binaryKey);
    if (binary && typeof binary === 'object' && !Array.isArray(binary)) for (const property of Object.keys(binary)) {
      if (result.files.length >= DISPLAY_LIMITS.files) {result.filesTruncated = true; break;}
      const file = own(binary, property);
      if (!file || typeof file !== 'object' || Array.isArray(file)) continue;
      const name = own(file, 'fileName'), mime = own(file, 'mimeType'), size = own(file, 'fileSize');
      const metadata: DisplayFile = {itemIndex: index, property, fileName: typeof name === 'string' ? name.slice(0, 240) : '파일', mimeType: typeof mime === 'string' ? mime.slice(0, 200) : 'application/octet-stream', fileSize: typeof size === 'number' && Number.isFinite(size) && size >= 0 ? size : 0};
      result.files.push(metadata); rowFiles.push(metadata);
    }
    result.rows.push({index, value: safe.value, searchText: (JSON.stringify(safe.value) + ' ' + rowFiles.map(file => `${file.fileName} ${file.property} ${file.mimeType}`).join(' ')).toLocaleLowerCase('ko-KR')});
  }
  return result;
}
export function filterDisplayRows(rows: DisplayRow[], query: string) {
  const needle = query.trim().toLocaleLowerCase('ko-KR'); return needle ? rows.filter(row => row.searchText.includes(needle)) : rows;
}
export function paginateData<T>(values: T[], page: number, pageSize = 20) {
  const size = Number.isFinite(pageSize) ? Math.max(1, Math.min(DISPLAY_LIMITS.pageSize, Math.floor(pageSize))) : 20;
  const pages = Math.max(1, Math.ceil(values.length / size));
  const current = Number.isFinite(page) ? Math.max(0, Math.min(pages - 1, Math.floor(page))) : 0;
  return {values: values.slice(current * size, (current + 1) * size), page: current, pages, size, total: values.length, start: values.length ? current * size + 1 : 0, end: Math.min((current + 1) * size, values.length)};
}
export function displayColumns(rows: DisplayRow[]) {
  const columns = new Set<string>(); let truncated = false;
  for (const row of rows) for (const key of Object.keys(row.value)) {
    if (columns.has(key)) continue;
    if (columns.size >= DISPLAY_LIMITS.columns) {truncated = true; continue;}
    columns.add(key);
  }
  return {columns: [...columns], truncated};
}
export function displayValueType(value: unknown) {
  if (value === null) return '빈 값'; if (Array.isArray(value)) return '목록';
  return ({string: '글자', number: '숫자', boolean: '참/거짓', object: '객체', undefined: '없음'} as Record<string, string>)[typeof value] || '알 수 없음';
}
export function displayCell(value: unknown) {
  if (value === undefined) return '—'; if (value === null) return 'null';
  if (Array.isArray(value)) return `목록 · ${value.length}개 항목`;
  if (typeof value === 'object') return `객체 · ${Object.keys(value).length}개 필드`;
  if (typeof value === 'boolean') return value ? '참 (true)' : '거짓 (false)';
  const text = String(value); return text.length > 180 ? text.slice(0, 180) + '…' : text || '""';
}
export function inferDisplaySchema(rows: DisplayRow[]) {
  const fields = new Map<string, SchemaField>(), samples = rows.slice(0, DISPLAY_LIMITS.schemaRows);
  let truncated = rows.length > DISPLAY_LIMITS.schemaRows;
  for (const row of samples) {
    const present = new Set<string>();
    const visit = (value: unknown, path: string, depth: number) => {
      if (path) {
        if (!fields.has(path)) {if (fields.size >= DISPLAY_LIMITS.schemaFields) {truncated = true; return;} fields.set(path, {path, types: [], present: 0, example: displayCell(value)});}
        const field = fields.get(path)!, type = displayValueType(value);
        if (!field.types.includes(type)) field.types.push(type);
        if (!present.has(path)) {field.present++; present.add(path);}
      }
      if (!value || typeof value !== 'object') return;
      if (depth >= DISPLAY_LIMITS.schemaDepth) {if (Object.keys(value).length) truncated = true; return;}
      if (Array.isArray(value)) {if (value.length > 3) truncated = true; for (const item of value.slice(0, 3)) visit(item, path + '[]', depth + 1);}
      else for (const key of Object.keys(value)) {
        const next = /^[\p{L}_$][\p{L}\p{N}_$]*$/u.test(key) ? (path ? path + '.' : '') + key : path + '[' + JSON.stringify(key) + ']';
        visit(own(value, key), next, depth + 1);
      }
    };
    visit(row.value, '', 0);
  }
  return {fields: [...fields.values()], sampled: samples.length, truncated};
}
export function fileDownloadHref(file: DisplayFile, context: {executionId: string; runIndex: number; side?: 'input' | 'output'; output?: number}) {
  return '/api/files/download?' + new URLSearchParams({executionId: context.executionId, runIndex: String(context.runIndex), side: context.side || 'output', output: String(context.output ?? 0), item: String(file.itemIndex), property: file.property});
}
export function fileSizeLabel(bytes: number) {return bytes >= 1024 * 1024 ? Math.round(bytes / (1024 * 1024) * 10) / 10 + 'MB' : Math.round(bytes / 1024 * 10) / 10 + 'KB';}
