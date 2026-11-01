import type {Json} from './workflow';

export type NodeReferenceContext = {nodeType?: string; kind?: 'expressions' | 'code'};
type Token = {kind: 'name' | 'string' | 'punct'; value: string; start: number; end: number};

/** Decode a JavaScript string literal without evaluating code or reading globals. */
function quoted(source: string, start: number): {end: number; value: string} | null {
  const quote = source[start];
  let value = '';
  for (let index = start + 1; index < source.length; index++) {
    const char = source[index];
    if (char === quote) return {end: index + 1, value};
    if (char === '\n' || char === '\r') return null;
    if (char !== '\\') {value += char; continue;}
    const next = source[++index];
    if (next === undefined) return null;
    if (next === '\n') continue;
    if (next === '\r') {if (source[index + 1] === '\n') index++; continue;}
    if (next === 'u') {
      const codePoint = /^\{([\da-fA-F]{1,6})\}/.exec(source.slice(index + 1));
      if (codePoint) {
        const number = parseInt(codePoint[1], 16);
        if (number > 0x10ffff) return null;
        value += String.fromCodePoint(number); index += codePoint[0].length; continue;
      }
      const hex = source.slice(index + 1, index + 5);
      if (!/^[\da-fA-F]{4}$/.test(hex)) return null;
      value += String.fromCharCode(parseInt(hex, 16)); index += 4; continue;
    }
    if (next === 'x') {
      const hex = source.slice(index + 1, index + 3);
      if (!/^[\da-fA-F]{2}$/.test(hex)) return null;
      value += String.fromCharCode(parseInt(hex, 16)); index += 2; continue;
    }
    if (/\d/.test(next)) {
      if (next !== '0' || /\d/.test(source[index + 1] || '')) return null;
      value += '\0'; continue;
    }
    value += ({n: '\n', r: '\r', t: '\t', b: '\b', f: '\f', v: '\v'} as Record<string, string>)[next] ?? next;
  }
  return null;
}

function commentEnd(source: string, index: number) {
  if (source.startsWith('//', index)) {const end = source.indexOf('\n', index + 2); return end < 0 ? source.length : end;}
  if (source.startsWith('/*', index)) {const end = source.indexOf('*/', index + 2); return end < 0 ? source.length : end + 2;}
  return index;
}

function expressionEnd(source: string, start: number): number | null {
  let braces = 0;
  for (let index = start; index < source.length; index++) {
    if (source[index] === '"' || source[index] === "'" || source[index] === '`') {
      const literal = quoted(source, index);
      if (!literal) return null;
      index = literal.end - 1; continue;
    }
    const comment = commentEnd(source, index);
    if (comment !== index) {index = comment - 1; continue;}
    if (source.startsWith('}}', index) && braces === 0) return index;
    if (source[index] === '{') braces++;
    else if (source[index] === '}') {if (!braces) return null; braces--;}
  }
  return null;
}

function tokens(source: string): Token[] | null {
  const result: Token[] = [];
  for (let index = 0; index < source.length;) {
    if (/\s/.test(source[index])) {index++; continue;}
    const comment = commentEnd(source, index);
    if (comment !== index) {index = comment; continue;}
    if (source[index] === '"' || source[index] === "'" || source[index] === '`') {
      const literal = quoted(source, index);
      if (!literal) return null;
      result.push({kind: source[index] === '`' ? 'punct' : 'string', value: literal.value, start: index, end: literal.end}); index = literal.end; continue;
    }
    const identifier = /^[\p{L}_$][\p{L}\p{N}_$]*/u.exec(source.slice(index));
    if (identifier) {result.push({kind: 'name', value: identifier[0], start: index, end: index + identifier[0].length}); index += identifier[0].length; continue;}
    result.push({kind: 'punct', value: source[index], start: index, end: index + 1}); index++;
  }
  return result;
}

function rewriteExpression(source: string, oldName: string, newName: string) {
  const lexed = tokens(source);
  if (!lexed) return source;
  const replacements: {start: number; end: number}[] = [];
  for (let index = 0; index < lexed.length; index++) {
    const root = lexed[index], open = lexed[index + 1], name = lexed[index + 2], close = lexed[index + 3];
    if (root.kind !== 'name' || !open || !name || !close || name.kind !== 'string' || name.value !== oldName || lexed[index - 1]?.value === '.') continue;
    if (root.value === '$' && open.value === '(' && close.value === ')' || root.value === '$items' && open.value === '(' && [',', ')'].includes(close.value) || root.value === '$node' && open.value === '[' && close.value === ']') replacements.push({start: name.start, end: name.end});
  }
  const literal = JSON.stringify(newName).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
  for (const replacement of replacements.reverse()) source = source.slice(0, replacement.start) + literal + source.slice(replacement.end);
  return source;
}

function rewriteString(source: string, oldName: string, newName: string) {
  let offset = 0, result = '';
  while (offset < source.length) {
    const start = source.indexOf('{{', offset);
    if (start < 0) return result + source.slice(offset);
    const end = expressionEnd(source, start + 2);
    if (end === null) return result + source.slice(offset);
    result += source.slice(offset, start + 2) + rewriteExpression(source.slice(start + 2, end), oldName, newName) + '}}';
    offset = end + 2;
  }
  return result;
}

/** Only template expressions are rewritten. Free JavaScript stays byte-for-byte intact. */
export function renameNodeReferences(parameters: Json, oldName: string, newName: string, context: NodeReferenceContext = {}): Json {
  if (!oldName || oldName === newName || context.kind === 'code' || ['n8n-nodes-base.code', 'n8n-nodes-base.function', 'n8n-nodes-base.functionItem'].includes(context.nodeType || '')) return parameters;
  const seen = new WeakMap<object, any>();
  const walk = (value: any): any => {
    if (typeof value === 'string') return rewriteString(value, oldName, newName);
    if (value === null || typeof value !== 'object') return value;
    if (seen.has(value)) return seen.get(value);
    const result: any = Array.isArray(value) ? [] : {};
    seen.set(value, result);
    for (const key of Object.keys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !('value' in descriptor)) continue;
      Object.defineProperty(result, key, {value: walk(descriptor.value), enumerable: true, configurable: true, writable: true});
    }
    return result;
  };
  return walk(parameters);
}
