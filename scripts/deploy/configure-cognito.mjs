import {spawnSync} from 'node:child_process';
import {writeFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const options=new Map();
for(let i=2;i<process.argv.length;i+=2){if(!process.argv[i].startsWith('--')||!process.argv[i+1])throw new Error('옵션은 --이름 값 형식으로 입력하세요.');options.set(process.argv[i].slice(2),process.argv[i+1]);}
const profile=options.get('profile')||'skn33',region=options.get('region')||'ap-northeast-2';
const expected=options.get('expected-account'),origin=options.get('origin'),stack=options.get('stack')||'a4b-cognito';
if(!expected||!/^\d{12}$/.test(expected)||!origin)throw new Error('--expected-account AWS계정번호 --origin 서비스출처주소가 필요합니다.');
if(!/^(https:\/\/[A-Za-z0-9.-]+(?::\d+)?|http:\/\/(localhost|127\.0\.0\.1):\d+)$/.test(origin))throw new Error('origin은 경로와 끝의 /가 없는 HTTPS 주소여야 합니다. 로컬 개발만 HTTP를 허용합니다.');
if(!/^[A-Za-z][A-Za-z0-9-]{0,90}$/.test(stack))throw new Error('스택 이름 형식을 확인하세요.');
const run=(args,capture=true)=>{
 const result=spawnSync('aws',[...args,'--profile',profile,'--region',region,'--no-cli-pager'],{cwd:root,encoding:'utf8',...(capture?{stdio:['ignore','pipe','pipe']}:{stdio:'inherit'})});
 if(result.error)throw new Error('AWS CLI를 실행하지 못했습니다. 설치 상태를 확인하세요.');
 if(result.status!==0)throw new Error(capture?String(result.stderr).trim():'AWS 구성을 완료하지 못했습니다. 스택 이벤트를 확인하세요.');
 return capture?JSON.parse(result.stdout):undefined;
};
const identity=run(['sts','get-caller-identity','--output','json']);
if(identity.Account!==expected)throw new Error('현재 AWS 계정과 --expected-account가 다릅니다. AWS 리소스를 변경하지 않았습니다.');
const prefix=options.get('domain-prefix')||`a4b-${expected}-${region}`;
if(!/^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(prefix))throw new Error('Cognito 도메인 접두사 형식을 확인하세요.');
run(['cloudformation','deploy','--template-file',resolve(root,'infrastructure/cognito.template.json'),'--stack-name',stack,'--parameter-overrides',`PublicOrigin=${origin}`,`DomainPrefix=${prefix}`,'--no-fail-on-empty-changeset'],false);
const result=run(['cloudformation','describe-stacks','--stack-name',stack,'--output','json']);
const values=Object.fromEntries(result.Stacks[0].Outputs.map(value=>[value.OutputKey,value.OutputValue]));
const destination=resolve(root,'.env.cognito.local');
if(existsSync(destination))throw new Error('AWS 구성은 완료했습니다. 기존 .env.cognito.local을 보호하기 위해 덮어쓰지 않았습니다. 스택 Outputs에서 변경된 값을 확인하세요.');
const local=['localhost','127.0.0.1'].includes(new URL(origin).hostname);
writeFileSync(destination,`# Generated Cognito configuration. Keep this file out of Git.\nA4B_AUTH_MODE=cognito\nEASYFLOW_PUBLIC_URL=${origin}\nPORT=${new URL(origin).port||3030}\nA4B_DOCKER_PORT=${new URL(origin).port||3030}\nA4B_COGNITO_USER_POOL_ID=${values.UserPoolId}\nA4B_COGNITO_CLIENT_ID=${values.ClientId}\nA4B_COGNITO_DOMAIN=${values.Domain}\nA4B_AUTH_USER_STORE=${local?'sqlite':'postgres'}\n${local?'# SQLite members are for local smoke tests only. Switch to postgres for RDS.':'# Add A4B_DATABASE_URL and A4B_DATABASE_CA_FILE before starting.'}\n`,{mode:0o600,flag:'wx'});
console.log('Cognito 구성 완료. .env.cognito.local에 앱 설정을 저장했습니다. 기존 .env는 유지했습니다.');
