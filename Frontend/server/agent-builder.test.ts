import assert from 'node:assert/strict';
import test from 'node:test';
import {parseBuilderAsset} from './agent-builder';
import {writeXlsx} from './document-files';
const encoded=(fileName:string,content:string)=>({fileName,data:Buffer.from(content).toString('base64')});
test('builder reuses CSV parser and identifies numeric strings, empty cells and duplicates',async()=>{const {asset,rows}=await parseBuilderAsset(encoded('stock.csv','code,stock\nA,20\nB,3\nB,\n'));assert.equal(rows.length,3);assert.deepEqual(asset.columns,['code','stock']);assert.equal(asset.profile.stock.numericStrings,2);assert.equal(asset.profile.stock.missing,1);assert.equal(asset.profile.code.duplicateValues,1);assert.equal(asset.kind,'table');});
test('builder reuses isolated XLSX reader without losing typed values',async()=>{const bytes=await writeXlsx([{code:'A',stock:20},{code:'B',stock:3}]);const {asset,rows}=await parseBuilderAsset({fileName:'stock.xlsx',data:Buffer.from(bytes).toString('base64')});assert.equal(asset.format,'xlsx');assert.equal(rows[0].stock,20);});
test('builder accepts JSON rows and rule documents with original text',async()=>{const table=await parseBuilderAsset(encoded('data.json','[{"name":"A","stock":null}]'));assert.equal(table.asset.profile.stock.missing,1);const doc=await parseBuilderAsset(encoded('policy.txt','빈 재고는 제외합니다.'));assert.equal(doc.asset.text,'빈 재고는 제외합니다.');assert.equal(doc.asset.kind,'document');});
test('builder rejects unsupported, malformed and unsafe file contents',async()=>{for(const input of [encoded('file.exe','binary'),encoded('file.json','{"rows":[]}'),encoded('file.json','[{"x":{"nested":1}}]'),encoded('../file.csv','a\n1'),{fileName:'file.csv',data:'garbage!'},encoded('file.csv','x,x\n1,2')])await assert.rejects(()=>parseBuilderAsset(input));});

test('large document base64 validation avoids recursive regular-expression stack overflow',async()=>{const {asset}=await parseBuilderAsset(encoded('large.txt','검증 원문 '.repeat(20000)));assert.ok(asset.text.length>100000);});
