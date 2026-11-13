import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';

const frontendRoot=resolve(fileURLToPath(new URL('..',import.meta.url)));
const require=createRequire(new URL('../../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(frontendRoot,'app/run-answer.tsx')],absWorkingDir:frontendRoot,
 bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const module={exports:{} as any};new Function('require','module','exports',compiled)(require,module,module.exports);
const {RunAnswer,answerBlocks}=module.exports;
const {createElement}=require('react'),{renderToStaticMarkup}=require('react-dom/server');

test('plain answers keep their text while headings, lists and quotations become readable blocks',()=>{
 const text='# 실행 요약\n\n확인된 사실입니다.\n두 번째 줄입니다.\n\n- 첫 번째\n- 두 번째\n\n> 원문 근거';
 assert.deepEqual(answerBlocks(text).map((block:any)=>block.kind),['heading','paragraph','list','quote']);
 const html=renderToStaticMarkup(createElement(RunAnswer,{text}));
 assert.match(html,/<h3>실행 요약<\/h3>/);
 assert.match(html,/확인된 사실입니다\.<br\/>두 번째 줄입니다/);
 assert.match(html,/<ul><li>첫 번째<\/li><li>두 번째<\/li><\/ul>/);
 assert.match(html,/<blockquote>원문 근거<\/blockquote>/);
});

test('untrusted answer text is escaped and only HTTP links become clickable',()=>{
 const text='**완료** <script>alert(1)</script>\njavascript:alert(1)\nhttps://example.com/report?q=1.';
 const html=renderToStaticMarkup(createElement(RunAnswer,{text}));
 assert.match(html,/<strong>완료<\/strong>/);
 assert.match(html,/&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
 assert.doesNotMatch(html,/<script>/);
 assert.match(html,/href="https:\/\/example\.com\/report\?q=1"/);
 assert.doesNotMatch(html,/href="javascript:/);
 assert.match(html,/javascript:alert\(1\)/);
});

test('structured data and code remain inspectable without rendering raw HTML',()=>{
 const text='```json\n{"text":"<b>not html</b>"}\n```\n\n{"status":"ok","count":3}';
 const html=renderToStaticMarkup(createElement(RunAnswer,{text}));
 assert.equal((html.match(/<pre>/g)||[]).length,2);
 assert.match(html,/&lt;b&gt;not html&lt;\/b&gt;/);
 assert.match(html,/&quot;status&quot;: &quot;ok&quot;/);
 assert.doesNotMatch(html,/<b>not html<\/b>/);
});
