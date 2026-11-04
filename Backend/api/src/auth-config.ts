import {isIP} from 'node:net';

export type AuthConfig={
 mode:'cognito';origin:string;userPoolId:string;clientId:string;domain:string;
 userStore:'postgres'|'sqlite';databaseUrl?:string;databaseCaFile?:string;databaseServerName?:string;sessionSeconds:number;
};
export function isLoopback(host:string){return ['localhost','127.0.0.1','[::1]','::1'].includes(host);}
/** RDS address rules shared by the member store and the business-data store. */
export function checkDatabaseUrl(databaseUrl:string){
 const database=new URL(databaseUrl);
 if(!['postgres:','postgresql:'].includes(database.protocol)||[...database.searchParams.keys()].some(key=>/^ssl/i.test(key)))throw new Error('A4B_DATABASE_URL에는 PostgreSQL 주소를 넣고 SSL 옵션은 제외하세요. TLS 인증서 검증은 서버가 설정합니다.');
 return databaseUrl;
}
export function checkDatabaseServerName(value?:string){
 const databaseServerName=value?.trim();
 if(databaseServerName&&(!/^[a-z0-9.-]+$/i.test(databaseServerName)||isIP(databaseServerName)||!databaseServerName.includes('.')))throw new Error('A4B_DATABASE_TLS_SERVER_NAME에는 인증서에서 확인할 실제 DB 호스트 이름을 넣으세요.');
 return databaseServerName||undefined;
}
export function authConfig(env:NodeJS.ProcessEnv=process.env):AuthConfig|undefined {
 const mode=env.A4B_AUTH_MODE||'off';
 if(!['off','cognito'].includes(mode))throw new Error('A4B_AUTH_MODE는 off 또는 cognito로 설정하세요.');
 if(mode==='off'){
  if(env.EASYFLOW_PUBLIC_URL&&!isLoopback(new URL(env.EASYFLOW_PUBLIC_URL).hostname))throw new Error('공개 서비스 주소를 사용하려면 A4B_AUTH_MODE=cognito를 설정하세요.');
  return;
 }
 const required=(key:string)=>{const value=env[key]?.trim();if(!value)throw new Error(key+' 설정이 필요합니다.');return value;};
 const origin=new URL(required('EASYFLOW_PUBLIC_URL'));
 if(origin.username||origin.password||origin.search||origin.hash||origin.pathname!=='/'||origin.protocol!=='https:'&&!(origin.protocol==='http:'&&isLoopback(origin.hostname)))throw new Error('EASYFLOW_PUBLIC_URL은 HTTPS 출처 주소여야 합니다. HTTP는 로컬 개발에만 허용합니다.');
 const userPoolId=required('A4B_COGNITO_USER_POOL_ID');
 if(!/^[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+$/.test(userPoolId))throw new Error('Cognito 사용자 풀 ID 형식을 확인하세요.');
 const clientId=required('A4B_COGNITO_CLIENT_ID');if(!/^[a-z0-9]{1,128}$/.test(clientId))throw new Error('Cognito 앱 클라이언트 ID 형식을 확인하세요.');
 const domain=new URL(required('A4B_COGNITO_DOMAIN'));
 if(domain.protocol!=='https:'||domain.username||domain.password||domain.pathname!=='/'||domain.search||domain.hash||!domain.hostname.includes('.')||isIP(domain.hostname)||isLoopback(domain.hostname))throw new Error('A4B_COGNITO_DOMAIN은 Cognito 로그인 도메인의 HTTPS 출처 주소여야 합니다.');
 const userStore=env.A4B_AUTH_USER_STORE||'postgres';
 if(userStore!=='postgres'&&userStore!=='sqlite')throw new Error('A4B_AUTH_USER_STORE는 postgres 또는 sqlite로 설정하세요.');
 if(userStore==='sqlite'&&!isLoopback(origin.hostname))throw new Error('SQLite 회원 저장소는 로컬 개발에서만 사용할 수 있습니다.');
 const databaseUrl=userStore==='postgres'?checkDatabaseUrl(required('A4B_DATABASE_URL')):undefined;
 const sessionSeconds=Number(env.A4B_AUTH_SESSION_SECONDS||28800);
 if(!Number.isInteger(sessionSeconds)||sessionSeconds<300||sessionSeconds>86400)throw new Error('로그인 세션 시간은 300~86400초로 설정하세요.');
 const databaseServerName=checkDatabaseServerName(env.A4B_DATABASE_TLS_SERVER_NAME);
 return {mode:'cognito',origin:origin.origin,userPoolId,clientId,domain:domain.origin,userStore,databaseUrl,databaseCaFile:env.A4B_DATABASE_CA_FILE,databaseServerName,sessionSeconds};
}
