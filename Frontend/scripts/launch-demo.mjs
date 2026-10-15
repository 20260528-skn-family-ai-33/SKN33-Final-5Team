// One-click demo start for macOS and Windows (npm run demo, 실행.command, 실행.cmd).
// Opens the browser if the demo is already running; otherwise installs what is missing, builds and starts it.
import {spawn,spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontend=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const windows=process.platform==='win32';
try{process.loadEnvFile(resolve(frontend,'.env'));}catch{}
const port=process.env.PORT||'3030';
const url=`http://127.0.0.1:${port}`;

function run(command,args){
 // npm is a .cmd file on Windows and needs a shell; the arguments here are fixed strings.
 const result=windows&&command==='npm'?spawnSync([command,...args].join(' '),{cwd:frontend,stdio:'inherit',shell:true}):spawnSync(command,args,{cwd:frontend,stdio:'inherit'});
 if(result.error||result.status!==0)throw new Error(`${[command,...args].join(' ')} 실행에 실패했습니다.`);
}

function openBrowser(){
 if(process.env.A4B_NO_BROWSER==='1')return;
 const [command,args]=windows?['cmd',['/c','start','',url]]:process.platform==='darwin'?['open',[url]]:['xdg-open',[url]];
 spawn(command,args,{stdio:'ignore',detached:true}).unref();
}

// /api/health answers at once. The agent catalog goes through Python and takes several seconds on Windows.
async function running(){
 try{const response=await fetch(url+'/api/health',{signal:AbortSignal.timeout(3000)});return response.ok&&(await response.json()).standalone===true;}
 catch{return false;}
}

try{
 if(await running()){openBrowser();process.exit(0);}
 if(!existsSync(resolve(frontend,'node_modules')))run('npm',['ci']);
 run(process.execPath,[resolve(frontend,'scripts','setup-python.mjs'),'--if-missing']);
 run('npm',['run','build']);
 console.log('A4B 에이전트 생성 데모를 시작합니다. 종료하려면 Ctrl+C를 누르세요.');
 const server=spawn(process.execPath,['build-server/index.mjs'],{cwd:frontend,stdio:'inherit',env:{...process.env,PORT:port,A4B_FOCUSED_DEMO:'1'}});
 setTimeout(openBrowser,2000).unref();
 server.on('exit',code=>process.exit(code??0));
 // Ctrl+C reaches the server directly (same console or process group); wait for its own clean shutdown.
 process.on('SIGINT',()=>{});
 process.on('SIGTERM',()=>server.kill('SIGTERM'));
}catch(error){
 console.error(`\n${error.message}`);
 process.exit(1);
}
