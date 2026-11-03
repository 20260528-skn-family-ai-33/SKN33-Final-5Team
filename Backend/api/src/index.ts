import {createApplication,authConfig} from './app-server';
import {waitForWorkers} from './worker-exit';
import {storageConfig,closeBuilderDatabase} from './storage-config';

const port=Number(process.env.PORT||3010),host=process.env.HOST||'127.0.0.1';
// Refuse to start with a misconfigured business-data store before serving anyone.
storageConfig();
const application=createApplication({port,config:authConfig()});
application.server.listen(port,host,()=>console.log(`A4B: ${process.env.EASYFLOW_PUBLIC_URL||`http://127.0.0.1:${port}`}`));
let closing=false;
function shutdown(){
 if(closing)return;closing=true;
 // Cancel executions before waiting for long-running HTTP responses to finish.
 const closed=application.close();
 application.server.close(()=>void closed.then(()=>waitForWorkers()).then(()=>closeBuilderDatabase().catch(()=>{})).then(exitCleanly));
}
process.on('SIGINT',shutdown);process.on('SIGTERM',shutdown);
process.on('message',message=>{if(message==='shutdown')shutdown();});
function exitCleanly(){
 if(process.platform!=='win32'){process.exit(0);return;}
 process.exitCode=0;if(process.connected)process.disconnect();
 setTimeout(()=>process.exit(0),2000).unref();
}
