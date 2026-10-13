import type {Json} from './workflow';

export type ValueSource = {
  kind: 'input' | 'node'; nodeId?: string; nodeName?: string;
  outputIndex: number; runIndex?: number; items: Json[];
  activationKey?: string;
  /** Historical causal sources can be linked even when first/all are outside the visible scope. */
  explicitSelection?: boolean;
  linkedItem?: {status: 'ready'; sample: Json; inputCount: number; multipleOrigins?: boolean} | {status: 'unavailable'; reason: string};
};
export type NodeValueSelection = 'linked' | 'first';
export type ValuePath = {
  id: string; sourceIndex: number; path: (string | number)[]; label: string;
  expression: string; preview: string; type: 'string' | 'number' | 'boolean' | 'null' | 'array' | 'object';
};
export type ValueGroup = {
  id: string; sourceIndex: number; label: string; count: number;
  status: 'ready' | 'empty' | 'invalid'; values: ValuePath[]; allExpression?: string;
  selection: 'input' | NodeValueSelection; linkedAvailable: boolean; linkedReason?: string;
  selectionUnavailable?: string;
};
export type ValuePaths = {groups: ValueGroup[]; paths: ValuePath[]; truncated: boolean; depthLimited: boolean; hiddenCount: number};

const blocked = new Set(['__proto__', 'prototype', 'constructor', 'caller', 'callee', 'arguments', '__easyflowBinary']);
const privateKey = /password|passwd|(?:^|[_-])pwd(?:$|[_-])|secret|token|api[_-]?key|authorization|cookie|private[_-]?key|access[_-]?key|credential/i;
const privateValue = /\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{15,}|github_pat_[A-Za-z0-9_]{15,}|AIza[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._~+/-]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/;

function stringLiteral(value: string) {
  return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}
function sourceValid(source: ValueSource) {
  return source.kind === 'input' || source.kind === 'node' && typeof source.nodeName === 'string' && source.nodeName.length > 0 && source.nodeName.length <= 200 && Number.isSafeInteger(source.outputIndex) && source.outputIndex >= 0 && source.outputIndex <= 1_000_000 && (source.runIndex === undefined || Number.isSafeInteger(source.runIndex) && source.runIndex >= 0 && source.runIndex <= 1_000_000);
}
function reference(source: ValueSource, method: 'linked' | 'first' | 'all') {
  if (!sourceValid(source)) throw new Error('값을 가져올 단계 이름과 출력 번호를 확인해주세요.');
  if (source.kind === 'input') return method === 'all' ? '$input.all()' : '$json';
  if (method === 'linked') {
    if (source.linkedItem?.status !== 'ready') throw new Error('각 항목의 원본 연결을 확인할 수 없어요. 첫 항목이나 전체 목록을 선택해주세요.');
    return `$(${stringLiteral(source.nodeName!)}).item.json`;
  }
  if (source.explicitSelection === false) throw new Error('이 처리 결과는 현재 범위의 첫 항목이나 전체 목록으로 가져올 수 없어요. 각 항목의 원본을 선택해주세요.');
  const args = source.outputIndex + (source.runIndex === undefined ? '' : ', ' + source.runIndex);
  return `$(${stringLiteral(source.nodeName!)}).${method}(${args})` + (method === 'first' ? '.json' : '');
}

export function valueExpression(source: ValueSource, path: (string | number)[], selection: NodeValueSelection = 'linked') {
  if (path.some(key => typeof key === 'string' ? blocked.has(key) || privateKey.test(key) || key.length > 512 : !Number.isSafeInteger(key) || key < 0)) throw new Error('이 경로는 값 선택에서 사용할 수 없어요.');
  const method = source.kind === 'node' && selection === 'linked' ? 'linked' : 'first';
  const expression = reference(source, method) + path.map(key => typeof key === 'number' ? `[${key}]` : `[${stringLiteral(key)}]`).join('');
  if (expression.length > 7900) throw new Error('값 경로가 너무 길어요. 필요한 데이터를 먼저 정리해주세요.');
  return `={{ ${expression} }}`;
}
export function allItemsExpression(source: ValueSource) {
  return `={{ ${reference(source, 'all')} }}`;
}

function valueType(value: unknown): ValuePath['type'] | null {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  if (['string', 'number', 'boolean'].includes(typeof value)) return typeof value as ValuePath['type'];
  return null;
}
function preview(value: unknown, type: ValuePath['type']) {
  if (type === 'array') return `목록 · ${(value as unknown[]).length}개 항목`;
  if (type === 'object') return `객체 · ${Object.keys(value as object).filter(key => !blocked.has(key) && !privateKey.test(key)).length}개 필드`;
  if (type === 'null') return 'null · 비어 있는 값';
  if (type === 'string') {
    const text = String(value);
    return text.length ? JSON.stringify(text.length > 100 ? text.slice(0, 100) + '…' : text) : '"" · 빈 글자';
  }
  return String(value);
}
function pathLabel(path: (string | number)[], selection: ValueGroup['selection']) {
  if (!path.length) return selection === 'linked' ? '각 항목의 원본 전체' : selection === 'input' ? '현재 항목 전체' : '첫 항목 전체';
  return path.map((key, index) => typeof key === 'number' ? `[${key}]` : /^[\p{L}_$][\p{L}\p{N}_$]*$/u.test(key) ? (index ? '.' : '') + key : `[${stringLiteral(key)}]`).join('');
}

/** Samples only the first record. No raw records or secret previews are returned. */
export function buildValuePaths(sources: ValueSource[], options: {maxPaths?: number; maxDepth?: number; nodeSelection?: NodeValueSelection} = {}): ValuePaths {
  const maximum = Math.max(1, Math.min(500, Math.floor(options.maxPaths ?? 500) || 500));
  const maxDepth = Math.max(1, Math.min(8, Math.floor(options.maxDepth ?? 8) || 8));
  const result: ValuePaths = {groups: [], paths: [], truncated: false, depthLimited: false, hiddenCount: 0};
  const active = new WeakSet<object>();
  for (let sourceIndex = 0; sourceIndex < sources.length; sourceIndex++) {
    if (result.paths.length >= maximum || sourceIndex >= 500) {result.truncated = true; break;}
    const source = sources[sourceIndex], valid = sourceValid(source);
    const label = source.kind === 'input' ? '현재 입력' : source.linkedItem?.status === 'ready' && source.linkedItem.multipleOrigins ? `${source.nodeName || '이름 없는 단계'} · 각 항목의 원본 · 여러 처리 회차/출력` : `${source.nodeName || '이름 없는 단계'} · 출력 ${source.outputIndex + 1}${source.runIndex === undefined ? '' : ' · ' + (source.runIndex + 1) + '번째 처리'}`;
    const linkedAvailable = source.kind === 'node' && source.linkedItem?.status === 'ready';
    const selection: ValueGroup['selection'] = source.kind === 'input' ? 'input' : options.nodeSelection === 'first' ? 'first' : 'linked';
    const group: ValueGroup = {id: 'source-' + sourceIndex, sourceIndex, label, count: source.items.length, status: !valid ? 'invalid' : !source.items.length ? 'empty' : 'ready', values: [], selection, linkedAvailable};
    if (source.kind === 'node' && !linkedAvailable) group.linkedReason = source.linkedItem?.status === 'unavailable' ? source.linkedItem.reason : '원본 연결 기록이 없어 각 항목의 원본을 확인할 수 없어요.';
    if (source.kind === 'node' && selection === 'linked' && !linkedAvailable) group.selectionUnavailable = `${group.linkedReason} 첫 항목으로 가져오려면 위에서 첫 항목을 선택해주세요.`;
    if (source.kind === 'node' && selection === 'first' && source.explicitSelection === false) group.selectionUnavailable = source.linkedItem?.status === 'ready' && source.linkedItem.multipleOrigins ? '여러 처리 회차나 출력에 연결된 원본이에요. 각 항목의 원본으로 선택해주세요.' : '이전 반복에서 연결된 결과예요. 각 항목의 원본으로 선택해주세요.';
    if (valid && source.items.length && source.explicitSelection !== false) group.allExpression = allItemsExpression(source);
    result.groups.push(group);
    if (group.status !== 'ready' || group.selectionUnavailable) continue;

    const visit = (value: unknown, path: (string | number)[]) => {
      if (result.paths.length >= maximum) {result.truncated = true; return;}
      const type = valueType(value);
      if (!type || type === 'number' && !Number.isFinite(value)) return;
      if (type === 'string' && privateValue.test(value as string)) {result.hiddenCount++; return;}
      let expression: string;
      try {expression = valueExpression(source, path, selection === 'first' ? 'first' : 'linked');} catch {result.depthLimited = true; return;}
      const row: ValuePath = {id: `value-${sourceIndex}-${group.values.length}`, sourceIndex, path, label: pathLabel(path, selection), expression, preview: preview(value, type), type};
      group.values.push(row); result.paths.push(row);
      if (type !== 'object' && type !== 'array') return;
      if (active.has(value as object)) {result.depthLimited = true; return;}
      const keys = Object.keys(value as object);
      if (path.length >= maxDepth) {if (keys.length) result.depthLimited = true; return;}
      active.add(value as object);
      for (const key of keys) {
        if (result.paths.length >= maximum) {result.truncated = true; break;}
        if (blocked.has(key) || privateKey.test(key) || key.length > 512) {result.hiddenCount++; continue;}
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (!descriptor || !('value' in descriptor)) {result.hiddenCount++; continue;}
        const part = type === 'array' && /^(0|[1-9]\d*)$/.test(key) ? Number(key) : key;
        visit(descriptor.value, [...path, part]);
      }
      active.delete(value as object);
    };
    visit(selection === 'linked' && source.linkedItem?.status === 'ready' ? source.linkedItem.sample : source.items[0], []);
  }
  return result;
}
