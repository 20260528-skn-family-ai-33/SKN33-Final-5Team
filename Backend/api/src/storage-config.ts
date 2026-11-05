import pg from 'pg';
import {readFile,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {baseDataDirectory} from './paths';
import {isLoopback,checkDatabaseUrl,checkDatabaseServerName} from './auth-config';
import {postgresTls} from './auth-users';

/** Business-data store: SQLite files per workspace locally, the a4b_builder schema in RDS. */
export type StorageConfig={kind:'sqlite'}|{kind:'postgres';databaseUrl:string;databaseCaFile?:string;databaseServerName?:string};
export function storageConfig(env:NodeJS.ProcessEnv=process.env):StorageConfig {
 const kind=env.A4B_STORAGE||'sqlite';
 if(env.A4B_BUSINESS_RECORDS && (env.A4B_BUSINESS_RECORDS!=='a4b' || kind!=='postgres' || env.A4B_AUTH_MODE!=='cognito' || (env.A4B_AUTH_USER_STORE||'postgres')!=='postgres'))
  throw new Error('기존 a4b 업무 표에 기록하려면 PostgreSQL 저장소와 Cognito 회원 DB가 필요합니다.');
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
export const REQUIRED_SCHEMA_VERSION=4;
/**
 * Connects as the app account before the server takes traffic: the schema must be migrated,
 * the account must not bypass row-level security, and it must be able to use the tables.
 * Without this an unreachable or unmigrated RDS would pass the health check.
 */
export async function checkBuilderDatabase(config:StorageConfig,database:pg.Pool|undefined=config.kind==='postgres'?builderDatabase(config):undefined,directory=baseDataDirectory(),sqliteStoppedAt?:string){
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
 if(!(version>=REQUIRED_SCHEMA_VERSION))throw new Error(`업무 데이터 DB 스키마가 최신이 아니거나 권한이 없습니다(필요 ${REQUIRED_SCHEMA_VERSION}). scripts/deploy/apply-builder-migrations.py와 a4b_app 권한을 확인하세요.`);
 const {rows:[access]}=await database.query(`SELECT bool_and(coalesce(c.relrowsecurity AND c.relforcerowsecurity
    AND has_table_privilege(current_user,c.oid,p.privilege), false)) AS ok
  FROM unnest(ARRAY['registered_tools','builder_sessions','builder_assets','agent_versions','agent_tests',
    'a4b.workflow_generation_requests','builder_run_leases','builder_data_sources','builder_schedules','builder_schedule_jobs','reply_review_decisions',
    'document_reviews','document_review_events','builder_tool_gap_jobs','reusable_nodes','builder_verifications',
    'preprocessing_batches','preprocessing_examples','agent_checkpoints','checkpoints','checkpoint_blobs','checkpoint_writes']) t(name)
  CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) p(privilege)
  LEFT JOIN pg_class c ON c.oid=to_regclass(CASE WHEN position('.' in t.name)>0 THEN t.name ELSE 'a4b_builder.'||t.name END)`);
 if(!access?.ok)throw new Error('업무 데이터 표의 행 수준 보안과 앱 계정의 SELECT·INSERT·UPDATE·DELETE 권한을 확인하세요.');
 if(process.env.A4B_BUSINESS_RECORDS==='a4b'){
  const {rows:[domain]}=await database.query(`SELECT bool_and(coalesce(has_table_privilege(current_user,
    to_regclass('a4b.'||t),p),false)) AS ok FROM (VALUES
    ('workflows','UPDATE'),('design_sessions','UPDATE'),('workflow_runs','UPDATE'),
    ('workflows','INSERT'),('design_sessions','INSERT'),('workflow_versions','INSERT'),
    ('workflow_runs','INSERT'),('node_runs','INSERT'),('outputs','INSERT'),
    ('documents','INSERT'),('document_versions','INSERT')) AS required(t,p)`);
  if(!domain?.ok || !(await database.query("SELECT 1 FROM a4b.document_types WHERE code='other' AND is_active")).rowCount)
   throw new Error('기존 a4b 업무 표 권한과 활성 문서 종류 other를 확인하세요.');
 }
 const receiptPath=join(directory,'.a4b-sqlite-import');
 const receipt=(await readFile(receiptPath,'utf8').catch((error:NodeJS.ErrnoException)=>{
  if(error.code==='ENOENT')return '';
  throw error;
 })).trim();
 const valid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(receipt);
 if(!valid||!(await database.query('SELECT 1 FROM a4b_builder.sqlite_imports WHERE id=$1',[receipt])).rowCount)
  throw new Error('이 데이터 볼륨과 DB의 SQLite 이전 완료 기록이 없습니다. scripts/deploy/migrate-sqlite-to-postgres.py를 실행하세요.');
 if(sqliteStoppedAt!==undefined){
  const stopped=Date.parse(sqliteStoppedAt);
  if(!Number.isFinite(stopped)||(await stat(receiptPath)).mtimeMs<=stopped)
   throw new Error('기존 SQLite 컨테이너를 마지막으로 정지한 후에 생성한 이전 기록이 필요합니다.');
 }
}
