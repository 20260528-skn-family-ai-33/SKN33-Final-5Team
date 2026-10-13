import {spawnSync} from 'node:child_process';
import {agentPython,BACKEND_ROOT,backendPath} from '../server/paths';

const result=spawnSync(agentPython(),['-m','pytest',backendPath('agent_builder'),'-q',...process.argv.slice(2)],{cwd:BACKEND_ROOT,stdio:'inherit'});
if(result.error){console.error('Python 실행 환경을 시작하지 못했습니다. Backend/scripts/setup-agent-demo.sh를 먼저 실행하세요.');process.exit(1);}
process.exit(result.status??1);
