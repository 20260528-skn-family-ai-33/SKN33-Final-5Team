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
 * Long model calls and runs may hold only about two thirds of the general slots (long is part of
 * general, so the budget above does not change); the rest keep schedules and screens moving.
 */
export function workerLimits(env:NodeJS.ProcessEnv=process.env){
 const general=limit(env,'A4B_WORKER_MAX_CONCURRENCY',12,64);
 return {general,long:Math.max(1,general-Math.max(1,Math.floor(general/3))),control:limit(env,'A4B_WORKER_CONTROL_CONCURRENCY',4,32),
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

// Must match REQUIRED_SCHEMA_VERSION in Backend/infrastructure/persistence/db.py.
export const REQUIRED_SCHEMA_VERSION=2;
/**
 * Connects as the app account before the server takes traffic: the schema must be migrated,
 * the account must not bypass row-level security, and it must be able to use the tables.
 * Without this an unreachable or unmigrated RDS would pass the health check.
 */
export async function checkBuilderDatabase(config:StorageConfig,database:pg.Pool|undefined=config.kind==='postgres'?builderDatabase(config):undefined){
 if(config.kind!=='postgres'||!database)return;
 let row:any;
 try{
  ({rows:[row]}=await database.query(`SELECT r.rolsuper OR r.rolbypassrls AS bypass,
   to_regclass('a4b_builder.schema_migrations') IS NOT NULL AS migrated,
   has_schema_privilege(current_user,'a4b_builder','USAGE') AS usage
   FROM pg_roles r WHERE r.rolname=current_user`));
 }catch{throw new Error('업무 데이터 DB에 접속하지 못했습니다. A4B_DATABASE_URL·CA 파일·네트워크를 확인하세요.');}
 if(!row||row.bypass)throw new Error('업무 데이터 DB 계정이 행 수준 보안을 우회할 수 있습니다. superuser나 BYPASSRLS가 아닌 앱 전용 계정으로 접속하세요.');
 const version=row.migrated&&row.usage?Number((await database.query('SELECT max(version) AS v FROM a4b_builder.schema_migrations')).rows[0]?.v):NaN;
 if(!(version>=REQUIRED_SCHEMA_VERSION))throw new Error(`업무 데이터 DB 스키마가 최신이 아니거나 권한이 없습니다(필요 ${REQUIRED_SCHEMA_VERSION}). scripts/apply-builder-migrations.py와 a4b_app 권한을 확인하세요.`);
 const {rows:[access]}=await database.query(`SELECT bool_and(has_table_privilege(current_user,'a4b_builder.'||t,'SELECT,INSERT,UPDATE,DELETE')) AS ok
  FROM unnest(ARRAY['builder_sessions','agent_tests','builder_schedule_jobs','agent_checkpoints','checkpoints','checkpoint_writes']) t`);
 if(!access?.ok)throw new Error('업무 데이터 DB 계정에 a4b_builder 표 읽기·쓰기 권한이 없습니다. a4b_app 역할을 부여하세요.');
}
