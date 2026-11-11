import assert from 'node:assert/strict';
import {cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {build} from 'esbuild';

test('backend bundles with only the files copied into its Docker build stages', async()=>{
 const root=fileURLToPath(new URL('../../../',import.meta.url));
 const dockerfile=readFileSync(join(root,'infrastructure/docker/Dockerfile'),'utf8');
 const workspace=mkdtempSync(join(tmpdir(),'a4b-docker-backend-'));
 try {
  let stage='';
  for(const line of dockerfile.split(/\r?\n/)){
   const from=line.match(/^FROM .* AS (\S+)$/i);
   if(from){stage=from[1];continue;}
   if(!['build','build-backend'].includes(stage) || !line.startsWith('COPY '))continue;
   const parts=line.slice(5).trim().split(/\s+/);
   const destination=parts.pop()!;
   for(const source of parts){
    const target=resolve(workspace,destination, ...(destination.endsWith('/')&&!source.endsWith('/')?[source.split('/').pop()!]:[]));
    mkdirSync(dirname(target),{recursive:true});
    cpSync(join(root,source),target,{recursive:true});
   }
  }
  const result=await build({absWorkingDir:workspace,entryPoints:['Backend/api/src/launch.ts'],
   bundle:true,platform:'node',format:'esm',packages:'external',write:false,logLevel:'silent'});
  assert.equal(result.errors.length,0);
  assert.equal(result.outputFiles.length,1);
 } finally {rmSync(workspace,{recursive:true,force:true});}
});
