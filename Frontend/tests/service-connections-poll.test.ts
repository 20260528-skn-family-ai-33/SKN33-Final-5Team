import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createContext,runInContext} from 'node:vm';
import ts from 'typescript';
const source=ts.createSourceFile('connections.tsx',readFileSync(new URL('../app/service-connections.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=source.statements.find((n):n is ts.FunctionDeclaration=>ts.isFunctionDeclaration(n)&&n.name?.text==='ServiceConnections');
assert.ok(component?.body);
const effect=component.body.statements.find(n=>ts.isExpressionStatement(n)&&n.getText(source).includes('pending.attempt.expiresAt'));
assert.ok(effect);
const code=ts.transpileModule(effect.getText(source),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
function harness(){
 let poll!:()=>Promise<void>,cleanup!:()=>void,resolve!:(value:any)=>void,now=1000;
 const calls:AbortSignal[]=[],updates:any[]=[];
 const waiting=new Promise(done=>{resolve=done;});
 const context:any={AbortController,Date:{now:()=>now},pending:{service:'slack',attempt:{expiresAt:5000},previous:'before'},changing:{current:true},statusRequest:{current:0},popup:{current:{close(){updates.push('close');}}},location:{origin:'http://localhost'},connected:(status:any)=>status.credentials[0],request:(_p:string,_b:any,_m:any,signal:AbortSignal)=>{calls.push(signal);return waiting;},load:()=>updates.push('load'),setPending:(v:any)=>updates.push(['pending',v]),setBusy:(v:any)=>updates.push(['busy',v]),setNotice:(v:any)=>updates.push(['notice',v]),useEffect:(fn:()=>()=>void)=>{cleanup=fn();},window:{setInterval:(fn:()=>Promise<void>)=>{poll=fn;return 1;},clearInterval:()=>{},addEventListener:()=>{},removeEventListener:()=>{}}};
 createContext(context);runInContext(code,context);
 return {poll:()=>poll(),stop:()=>cleanup(),resolve:()=>resolve({credentials:[{updatedAt:'after'}]}),expire:()=>{now=6000;},calls,updates};
}
test('slow OAuth status polls never overlap and success refreshes once',async()=>{
 const h=harness();try{
  const first=h.poll();await h.poll();await h.poll();assert.equal(h.calls.length,1);
  h.resolve();await first;await h.poll();assert.equal(h.calls.length,1);assert.equal(h.updates.filter(v=>v==='load').length,1);
 }finally{h.stop();}
});
test('expired OAuth attempts cancel an in-flight poll without accepting its late success',async()=>{
 const h=harness();try{
  const first=h.poll();h.expire();await h.poll();assert.equal(h.calls[0].aborted,true);
  h.resolve();await first;assert.ok(!h.updates.includes('load'));assert.ok(h.updates.some(v=>Array.isArray(v)&&v[0]==='notice'&&v[1].includes('만료')));
 }finally{h.stop();}
});
test('leaving service settings aborts pending polling and ignores a late response',async()=>{
 const h=harness(),first=h.poll();h.stop();assert.equal(h.calls[0].aborted,true);h.resolve();await first;assert.equal(h.updates.length,0);
});
