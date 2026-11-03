import {createServer,type IncomingMessage,type ServerResponse} from 'node:http';
import {existsSync,readdirSync} from 'node:fs';
import {readFile,stat} from 'node:fs/promises';
import {resolve,sep,extname} from 'node:path';
import {authConfig,type AuthConfig} from './auth-config';
import {PostgresAuthUsers,SqliteAuthUsers,type AuthUsers} from './auth-users';
import {CognitoAuth,AuthError,authJson} from './cognito-auth';
import {baseDataDirectory,frontendPath,withDataDirectory} from './paths';
import {createWorkspaceRuntime} from './workspace-runtime';
import {validateWorkspaceFiles} from './workspace-files';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Runtime=ReturnType<typeof createWorkspaceRuntime>;
/** Workspaces are chosen only from the authenticated RDS member ID, never request bodies. */
export class WorkspaceRegistry {
 private entries=new Map<string,{directory:string;runtime:Runtime}>();
 constructor(private port:number,private privateMode:boolean,private publicOrigin?:string){}
 get(ownerId?:string,create=true){
  if(this.privateMode&&(!ownerId||!uuid.test(ownerId)))throw new AuthError(404,'작업 공간을 찾을 수 없습니다.');
  const key=ownerId||'local',directory=ownerId?baseDataDirectory('users',ownerId):baseDataDirectory();
  let entry=this.entries.get(key);
  if(!entry){
   if(!create&&!existsSync(directory))throw new AuthError(404,'작업 공간을 찾을 수 없습니다.');
   entry={directory,runtime:withDataDirectory(directory,()=>createWorkspaceRuntime(this.port,ownerId,this.publicOrigin))};this.entries.set(key,entry);
  }
  return {handle:(req:IncomingMessage,res:ServerResponse)=>withDataDirectory(entry.directory,()=>entry.runtime.handle(req,res))};
 }
 restore(){
  if(!this.privateMode){this.get();return;}
  const root=baseDataDirectory('users');if(!existsSync(root))return;
  for(const entry of readdirSync(root,{withFileTypes:true}))if(entry.isDirectory()&&uuid.test(entry.name))this.get(entry.name,false);
 }
 async close(){await Promise.all([...this.entries.values()].map(entry=>withDataDirectory(entry.directory,()=>entry.runtime.close())));this.entries.clear();}
}
async function frontend(req:IncomingMessage,res:ServerResponse,path:string){
 const root=resolve(process.env.A4B_FRONTEND_DIR||frontendPath('dist-app'));let file=resolve(root,'.'+decodeURIComponent(path));
 if(!file.startsWith(root+sep))file=resolve(root,'index.html');
 try{if(!(await stat(file)).isFile())file=resolve(root,'index.html');}catch{file=resolve(root,'index.html');}
 const content=await readFile(file),mime:Record<string,string>={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
 res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'});res.end(req.method==='HEAD'?undefined:content);
}
export function createApplication(options:{port:number;config?:AuthConfig;auth?:CognitoAuth;users?:AuthUsers;restore?:boolean}){
 const config=options.config||options.auth?.config,users=options.users||options.auth?.users||(config?(config.userStore==='postgres'?new PostgresAuthUsers(config):new SqliteAuthUsers(baseDataDirectory())):undefined);
 const auth=options.auth||(config&&users?new CognitoAuth(config,users):undefined),registry=new WorkspaceRegistry(options.port,!!auth,config?.origin);
 validateWorkspaceFiles(!!auth);
 if(options.restore!==false)registry.restore();
 const server=createServer(async(req,res)=>{
  try{
   const authority=req.headers.host||'',origin=config?.origin||`http://127.0.0.1:${options.port}`,url=new URL(req.url||'/',origin),path=url.pathname,method=req.method||'GET';
   // ALB probes use the target IP as Host. Only this minimal, read-only endpoint bypasses it.
   if(auth&&path==='/api/health'&&(method==='GET'||method==='HEAD')){authJson(res,200,{ok:true,standalone:true,auth:'cognito'});return;}
   const allowedHosts=config?new Set([new URL(config.origin).host]):new Set([`127.0.0.1:${options.port}`,`localhost:${options.port}`,`[::1]:${options.port}`]);
   if(!allowedHosts.has(authority))throw new AuthError(403,config?'등록된 서비스 주소로 접속하세요.':'로컬 프로그램에서만 관리할 수 있어요.');
   const sensitive=path.startsWith('/api/')||path.startsWith('/auth/');
   if(sensitive){
    const requestOrigin=req.headers.origin;
    if(requestOrigin&&(config?requestOrigin!==config.origin:!allowedHosts.has(new URL(requestOrigin).host)))throw new AuthError(403,'이 프로그램에서 요청해주세요.');
    if(auth&&!['GET','HEAD','OPTIONS'].includes(method)&&requestOrigin!==config?.origin)throw new AuthError(403,'요청 출처를 확인할 수 없습니다. 페이지를 새로 열고 시도하세요.');
   }
   if(path==='/api/auth/me'&&!auth){authJson(res,200,{enabled:false,authenticated:false,user:null});return;}
   if(auth&&await auth.handle(req,res,url))return;
   if(path.startsWith('/auth/'))throw new AuthError(503,'로그인 서비스가 설정되지 않았습니다.');
   if(path.startsWith('/api/')){
    const user=auth?await auth.currentUser(req):undefined;
    if(auth&&!user)throw new AuthError(401,'서비스를 이용하려면 로그인하세요.');
    await registry.get(user?.id).handle(req,res);return;
   }
   if(path.startsWith('/hooks/')){
    const owner=auth?/^\/hooks\/([0-9a-f-]{36})\//i.exec(path)?.[1]:undefined;
    if(auth&&!owner)throw new AuthError(404,'Webhook 요청 주소를 확인해주세요.');
    if(auth&&owner){
     let user;try{user=await users!.get(owner);}catch{throw new AuthError(503,'작업 공간을 확인할 수 없습니다.');}
     if(!user||user.status!=='active')throw new AuthError(404,'Webhook 요청 주소를 확인해주세요.');
    }
    await registry.get(owner,!auth).handle(req,res);return;
   }
   if(method!=='GET'&&method!=='HEAD')throw new AuthError(405,'요청 방식을 확인해주세요.');
   await frontend(req,res,path);
  }catch(error){
   if(res.writableEnded||res.destroyed)return;
   if(error instanceof AuthError)authJson(res,error.status,{error:error.message});
   else authJson(res,500,{error:'요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.'});
  }
 });
 return {server,registry,async close(){auth?.close();await registry.close();await users?.close();}};
}
export {authConfig};
