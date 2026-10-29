import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
import {nodeSpecFromForm} from '../app/node-form';
const sf=ts.createSourceFile('nodes.tsx',readFileSync(new URL('../app/node-library.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const body=sf.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='NodeLibrary')?.body;assert.ok(body);
const script=ts.transpileModule(body.statements.filter(n=>ts.isFunctionDeclaration(n)&&['lock','clear','save','reloadNodes'].includes(n.name!.text)).map(n=>n.getText(sf)).join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
function harness(){
 const calls:any[]=[];const c:any={nodeSpecFromForm,saving:{current:false},editing:null,kind:'agent',name:'테스트 역할',purpose:'문의 분석 역할',instructions:'고객의 문의를 분석합니다.',reference:'',model:'',tool:'read_document',field:'',operator:'lt',value:'',group:'',aggregation:'count',sumField:'',busy:false,refreshNeeded:false,onSaved:async()=>{},onBusyChange:()=>{},request:async(path:string,body:any)=>{calls.push({path,body});return {node:{id:'new',version:1}};}};
 for(const key of ['Editing','Kind','Tool','Operator','Aggregation','SumField','Name','Purpose','Instructions','Reference','Model','Field','Value','Group','Error','Notice','Busy','RefreshNeeded']){const prop=key[0].toLowerCase()+key.slice(1);c['set'+key]=(v:any)=>c[prop]=v;}
 createContext(c);runInContext(script,c);return {c,calls,save:()=>c.save({preventDefault:()=>{}})};
}
test('node write success plus list failure is recoverable with read-only reload',async()=>{
 const h=harness();h.c.onSaved=async()=>{throw Error('offline');};await h.save();assert.equal(h.calls.length,1);assert.equal(h.c.refreshNeeded,true);assert.match(h.c.notice,/저장했습니다/);assert.match(h.c.error,/저장은 완료/);assert.equal(h.c.name,'');
 h.c.onSaved=async()=>{};await h.c.reloadNodes();assert.equal(h.calls.length,1);assert.equal(h.c.refreshNeeded,false);assert.equal(h.c.error,'');
});
test('node write failure preserves edited values and releases save lock',async()=>{
 const h=harness();h.c.request=async()=>{throw Error('stale version');};await h.save();assert.equal(h.c.name,'테스트 역할');assert.equal(h.c.error,'stale version');assert.equal(h.c.saving.current,false);assert.equal(h.c.busy,false);
});
test('node duplicate submissions and reload are blocked during a pending save',async()=>{
 const h=harness();let release!:(v:any)=>void;const pending=new Promise(r=>release=r);let writes=0,reads=0;const busy:boolean[]=[];
 h.c.request=()=>{writes++;return pending;};h.c.onSaved=async()=>{reads++;};h.c.onBusyChange=(v:boolean)=>busy.push(v);
 const first=h.save();await h.save();await h.c.reloadNodes();assert.equal(writes,1);assert.equal(reads,0);release({node:{id:'new'}});await first;assert.equal(reads,1);assert.deepEqual(busy,[true,false]);
});
