import assert from 'node:assert/strict';
import {existsSync,readFileSync,readdirSync} from 'node:fs';
import {resolve} from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));

test('every product brand reference resolves and the retired mark is absent',()=>{
 const files=['index.html',...readdirSync(resolve(root,'app'),{recursive:true}).filter(path=>/\.(tsx|css)$/.test(String(path))).map(path=>'app/'+path)];
 const references=new Set<string>();
 for(const file of files){
  const source=readFileSync(resolve(root,file),'utf8');
  assert.ok(!source.includes('/brand/a4b-mark.svg'),file+' still references the retired mark');
  for(const match of source.matchAll(/\/brand\/[\w.-]+\.(?:svg|png)/g)){
   references.add(match[0]);
   assert.ok(existsSync(resolve(root,'public',match[0].slice(1))),file+' references missing '+match[0]);
  }
 }
 assert.ok(references.has('/brand/a4b-logo.png'));
 assert.ok(references.has('/brand/a4b-favicon.svg'));
 const symbol=readFileSync(resolve(root,'public/brand/a4b-flow-mark.svg'),'utf8');
 const favicon=readFileSync(resolve(root,'public/brand/a4b-favicon.svg'),'utf8');
 assert.equal(symbol.match(/<path[^>]+>/)?.[0],favicon.match(/<path[^>]+>/)?.[0]);
});
