import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
const source=ts.createSourceFile('workspace.tsx',readFileSync(new URL('../app/workspace.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=source.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='Home');assert.ok(component?.body);
const statements=component.body.statements;
const path=statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='executionsPath');assert.ok(path);
const load=statements.find(n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText(source)==='load'));assert.ok(load);
const poll=statements.find(n=>ts.isExpressionStatement(n)&&n.getText(source).includes('},2500)'));assert.ok(poll);
const active=statements.find(n=>ts.isExpressionStatement(n)&&n.getText(source).includes('}},750)'));assert.ok(active);
const compile=(value:string)=>ts.transpileModule(value,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
function deferred(){let resolve!:(value:any)=>void;const promise=new Promise<any>(done=>{resolve=done;});return {promise,resolve};}
function harness(){
 let callback!:()=>Promise<void>,cleanup!:()=>void;
 const updates:any[]=[],requests:{path:string;signal:AbortSignal}[]=[];
 let read=async(path:string):Promise<any>=>path.includes('/workflows')?{workflows:[{id:'A'}]}:path.includes('/credentials')?{credentials:[]}:{executions:[],execution:{id:'run',status:'running'}};
 const context:any={AbortController,encodeURIComponent,embedded:true,initialWorkflowId:'A',initialLoad:{current:true},flowRef:{current:{id:'A'}},loadRequest:{current:null},dirtyRef:{current:false},flow:{active:true},view:'editor',tab:'editor',execution:{id:'run',status:'running'},toast:{success:()=>{},error:()=>{}},useCallback:(f:any)=>f,useEffect:(f:any)=>{cleanup=f();},setInterval:(fn:any)=>{callback=fn;return 1;},clearInterval:()=>{},request:(path:string,_body:any,_method:any,signal:AbortSignal)=>{requests.push({path,signal});return read(path);}};
 for(const name of ['Loading','Workflows','Executions','Credentials','Flow','Dirty','StorageError','Execution','Running','Past','Future','Selected','NodeOpen'])context['set'+name]=(value:any)=>updates.push([name,value]);
 createContext(context);runInContext(compile(path!.getText(source)+'\n'+load!.getText(source)+'\nglobalThis.reload=load;'),context);
 return {context,requests,updates,read:(fn:typeof read)=>{read=fn;},startPoll:(detail=false)=>runInContext(compile((detail?active!:poll!).getText(source)),context),poll:()=>callback(),stop:()=>cleanup()};
}
test('embedded external editors scope history to the initial and then current workflow',async()=>{
 const h=harness();await h.context.reload();assert.ok(h.requests.some(r=>r.path==='/api/executions?workflowId=A'));
 h.context.flowRef.current.id='B';await h.context.reload();assert.equal(h.requests.filter(r=>r.path.includes('/executions')).at(-1)!.path,'/api/executions?workflowId=B');
});
test('embedded polling never overlaps or applies an earlier workflow history after switching',async()=>{
 const h=harness(),pending=deferred();h.read(()=>pending.promise);h.startPoll();const first=h.poll();await h.poll();assert.equal(h.requests.length,2);
 h.context.flowRef.current.id='B';pending.resolve({workflows:[{id:'A'}],executions:[{workflowId:'A'}]});await first;assert.equal(h.updates.length,0);
 h.stop();assert.ok(h.requests.every(r=>r.signal.aborted));
});
test('live execution detail polling shares one pending read and aborts on exit',async()=>{
 const h=harness(),pending=deferred();h.read(()=>pending.promise);h.startPoll(true);const first=h.poll();await h.poll();assert.equal(h.requests.length,1);
 h.stop();assert.equal(h.requests[0].signal.aborted,true);pending.resolve({execution:{id:'run',status:'success'}});await first;assert.equal(h.updates.length,0);
});
test('a superseded initial load ignores its eventual response',async()=>{
 const h=harness(),pending=deferred();h.read(()=>pending.promise);const first=h.context.reload();h.context.loadRequest.current.abort();pending.resolve({workflows:[],executions:[],credentials:[]});await first;
 assert.ok(!h.updates.some(v=>['Workflows','Executions','Flow'].includes(v[0])));
});


test('runtime clock refresh retains undo history, while external edits invalidate it',async()=>{
 const h=harness();
 h.context.flowRef.current={id:'A',revision:'one',updatedAt:'old'};
 h.read(async path=>path.includes('/workflows')?{workflows:[{id:'A',revision:'one',updatedAt:'tick'}]}:{executions:[]});
 h.startPoll();await h.poll();
 assert.equal(h.context.flowRef.current.updatedAt,'tick');
 assert.ok(!h.updates.some(v=>v[0]==='Past'||v[0]==='Future'));
 h.read(async path=>path.includes('/workflows')?{workflows:[{id:'A',revision:'two',nodes:[]}]}:{executions:[]});
 await h.poll();
 assert.equal(h.context.flowRef.current.revision,'two');
 assert.equal(h.updates.find(v=>v[0]==='Past')?.[1].length,0);
 assert.equal(h.updates.find(v=>v[0]==='Future')?.[1].length,0);
 h.stop();
});

test('a poll started before saving cannot restore the previous flow revision',async()=>{
 const h=harness(),pending=deferred();
 h.context.flowRef.current={id:'A',revision:'one'};
 h.read(()=>pending.promise);h.startPoll();
 const refresh=h.poll();
 h.context.flowRef.current={id:'A',revision:'two'};
 pending.resolve({workflows:[{id:'A',revision:'one'}],executions:[]});
 await refresh;
 assert.equal(h.context.flowRef.current.revision,'two');
 assert.ok(!h.updates.some(v=>v[0]==='Flow'));
 h.stop();
});
