import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer as createHTTPServer,type Server} from 'node:http';
import {createServer as createViteServer,type ProxyOptions} from 'vite';
import config from '../vite.config';

async function listen(server:Server){
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 const address=server.address();assert.ok(address&&typeof address!=='string');
 return `http://127.0.0.1:${address.port}`;
}
async function close(server:Server){server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}

test('development API proxy accepts its own browser origin and preserves foreign origins for rejection',async()=>{
 const seen:{origin:string|undefined;body:string;method:string|undefined}[]=[];
 const backend=createHTTPServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;
  seen.push({origin:req.headers.origin,body,method:req.method});
  res.writeHead(req.headers.origin===`http://${req.headers.host}`?200:403,{'content-type':'application/json'});
  res.end(JSON.stringify({ok:true}));
 });
 const target=await listen(backend);
 let vite:Awaited<ReturnType<typeof createViteServer>>|undefined,frontend:Server|undefined;
 try{
  const proxy=config.server!.proxy!['/api'] as ProxyOptions;
  vite=await createViteServer({...config,configFile:false,logLevel:'silent',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,proxy:{'/api':{...proxy,target}}}});
  frontend=createHTTPServer(vite.middlewares);const origin=await listen(frontend);
  const send=(source:string)=>fetch(origin+'/api/agent-builder/workflow_folders',{method:'POST',headers:{origin:source,'content-type':'application/json'},body:JSON.stringify({operation:'create',name:'검증'})});
  assert.equal((await send(origin)).status,200);
  assert.deepEqual(seen[0],{origin:target,method:'POST',body:JSON.stringify({operation:'create',name:'검증'})});
  for(const foreign of ['https://foreign.example','http://127.0.0.1:1','http://localhost:1']){
   assert.equal((await send(foreign)).status,403);assert.equal(seen.at(-1)!.origin,foreign);
  }
 }finally{if(frontend)await close(frontend);await vite?.close();await close(backend);}
});
