// Creates the Python environment for the agent worker on macOS, Linux and Windows.
//   npm run setup            Backend/.venv-agent  (requirements-agent-lock.txt)
//   npm run setup:docling    Backend/.venv-docling (optional PDF conversion)
//   --if-missing             skip when the environment already exists (used by the launcher)
// A specific interpreter can be chosen with A4B_SETUP_PYTHON=/path/to/python.
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const backend=resolve(dirname(fileURLToPath(import.meta.url)),'..','..','Backend');
const windows=process.platform==='win32';
const docling=process.argv.includes('--docling');
const ifMissing=process.argv.includes('--if-missing');
const venv=resolve(backend,docling?'.venv-docling':'.venv-agent');
const venvPython=resolve(venv,windows?'Scripts/python.exe':'bin/python');
const MIN=[3,11],MAX=[3,14];

function run(command,args,options={}){
 const result=spawnSync(command,args,{stdio:'inherit',...options});
 if(result.error||result.status!==0)throw new Error(`${[command,...args].join(' ')} 실행에 실패했습니다.`);
}

function version(command,args){
 const result=spawnSync(command,[...args,'-c','import sys;print(sys.version_info[0],sys.version_info[1])'],{encoding:'utf8'});
 if(result.error||result.status!==0)return null;
 const [major,minor]=result.stdout.trim().split(' ').map(Number);
 return Number.isFinite(major)&&Number.isFinite(minor)?[major,minor]:null;
}

const supported=v=>v&&(v[0]>MIN[0]||v[0]===MIN[0]&&v[1]>=MIN[1])&&(v[0]<MAX[0]||v[0]===MAX[0]&&v[1]<=MAX[1]);

function findPython(){
 if(process.env.A4B_SETUP_PYTHON)return [process.env.A4B_SETUP_PYTHON,[]];
 const candidates=windows
  ?[['py',['-3.12']],['py',['-3.13']],['py',['-3.11']],['py',['-3.14']],['python',[]]]
  :[['python3.12',[]],['python3.13',[]],['python3.11',[]],['python3.14',[]],['python3',[]]];
 for(const [command,args] of candidates){
  const found=version(command,args);
  if(supported(found)){console.log(`Python ${found.join('.')} 사용: ${[command,...args].join(' ')}`);return [command,args];}
 }
 throw new Error(`Python ${MIN.join('.')}~${MAX.join('.')}을 찾지 못했습니다. https://www.python.org/downloads/ 에서 3.12를 설치하거나 A4B_SETUP_PYTHON에 실행 파일 경로를 지정하세요.`);
}

try{
 if(existsSync(venvPython)&&supported(version(venvPython,[]))){
  if(ifMissing){console.log(`Python 환경이 이미 있습니다: ${venv}`);process.exit(0);}
 }else{
  const [command,args]=findPython();
  // A broken or unsupported environment is rebuilt instead of patched.
  run(command,[...args,'-m','venv',...(existsSync(venv)?['--clear']:[]),venv]);
 }
 run(venvPython,['-m','pip','install','--upgrade','pip']);
 if(docling)run(venvPython,['-m','pip','install','docling==2.133.0']);
 else run(venvPython,['-m','pip','install','-r',resolve(backend,'requirements-agent-lock.txt')]);
 console.log(`\n설치 완료: ${venv}\n다음 실행: npm --prefix Frontend run dev`);
}catch(error){
 console.error(`\n${error.message}`);
 process.exit(1);
}
