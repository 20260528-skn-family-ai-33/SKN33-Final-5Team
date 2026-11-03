import pg from 'pg';
import {isLoopback,checkDatabaseUrl,checkDatabaseServerName} from './auth-config';
import {postgresTls} from './auth-users';

/** Business-data store: SQLite files per workspace locally, the a4b_builder schema in RDS. */
export type StorageConfig={kind:'sqlite'}|{kind:'postgres';databaseUrl:string;databaseCaFile?:string;databaseServerName?:string};
export function storageConfig(env:NodeJS.ProcessEnv=process.env):StorageConfig {
 const kind=env.A4B_STORAGE||'sqlite';
 if(kind!=='sqlite'&&kind!=='postgres')throw new Error('A4B_STORAGE는 sqlite 또는 postgres로 설정하세요.');
 if(kind==='sqlite'){
  // Same rule as the SQLite member store: a public server keeps its data in RDS.
  if(env.EASYFLOW_PUBLIC_URL&&!isLoopback(new URL(env.EASYFLOW_PUBLIC_URL).hostname))throw new Error('SQLite 업무 데이터 저장소는 로컬 개발에서만 사용할 수 있습니다. 운영에서는 A4B_STORAGE=postgres를 설정하세요.');
  return {kind};
 }
 const databaseUrl=env.A4B_DATABASE_URL?.trim();
 if(!databaseUrl)throw new Error('A4B_DATABASE_URL 설정이 필요합니다.');
 return {kind,databaseUrl:checkDatabaseUrl(databaseUrl),databaseCaFile:env.A4B_DATABASE_CA_FILE,databaseServerName:checkDatabaseServerName(env.A4B_DATABASE_TLS_SERVER_NAME)};
}

function limit(env:NodeJS.ProcessEnv,key:string,fallback:number,max:number){
 const raw=env[key];if(raw===undefined||raw==='')return fallback;
 const value=Number(raw);
 if(!Number.isInteger(value)||value<1||value>max)throw new Error(`${key}는 1~${max} 사이의 정수로 설정하세요.`);
 return value;
}
/**
 * Python workers run one process per job and each holds up to two RDS connections, so the
 * process count is what must be bounded. Limits apply per server: with several servers on one
 * RDS, budget (sum of general+control over servers)×2 + member-store pools under max_connections.
 * Short ownership calls (heartbeat/interrupt) get their own slots so long runs cannot starve them.
 */
export function workerLimits(env:NodeJS.ProcessEnv=process.env){
 return {general:limit(env,'A4B_WORKER_MAX_CONCURRENCY',12,64),control:limit(env,'A4B_WORKER_CONTROL_CONCURRENCY',4,32),
  queueMs:limit(env,'A4B_WORKER_QUEUE_TIMEOUT_MS',60000,600000)};
}

let pool:pg.Pool|undefined;
/** One small pool per server process for Node-side business data (assistant checkpoints). */
export function builderDatabase(config:Extract<StorageConfig,{kind:'postgres'}>):pg.Pool {
 if(!pool){
  pool=new pg.Pool({connectionString:config.databaseUrl,max:3,connectionTimeoutMillis:5000,ssl:postgresTls(config)});
  // Pool errors must not print connection strings or terminate the app.
  pool.on('error',()=>{});
 }
 return pool;
}
export async function closeBuilderDatabase(){const current=pool;pool=undefined;await current?.end();}
